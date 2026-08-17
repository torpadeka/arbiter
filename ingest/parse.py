"""Tier A: schema-driven parsing and deterministic claim extraction.

One generic parser, nine tools. Every tool-specific detail lives in
`ontology/schema.yaml` under `sources:` — this module only knows how to walk
dotted paths and apply rules. Adding a tenth tool is a YAML edit.

This is the ontology-alignment layer for structured fields: `jira.fields.
assignee`, `linear.assignee.name` and `hubspot.owner` all land on canonical
predicates without an LLM ever seeing them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from graph.models import ExtractedClaim, RawDoc, Schema, iso, load_schema

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

SELF = "__self__"


def dotted(record: dict, path: str | None) -> Any:
    """Walk a dotted path; missing links yield None rather than raising."""
    if not path:
        return None
    cur: Any = record
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def parse_record(tool: str, record: dict, schema: Schema) -> RawDoc:
    """Map one native record onto the common envelope."""
    cfg = schema.sources[tool]
    doc_id = dotted(record, cfg["doc_id"])
    if doc_id is None:
        raise ValueError(f"{tool}: record missing doc_id path {cfg['doc_id']!r}")

    acl: list[str] = []
    for path in cfg.get("acl") or []:
        acl.extend(_as_list(dotted(record, path)))
    participants: list[str] = []
    for path in cfg.get("participants") or []:
        participants.extend(_as_list(dotted(record, path)))

    return RawDoc(
        doc_id=str(doc_id),
        tool=tool,
        kind=cfg["artifact_kind"],
        title=str(dotted(record, cfg.get("title")) or ""),
        body=str(dotted(record, cfg.get("body")) or ""),
        author_raw=str(dotted(record, cfg.get("author")) or ""),
        author_email=str(dotted(record, cfg.get("author_email")) or ""),
        participants_raw=participants,
        created_at=iso(str(dotted(record, cfg.get("created_at")) or "")),
        updated_at=iso(str(dotted(record, cfg.get("updated_at")) or "")),
        acl_groups=acl,
        structured=record,
    )


def _field_evidence(tool: str, rule: dict, subject: object, obj: object) -> str:
    """Human-readable provenance for a field-map claim."""
    if rule["object"] != SELF:
        return f"{tool}.{rule['object']} = {str(obj)[:70]}"
    return f"{tool}.{rule['subject']} = {str(subject)[:70]}"


def apply_rules(doc: RawDoc, schema: Schema) -> list[ExtractedClaim]:
    """Emit deterministic claims from a source's field-map rules."""
    cfg = schema.sources[doc.tool]
    record = doc.structured
    claims: list[ExtractedClaim] = []

    for rule in cfg.get("rules") or []:
        subject = doc.key if rule["subject"] == SELF else dotted(record, rule["subject"])
        obj = doc.key if rule["object"] == SELF else dotted(record, rule["object"])
        if not subject or not obj:
            continue  # absent field == no claim, not a null claim

        predicate = rule["predicate"]
        if not schema.validate_predicate(predicate):
            raise ValueError(f"{doc.tool}: rule uses predicate {predicate!r} not in the vocabulary")

        asserted_at = doc.updated_at or doc.created_at
        if rule.get("asserted_at"):
            asserted_at = iso(str(dotted(record, rule["asserted_at"]) or "")) or asserted_at

        claims.append(
            ExtractedClaim(
                subject_surface=str(subject),
                subject_type=rule["subject_type"],
                predicate=predicate,
                object_surface=str(obj),
                object_type=rule["object_type"],
                source_tool=doc.tool,
                source_doc_id=doc.doc_id,
                asserted_at=asserted_at,
                asserted_by_surface=doc.author_raw,
                confidence=1.0,
                # Show the source field that produced the claim: this is the
                # ontology alignment made visible ("jira.fields.assignee -> ASSIGNED_TO").
                evidence_span=_field_evidence(doc.tool, rule, subject, obj),
                tier="A",
            )
        )
    return claims


def load_tool(tool: str, raw_dir: Path = RAW_DIR, schema: Schema | None = None) -> list[RawDoc]:
    schema = schema or load_schema()
    path = raw_dir / f"{tool}.jsonl"
    if not path.exists():
        return []
    docs = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                docs.append(parse_record(tool, json.loads(line), schema))
    return docs


def load_corpus(raw_dir: Path = RAW_DIR, schema: Schema | None = None) -> list[RawDoc]:
    """Every document across every configured tool."""
    schema = schema or load_schema()
    docs: list[RawDoc] = []
    for tool in schema.sources:
        docs.extend(load_tool(tool, raw_dir, schema))
    return docs


def tier_a_claims(docs: Iterable[RawDoc], schema: Schema | None = None) -> list[ExtractedClaim]:
    schema = schema or load_schema()
    claims: list[ExtractedClaim] = []
    for doc in docs:
        claims.extend(apply_rules(doc, schema))
    return claims


if __name__ == "__main__":
    schema = load_schema()
    docs = load_corpus(schema=schema)
    claims = tier_a_claims(docs, schema)

    by_tool: dict[str, int] = {}
    for d in docs:
        by_tool[d.tool] = by_tool.get(d.tool, 0) + 1
    print("documents parsed")
    for tool, n in sorted(by_tool.items()):
        print(f"  {tool:<11} {n:>3}")

    by_pred: dict[str, int] = {}
    for c in claims:
        by_pred[c.predicate] = by_pred.get(c.predicate, 0) + 1
    print(f"\ntier A claims: {len(claims)}")
    for pred, n in sorted(by_pred.items(), key=lambda kv: -kv[1]):
        print(f"  {pred:<14} {n:>3}")

    print("\nsample:")
    for c in claims[:6]:
        print(f"  ({c.subject_surface}) -{c.predicate}-> ({c.object_surface})  [{c.source_tool} @ {c.asserted_at[:10]}]")
