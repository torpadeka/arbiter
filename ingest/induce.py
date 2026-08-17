"""Schema induction: point Arbiter at a folder, get an ontology.

    python -m ingest.induce ./mydata

Everything the pipeline needed hand-written (per-source field maps, the
predicate vocabulary, cardinality, authority weights) is derived from the data
instead. Five stages:

  1. profile     walk the folder, detect formats, flatten sample records into
                 dotted paths with types and example values
  2. envelope    an LLM maps each source's paths onto the common document
                 envelope (doc_id, title, body, author, timestamps, acl)
  3. vocabulary  extract with an *open* vocabulary over a sample of free text,
                 then cluster the raw phrases into canonical predicates
  4. cardinality COUNT it, do not ask. For each (subject, predicate), how many
                 distinct objects? Consistently one means functional, so
                 competing values arbitrate; many means they coexist
  5. rules       map structured fields onto the induced predicates so tier A
                 covers the whole corpus without an LLM

Every LLM output is validated against the data before it is accepted: dotted
paths must actually resolve on sample records, predicates must exist in the
induced vocabulary, and types must respect the declared domain and range.
Anything that fails validation is dropped and reported rather than written into
the schema.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from graph.models import RawDoc, Schema, load_schema, slugify
from llm import LLM

BASE_SCHEMA = Path(__file__).resolve().parents[1] / "ontology" / "schema.yaml"
OUT_SCHEMA = Path(__file__).resolve().parents[1] / "ontology" / "generated.yaml"

# The graph model is fixed even though the vocabulary is not: these are the
# types a node can have. Left open, induction invents "Ticket" and "Deal", which
# then fail every domain and range check downstream.
ENTITY_TYPES = ["Person", "Team", "Project", "Account", "Artifact", "Decision", "Topic"]
MIN_SUBJECTS_TO_TRUST = 3  # below this, counting cardinality is noise

SAMPLE_RECORDS = 12       # records shown to the model per source
VOCAB_DOCS = 40           # documents sampled for open-vocabulary extraction
MIN_CLUSTER = 2           # raw phrases needed before a predicate is admitted
MIN_TEXT = 40             # a body shorter than this carries no extractable relation
TEXT_EXTS = {".md", ".txt", ".text"}


# --- 1. profiling -----------------------------------------------------------


@dataclass
class Source:
    tool: str
    path: Path
    section: str = ""              # for one-JSON-many-sections files
    records: list[dict] = field(default_factory=list)
    paths: dict[str, list] = field(default_factory=dict)   # dotted path -> examples

    @property
    def label(self) -> str:
        return f"{self.path.name}" + (f" [{self.section}]" if self.section else "")


def flatten(obj: Any, prefix: str = "", out: dict | None = None, depth: int = 0) -> dict:
    """Dotted paths to scalar (or first-of-list) values."""
    out = {} if out is None else out
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            flatten(value, f"{prefix}.{key}" if prefix else key, out, depth + 1)
    elif isinstance(obj, list):
        if obj:
            if isinstance(obj[0], (dict, list)):
                flatten(obj[0], prefix, out, depth + 1)
            else:
                out.setdefault(prefix, []).append(obj[:3])
    else:
        out.setdefault(prefix, []).append(obj)
    return out


# Sections that hold evaluation material rather than corpus. Ingesting a
# benchmark's own answer key would make every score meaningless.
EXCLUDED_SECTIONS = ("question", "answer", "ground_truth", "label", "eval", "qa")


def _records_from_json(data: Any, stem: str, path: Path) -> list[Source]:
    """A JSON file is either a list of records or sections of lists."""
    if isinstance(data, list):
        return [Source(tool=slugify(stem), path=path, records=[r for r in data if isinstance(r, dict)])]
    sources = []
    if isinstance(data, dict):
        for key, value in data.items():
            if any(marker in key.lower() for marker in EXCLUDED_SECTIONS):
                continue
            rows = [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []
            if len(rows) >= 3:  # a section worth treating as its own source
                sources.append(Source(tool=slugify(f"{stem}_{key}"), path=path, section=key, records=rows))
        if not sources and all(isinstance(v, dict) for v in data.values()) and len(data) >= 3:
            # dict keyed by id, e.g. HERB's employee.json
            sources.append(Source(tool=slugify(stem), path=path, records=list(data.values())))
    return sources


def profile(data_dir: Path, sample: int = SAMPLE_RECORDS) -> list[Source]:
    sources: list[Source] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        stem, suffix = path.stem, path.suffix.lower()
        try:
            if suffix in {".jsonl", ".ndjson"}:
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                sources.append(Source(tool=slugify(stem), path=path, records=[r for r in rows if isinstance(r, dict)]))
            elif suffix == ".json":
                sources.extend(_records_from_json(json.loads(path.read_text(encoding="utf-8")), stem, path))
            elif suffix == ".csv":
                with path.open(encoding="utf-8", newline="") as fh:
                    sources.append(Source(tool=slugify(stem), path=path, records=list(csv.DictReader(fh))))
            elif suffix in TEXT_EXTS:
                sources.append(Source(
                    tool=slugify(path.parent.name or stem), path=path,
                    records=[{"id": path.stem, "title": path.stem, "content": path.read_text(encoding="utf-8")[:20000]}],
                ))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"  ! skipped {path.name}: {type(exc).__name__}")

    for source in sources:
        merged: dict[str, list] = {}
        for record in source.records[:sample]:
            for key, values in flatten(record).items():
                merged.setdefault(key, [])
                merged[key].extend(v for v in values if v not in merged[key])
        source.paths = {k: v[:3] for k, v in merged.items()}
    return [s for s in sources if s.records and s.paths]


def describe(source: Source, sample: int = SAMPLE_RECORDS) -> str:
    lines = [f"source: {source.tool}", f"records: {len(source.records)}", "", "paths (dotted, with examples):"]
    for path, examples in list(source.paths.items())[:60]:
        rendered = "; ".join(str(e)[:70] for e in examples[:2])
        lines.append(f"  {path}: {rendered}")
    lines.append("")
    lines.append("one full record:")
    lines.append(json.dumps(source.records[0], ensure_ascii=False)[:1500])
    return "\n".join(lines)


# --- 2. envelope induction --------------------------------------------------

ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "artifact_kind": {"type": "string", "description": "message, email, ticket, doc, pr, meeting, deal, transcript"},
        "doc_id": {"type": "string", "description": "dotted path to a unique id"},
        "title": {"type": "string"},
        "body": {"type": "string", "description": "dotted path to the main free text, empty if none"},
        "author": {"type": "string", "description": "dotted path to who created it, empty if none"},
        "author_email": {"type": "string"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
        "acl": {"type": "array", "items": {"type": "string"}, "description": "paths whose values act as visibility groups"},
        "participants": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string", "description": "one line on what this source appears to be"},
    },
    "required": ["artifact_kind", "doc_id", "title", "body", "author", "author_email",
                 "created_at", "updated_at", "acl", "participants", "notes"],
    "additionalProperties": False,
}

ENVELOPE_PROMPT = """You map a data source onto a common document envelope.

Given the dotted paths of a source's records, choose which path fills each
envelope field. Rules:
1. Use ONLY paths that appear in the listing. Never invent one.
2. doc_id must be stable and unique per record.
3. body is the main human-written text. If the source has no prose, use "".
4. author is who wrote or owns the record; author_email only if a path holds an
   actual email address.
5. acl holds paths whose values act as visibility scopes (channel, space, repo,
   project key). Not people.
6. participants holds paths listing other people involved (recipients, attendees).
7. Leave a field as "" when nothing fits. A wrong mapping is worse than none."""


def induce_envelope(source: Source, llm: LLM) -> dict:
    payload = llm.structured(ENVELOPE_PROMPT, describe(source), ENVELOPE_SCHEMA, cache=True)
    return validate_envelope(payload, source)


def validate_envelope(envelope: dict, source: Source) -> dict:
    """Drop any mapping whose path does not resolve on the sample records."""
    known = set(source.paths)

    def ok(path: str) -> bool:
        return bool(path) and path in known

    cleaned = {
        "artifact_kind": envelope.get("artifact_kind") or "doc",
        "doc_id": envelope.get("doc_id") if ok(envelope.get("doc_id", "")) else "",
        "notes": envelope.get("notes", ""),
    }
    for key in ("title", "body", "author", "author_email", "created_at", "updated_at"):
        value = envelope.get(key, "")
        cleaned[key] = value if ok(value) else ""
    for key in ("acl", "participants"):
        cleaned[key] = [p for p in (envelope.get(key) or []) if ok(p)]

    if not cleaned["doc_id"]:
        # Fall back to any path that looks like an identifier and is unique.
        for candidate in known:
            tail = candidate.split(".")[-1].lower()
            if tail in {"id", "key", "identifier", "number", "ts", "message_id", "uuid"}:
                cleaned["doc_id"] = candidate
                break
    return cleaned


# --- 3. open-vocabulary extraction and clustering ---------------------------

OPEN_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "subject_type": {"type": "string", "enum": ENTITY_TYPES},
                    "relation": {"type": "string", "description": "1-3 words, verb-like, lowercase"},
                    "object": {"type": "string"},
                    "object_type": {"type": "string", "enum": ENTITY_TYPES},
                },
                "required": ["subject", "subject_type", "relation", "object", "object_type"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

OPEN_PROMPT = """Extract factual statements from this document as triples.

Use whatever relation wording the text itself suggests: this pass is discovering
a vocabulary, not conforming to one. Keep relations short and verb-like
("assigned to", "reports to", "blocks", "owns").

Types should be one of: Person, Team, Project, Account, Artifact, Decision, Topic.

Rules:
1. Only what the text asserts. Never infer or complete.
2. Keep surface forms exactly as written, including handles and abbreviations.
3. Questions, greetings and speculation are not claims.
4. Return an empty list rather than a low-quality guess."""

CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "predicates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "UPPER_SNAKE_CASE canonical name"},
                    "raw_forms": {"type": "array", "items": {"type": "string"}},
                    "domain": {"type": "array", "items": {"type": "string", "enum": ENTITY_TYPES}},
                    "range": {"type": "array", "items": {"type": "string", "enum": ENTITY_TYPES}},
                    "temporal": {"type": "boolean", "description": "can a later source override it"},
                    "description": {"type": "string"},
                },
                "required": ["name", "raw_forms", "domain", "range", "temporal", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["predicates"],
    "additionalProperties": False,
}

CLUSTER_PROMPT = """You are designing the canonical relation vocabulary for a corpus.

You get two inputs:
  A. relation phrases observed in free text, with the entity types they connect
  B. the structured fields each source carries, with example values

Both matter. Most high-value relations in enterprise data live in FIELDS, not
prose: an assignee field, a status field, a due date, a deal owner. A vocabulary
built only from prose will miss them, and then nothing can map the fields.

Rules:
1. Cover both inputs. Every field that clearly encodes a relation between two
   things deserves a predicate, even if no prose phrase matched it.
2. Merge synonyms aggressively. "assigned to", "assignee" and "is working on the
   ticket" are one predicate. Nine tools describe the same relation nine ways,
   and merging them is the entire point.
3. Do NOT merge relations that differ in direction. "owns" and "owned by" are
   one relation stated two ways: pick the direction where the subject has a
   single answer, so a ticket has one assignee rather than a person having many.
4. A field on a record describes THAT RECORD. An assignee field on a ticket
   means (ticket) ASSIGNED_TO (person), where the ticket is an Artifact. Do not
   redirect it at some other entity the record merely mentions: (person)
   ASSIGNED_TO (project) loses which ticket was actually assigned, and the
   project is not what the field was about.
4. Status, dates and priorities relate a thing to a value: use Topic as the range.
5. domain and range come from the observed types and the field examples.
6. temporal is true when a later, better source could overturn the statement
   (assignment, status, ownership), false for what stays true forever
   (authorship, mentions, references).
7. Prefer fewer, sharper predicates. Aim for 12 to 25 total.
8. Drop noise: vague relations seen once, and anything you cannot name crisply."""


def sample_free_text(docs: list[RawDoc], limit: int = VOCAB_DOCS) -> list[RawDoc]:
    """Longest bodies first: prose carries relations that fields do not."""
    with_text = [d for d in docs if len((d.body or "").strip()) >= MIN_TEXT]
    with_text.sort(key=lambda d: -len(d.body))
    spread: list[RawDoc] = []
    per_tool: Counter = Counter()
    for doc in with_text:
        if per_tool[doc.tool] < max(3, limit // max(1, len({d.tool for d in with_text}))):
            spread.append(doc)
            per_tool[doc.tool] += 1
        if len(spread) >= limit:
            break
    return spread


def extract_open(docs: list[RawDoc], llm: LLM, verbose: bool = True) -> list[dict]:
    raw: list[dict] = []
    for i, doc in enumerate(docs, 1):
        text = f"tool: {doc.tool}\nauthor: {doc.author_raw}\ntitle: {doc.title}\n\n{doc.body[:4000]}"
        try:
            payload = llm.structured(OPEN_PROMPT, text, OPEN_SCHEMA, cache=True)
        except Exception as exc:
            if verbose:
                print(f"  ! {doc.key}: {type(exc).__name__}")
            continue
        for row in payload.get("claims", []) or []:
            if isinstance(row, dict) and row.get("relation") and row.get("subject") and row.get("object"):
                raw.append(row)
        if verbose and i % 10 == 0:
            print(f"  {i}/{len(docs)} documents, {len(raw)} raw claims")
    return raw


def field_inventory(sources: list[Source], envelopes: dict[str, dict]) -> str:
    """Structured paths not already consumed by the document envelope.

    Assignment, status, due dates and ownership live here, so the vocabulary has
    to see them or the field-mapping stage has nothing to map onto.
    """
    lines = []
    for source in sources:
        envelope = envelopes.get(source.tool, {})
        consumed = {envelope.get(k) for k in ("doc_id", "title", "body", "author", "author_email",
                                              "created_at", "updated_at")}
        consumed |= set(envelope.get("acl") or []) | set(envelope.get("participants") or [])
        leftover = [(p, ex) for p, ex in source.paths.items() if p not in consumed]
        if not leftover:
            continue
        lines.append(f"  {source.tool} ({envelope.get('artifact_kind', 'doc')}):")
        for path, examples in leftover[:18]:
            rendered = "; ".join(str(e)[:50] for e in examples[:2])
            lines.append(f"    {path} = {rendered}")
    return "\n".join(lines) or "  (none)"


def cluster_predicates(raw: list[dict], fields: str, llm: LLM) -> list[dict]:
    observed: dict[str, Counter] = defaultdict(Counter)
    counts: Counter = Counter()
    for row in raw:
        relation = row["relation"].strip().lower()
        counts[relation] += 1
        observed[relation][f"{row.get('subject_type', '?')}->{row.get('object_type', '?')}"] += 1

    listing = "\n".join(
        f"  {relation} (x{n}): " + ", ".join(f"{types} x{c}" for types, c in observed[relation].most_common(3))
        for relation, n in counts.most_common(120)
    ) or "  (none)"
    prompt = f"A. relations observed in free text:\n{listing}\n\nB. structured fields per source:\n{fields}"
    payload = llm.structured(CLUSTER_PROMPT, prompt, CLUSTER_SCHEMA, cache=True)
    return [p for p in payload.get("predicates", []) if p.get("name") and p.get("domain") and p.get("range")]


# --- 4. cardinality by counting ---------------------------------------------


def cardinality_from_triples(triples: Iterable[tuple[str, str, str]]) -> dict[str, tuple[str, float]]:
    """Functional or not, decided by counting objects per subject.

    Runs over every tier-A claim in the corpus rather than a prose sample, so
    the verdict is a measurement. Getting this wrong is expensive in both
    directions: mark a multi-valued predicate functional and every second value
    looks like a contradiction, mark a functional one multi-valued and real
    contradictions are never detected.
    """
    grouped: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for subject, predicate, obj in triples:
        grouped[predicate][subject.strip().lower()].add(obj.strip().lower())

    out: dict[str, tuple[str, float]] = {}
    for predicate, subjects in grouped.items():
        if len(subjects) < MIN_SUBJECTS_TO_TRUST:
            continue  # too few subjects for the count to mean anything
        avg = sum(len(objects) for objects in subjects.values()) / len(subjects)
        multi = sum(1 for objects in subjects.values() if len(objects) > 1) / len(subjects)
        out[predicate] = ("one" if (avg < 1.35 and multi < 0.25) else "many", round(avg, 2))
    return out


def infer_cardinality(raw: list[dict], predicates: list[dict]) -> dict[str, str]:
    """Functional or not, decided by the data rather than by opinion.

    For every (subject, predicate) seen, count distinct objects. A predicate
    whose subjects almost always have exactly one object is functional, which
    is what makes competing values a contradiction worth arbitrating.
    """
    lookup = {form.strip().lower(): p["name"] for p in predicates for form in p["raw_forms"]}
    grouped: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for row in raw:
        name = lookup.get(row["relation"].strip().lower())
        if name:
            grouped[name][row["subject"].strip().lower()].add(row["object"].strip().lower())

    cardinality: dict[str, str] = {}
    for predicate in predicates:
        subjects = grouped.get(predicate["name"], {})
        if not subjects:
            cardinality[predicate["name"]] = "many" if not predicate.get("temporal") else "one"
            continue
        avg = sum(len(objects) for objects in subjects.values()) / len(subjects)
        multi = sum(1 for objects in subjects.values() if len(objects) > 1) / len(subjects)
        cardinality[predicate["name"]] = "one" if (avg < 1.35 and multi < 0.25) else "many"
        predicate["_objects_per_subject"] = round(avg, 2)
    return cardinality


# --- 5. tier A rules --------------------------------------------------------

RULES_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "dotted path, or __self__ for the document"},
                    "subject_type": {"type": "string", "enum": ENTITY_TYPES},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                    "object_type": {"type": "string", "enum": ENTITY_TYPES},
                    "asserted_at": {"type": "string"},
                },
                "required": ["subject", "subject_type", "predicate", "object", "object_type", "asserted_at"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rules"],
    "additionalProperties": False,
}

RULES_PROMPT = """Map a source's structured fields onto canonical predicates.

These rules run over every document with no LLM involved, so they must be
mechanical and certain. Use __self__ to mean the document itself.

Rules:
1. Use ONLY dotted paths from the listing, or __self__.
2. Use ONLY predicates from the vocabulary, and respect their domain and range.
3. Write functional predicates from the side that has one answer: a ticket has
   one assignee, so (ticket) ASSIGNED_TO (person), never the reverse.
   In practice this means __self__ is usually the subject, because the fields
   belong to the document.
4. Only map fields whose meaning is unambiguous. Skip anything you are guessing at.
5. asserted_at is a dotted path to when the fact became true, usually the
   updated timestamp. Use "" to default to the document date."""


def induce_rules(source: Source, envelope: dict, predicates: list[dict], llm: LLM) -> list[dict]:
    vocabulary = "\n".join(
        f"  {p['name']}: {'|'.join(p['domain'])} -> {'|'.join(p['range'])} [{p['cardinality']}] {p['description'][:80]}"
        for p in predicates
    )
    prompt = f"{describe(source)}\n\nvocabulary:\n{vocabulary}"
    payload = llm.structured(RULES_PROMPT, prompt, RULES_SCHEMA, cache=True)

    known_paths, by_name = set(source.paths), {p["name"]: p for p in predicates}
    rules = []
    for rule in payload.get("rules", []) or []:
        predicate = by_name.get(rule.get("predicate", ""))
        if not predicate:
            continue
        subject, obj = rule.get("subject", ""), rule.get("object", "")
        if subject != "__self__" and subject not in known_paths:
            continue
        if obj != "__self__" and obj not in known_paths:
            continue
        if rule.get("subject_type") not in predicate["domain"] or rule.get("object_type") not in predicate["range"]:
            continue
        cleaned = {
            "subject": subject, "subject_type": rule["subject_type"],
            "predicate": predicate["name"],
            "object": obj, "object_type": rule["object_type"],
        }
        if rule.get("asserted_at") in known_paths:
            cleaned["asserted_at"] = rule["asserted_at"]
        rules.append(cleaned)
    return rules


# --- authority --------------------------------------------------------------

KIND_AUTHORITY = {
    "ticket": 1.00, "deal": 0.95, "pr": 0.90, "doc": 0.80,
    "meeting": 0.70, "transcript": 0.65, "email": 0.60, "message": 0.50,
}


def estimate_authority(envelope: dict) -> float:
    """Structured records outrank prose, since a field is maintained and chat is not."""
    base = KIND_AUTHORITY.get(envelope.get("artifact_kind", "doc"), 0.6)
    # A source with timestamps that get updated is being maintained; trust it more.
    if envelope.get("updated_at"):
        base = min(1.0, base + 0.05)
    return round(base, 2)


# --- assembly ---------------------------------------------------------------


def build_schema(sources: list[Source], envelopes: dict[str, dict], predicates: list[dict],
                 rules: dict[str, list[dict]]) -> dict:
    base = yaml.safe_load(BASE_SCHEMA.read_text(encoding="utf-8"))
    generated = {
        "version": base["version"],
        "node_types": base["node_types"],
        "artifact_kinds": base["artifact_kinds"],
        "predicates": {
            p["name"]: {
                "domain": p["domain"], "range": p["range"],
                "temporal": bool(p["temporal"]), "cardinality": p["cardinality"],
                "description": p.get("description", "")[:160],
                "observed_objects_per_subject": p.get("_objects_per_subject"),
            }
            for p in predicates
        },
        "structural_edges": base["structural_edges"],
        "arbitration": {**base["arbitration"], "source_authority": {
            s.tool: estimate_authority(envelopes[s.tool]) for s in sources if s.tool in envelopes
        }},
        "retrieval": base["retrieval"],
        "sources": {},
    }
    for source in sources:
        envelope = envelopes.get(source.tool)
        if not envelope or not envelope.get("doc_id"):
            continue
        block = {"artifact_kind": envelope["artifact_kind"], "doc_id": envelope["doc_id"]}
        for key in ("title", "body", "author", "author_email", "created_at", "updated_at"):
            if envelope.get(key):
                block[key] = envelope[key]
        block["acl"] = envelope.get("acl", [])
        if envelope.get("participants"):
            block["participants"] = envelope["participants"]
        block["rules"] = rules.get(source.tool, [])
        generated["sources"][source.tool] = block
    return generated


def run(data_dir: Path, out: Path = OUT_SCHEMA, vocab_docs: int = VOCAB_DOCS, verbose: bool = True) -> dict:
    from ingest.parse import parse_record

    llm = LLM(model_env="EXTRACT_MODEL")
    if verbose:
        print(f"profiling {data_dir}")
    sources = profile(data_dir)
    if not sources:
        raise SystemExit(f"no readable records under {data_dir}")
    for source in sources:
        if verbose:
            print(f"  {source.label:<34} {len(source.records):>6} records, {len(source.paths)} paths")

    if verbose:
        print("\ninducing document envelopes")
    envelopes: dict[str, dict] = {}
    for source in sources:
        envelopes[source.tool] = induce_envelope(source, llm)
        if verbose:
            env = envelopes[source.tool]
            print(f"  {source.tool:<22} kind={env['artifact_kind']:<10} id={env['doc_id']} body={env['body'] or '-'}")

    # Build documents with the induced envelopes so the vocabulary pass reads
    # real prose rather than raw JSON.
    interim = Schema(**{
        **yaml.safe_load(BASE_SCHEMA.read_text(encoding="utf-8")),
        "sources": {s.tool: {**envelopes[s.tool], "rules": []} for s in sources if envelopes[s.tool].get("doc_id")},
    })
    docs: list[RawDoc] = []
    for source in sources:
        if not envelopes[source.tool].get("doc_id"):
            continue
        for record in source.records:
            try:
                docs.append(parse_record(source.tool, record, interim))
            except (ValueError, KeyError):
                continue

    sampled = sample_free_text(docs, vocab_docs)
    if verbose:
        print(f"\nextracting an open vocabulary from {len(sampled)} of {len(docs)} documents")
    raw = extract_open(sampled, llm, verbose)
    if not raw:
        raise SystemExit("no claims extracted; check the LLM configuration")

    if verbose:
        print(f"\nclustering {len({r['relation'].lower() for r in raw})} raw relations plus structured fields")
    predicates = cluster_predicates(raw, field_inventory(sources, envelopes), llm)
    cardinality = infer_cardinality(raw, predicates)
    for predicate in predicates:
        predicate["cardinality"] = cardinality.get(predicate["name"], "many")

    if verbose:
        print(f"\n{len(predicates)} predicates induced")
        for p in predicates:
            observed = p.get("_objects_per_subject")
            note = f"{observed} objects/subject" if observed else "unobserved"
            print(f"  {p['name']:<20} {'|'.join(p['domain'])[:22]:<22} -> {'|'.join(p['range'])[:18]:<18}"
                  f" {p['cardinality']:<5} {note}")

    if verbose:
        print("\nmapping structured fields onto the vocabulary")
    rules: dict[str, list[dict]] = {}
    for source in sources:
        if not envelopes[source.tool].get("doc_id"):
            continue
        rules[source.tool] = induce_rules(source, envelopes[source.tool], predicates, llm)
        if verbose:
            print(f"  {source.tool:<22} {len(rules[source.tool])} rules")

    schema = build_schema(sources, envelopes, predicates, rules)

    # Re-count cardinality over every tier-A claim the induced rules produce,
    # not just the prose sample the vocabulary came from. This is the whole
    # corpus voting on whether a predicate is functional.
    from ingest.parse import tier_a_claims

    measured = tier_a_claims(docs, Schema(**schema))
    counted = cardinality_from_triples(
        (c.subject_surface, c.predicate, c.object_surface) for c in measured
    )
    for name, spec in schema["predicates"].items():
        if name in counted:
            verdict, avg = counted[name]
            spec["cardinality"] = verdict
            spec["observed_objects_per_subject"] = avg
        # A statement that can never be overturned cannot have a "current"
        # value, so it is additive by definition. Authorship is the clear case:
        # a person authors many documents and none of them supersede another.
        if not spec.get("temporal"):
            spec["cardinality"] = "many"
    if verbose and counted:
        print(f"\ncardinality re-counted over {len(measured)} tier-A claims")
        for name, (verdict, avg) in sorted(counted.items()):
            print(f"  {name:<22} {verdict:<5} {avg} objects/subject")

    out.write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if verbose:
        print(f"\nwritten to {out}")
        print(f"  {len(schema['sources'])} sources, {len(schema['predicates'])} predicates, "
              f"{sum(len(b['rules']) for b in schema['sources'].values())} field rules")
    return schema


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    ap = argparse.ArgumentParser(description="Induce an ontology from a folder of data")
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--out", type=Path, default=OUT_SCHEMA)
    ap.add_argument("--vocab-docs", type=int, default=VOCAB_DOCS)
    args = ap.parse_args()
    run(args.data_dir, args.out, args.vocab_docs)
