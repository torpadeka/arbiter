"""Build the graph: parse -> resolve -> arbitrate -> write to HydraDB.

    python -m ingest.load            # full pipeline against data/raw
    python -m ingest.load --dry-run  # build everything, write nothing

Writes are shaped around the engine's constraints (docs/hydradb-capabilities.md):
batched vertex upserts one label at a time, property-free edges batched one
type at a time, and every collection expressed as nodes + edges rather than
array properties.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from arbiter.policy import Decision, arbitrate
from graph.hydra import HydraClient
from graph.models import (
    Claim,
    ExtractedClaim,
    RawDoc,
    Schema,
    alias_key,
    artifact_key,
    claim_key,
    entity_key,
    group_key,
    load_schema,
    node_id,
    person_key,
    slugify,
)
from ingest.parse import load_corpus, parse_record, tier_a_claims
from resolve.align import AlignmentReport, align, known_from_tier_a
from resolve.engine import Resolution, resolve_people

HEDGE_TIER_SPECIFICITY = {"A": 0.80, "B": 0.55}


# --- claim construction -----------------------------------------------------


def _ref_key(surface: str, node_type: str, tool: str, resolution: Resolution, email_hint: str = "") -> str:
    """Canonical key for one side of a claim."""
    if node_type == "Person":
        cluster = resolution.lookup(surface, email_hint)
        return cluster.key if cluster else person_key(surface)
    if node_type == "Artifact":
        return surface if str(surface).startswith("artifact:") else artifact_key(tool, surface)
    return entity_key(node_type, surface)


def build_claims(
    docs: list[RawDoc],
    extracted: list[ExtractedClaim],
    resolution: Resolution,
    schema: Schema,
) -> list[Claim]:
    docs_by_key = {d.key: d for d in docs}
    hedges = [h.lower() for h in schema.arbitration.get("hedging_markers", [])]
    claims: list[Claim] = []

    for ex in extracted:
        source_key = artifact_key(ex.source_tool, ex.source_doc_id)
        doc = docs_by_key.get(source_key)
        email_hint = doc.author_email if doc else ""

        subject_key = _ref_key(ex.subject_surface, ex.subject_type, ex.source_tool, resolution, email_hint)
        object_key = _ref_key(ex.object_surface, ex.object_type, ex.source_tool, resolution)
        asserted_by = (
            _ref_key(doc.author_raw, "Person", doc.tool, resolution, doc.author_email)
            if doc and doc.author_raw
            else ""
        )

        text = f"{ex.evidence_span} {doc.body if doc else ''}".lower()
        hedging = 1.0 if any(marker in text for marker in hedges) else 0.0

        claims.append(
            Claim(
                key=claim_key(subject_key, ex.predicate, object_key, source_key, ex.asserted_at),
                predicate=ex.predicate,
                subject_key=subject_key,
                object_key=object_key,
                object_literal=ex.object_surface if ex.object_type == "Topic" else "",
                source_artifact_key=source_key,
                source_tool=ex.source_tool,
                asserted_by_key=asserted_by,
                asserted_at=ex.asserted_at,
                authority=schema.authority(ex.source_tool),
                confidence=ex.confidence,
                specificity=HEDGE_TIER_SPECIFICITY.get(ex.tier, 0.5),
                hedging=hedging,
                evidence_span=ex.evidence_span,
                tier=ex.tier,
            )
        )

    # Identical statements from one source appear more than once in real
    # corpora; keep one node per distinct claim key.
    unique: dict[str, Claim] = {}
    for c in claims:
        unique.setdefault(c.key, c)
    return list(unique.values())


# --- graph assembly ---------------------------------------------------------


class GraphBuild:
    """Accumulates node rows and edges, deduplicated, ready to write."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[int, dict]] = defaultdict(dict)      # label -> id -> row
        self.edges: dict[str, set[tuple[int, int]]] = defaultdict(set)  # type -> {(src, dst)}

    def node(self, label: str, key: str, **props) -> int:
        nid = node_id(key)
        row = {"id": nid, "key": key, **props}
        self.nodes[label].setdefault(nid, row).update(row)
        return nid

    def edge(self, rel_type: str, src_key: str, dst_key: str) -> None:
        self.edges[rel_type].add((node_id(src_key), node_id(dst_key)))

    @property
    def node_count(self) -> int:
        return sum(len(v) for v in self.nodes.values())

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self.edges.values())


def assemble(
    docs: list[RawDoc],
    claims: list[Claim],
    resolution: Resolution,
    decisions: list[Decision],
    schema: Schema,
    person_names: dict[str, str] | None = None,
) -> GraphBuild:
    g = GraphBuild()
    person_names = person_names or {}

    # People and their aliases. Collections are graph structure, not arrays.
    for cluster in resolution.clusters:
        g.node(
            "Person",
            cluster.key,
            # HERB identifies people by opaque id; the readable name lives in
            # metadata, so attach it without displacing the id as the key —
            # its own ground-truth answers are expressed in ids.
            name=person_names.get(cluster.canonical) or cluster.canonical,
            employee_id=cluster.canonical if person_names.get(cluster.canonical) else "",
            email=cluster.emails[0] if cluster.emails else "",
            alias_count=len(cluster.alias_surfaces()),
            mention_count=cluster.mention_count,
            tools=",".join(cluster.tools),
            merge_evidence=" | ".join(cluster.evidence)[:400],
        )
        for surface in cluster.alias_surfaces():
            g.node("Alias", alias_key(surface), name=surface)
            g.edge("ALIAS_OF", alias_key(surface), cluster.key)

    # Source documents, plus their visibility groups.
    for doc in docs:
        g.node("Artifact", doc.key, **{k: v for k, v in doc.artifact_row().items() if k not in {"id", "key"}})
        for grp in doc.acl_groups:
            g.node("Group", group_key(grp), name=grp)
            g.edge("VISIBLE_TO", doc.key, group_key(grp))

    # Entities referenced by claims but not produced by resolution.
    known = {c.key for c in resolution.clusters} | {d.key for d in docs}
    label_for = {"Project": "Project", "Account": "Account", "Topic": "Topic", "Team": "Team", "Decision": "Decision"}
    # Prefer the surface form the source actually used: slugified keys mangle
    # dates ("2026-07-12" -> "2026 07 12") and drop original casing.
    surfaces = {c.object_key: c.object_literal for c in claims if c.object_literal}
    for claim in claims:
        for key in (claim.subject_key, claim.object_key):
            if key in known or key.startswith(("person:", "artifact:")):
                continue
            prefix = key.split(":", 1)[0].capitalize()
            label = label_for.get(prefix, "Topic")
            g.node(label, key, name=surfaces.get(key) or key.split(":", 1)[1].replace("-", " "))

    # Claims: the provenance layer.
    for claim in claims:
        g.node("Claim", claim.key, **{k: v for k, v in claim.node_row().items() if k not in {"id", "key"}})
        g.edge("ABOUT", claim.key, claim.subject_key)
        g.edge("OBJECT", claim.key, claim.object_key)
        g.edge("SOURCED_FROM", claim.key, claim.source_artifact_key)
        if claim.asserted_by_key:
            g.edge("ASSERTED_BY", claim.key, claim.asserted_by_key)
        # Denormalized fast edge; status is encoded in the type because
        # batched edges cannot carry properties.
        g.edge(claim.fast_edge_type(), claim.subject_key, claim.object_key)

    for d in decisions:
        g.edge(d.relation, d.winner, d.loser)

    return g


def write(g: GraphBuild, client: HydraClient) -> None:
    for label, rows in sorted(g.nodes.items()):
        client.upsert_nodes(label, list(rows.values()))
    for rel_type, pairs in sorted(g.edges.items()):
        client.create_edges(rel_type, sorted(pairs))


def read_folder(data_dir: Path, schema: Schema, verbose: bool = True) -> list[RawDoc]:
    """Read a folder using the same profiler that induced the schema.

    `init` understands nested JSON, sections, CSV and text, so `ingest` has to
    read exactly what `init` saw. Reading only *.jsonl here would mean an
    induced schema could describe sources the loader cannot open.
    """
    from ingest.induce import profile

    docs: list[RawDoc] = []
    skipped: list[str] = []
    for source in profile(data_dir):
        if source.tool not in schema.sources:
            skipped.append(source.tool)
            continue
        for record in source.records:
            try:
                docs.append(parse_record(source.tool, record, schema))
            except (ValueError, KeyError):
                continue
    if verbose and skipped:
        print(f"  {len(skipped)} source(s) present in the folder but absent from the schema: "
              f"{', '.join(skipped[:4])}{'…' if len(skipped) > 4 else ''}")
    return docs


# --- pipeline ---------------------------------------------------------------


def run(
    dry_run: bool = False,
    verbose: bool = True,
    tier_b: bool = False,
    limit: int | None = None,
    source: str = "seed",
    products: int | None = None,
    schema_path: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> GraphBuild:
    schema = load_schema(Path(schema_path)) if schema_path else load_schema()
    person_names: dict[str, str] = {}

    if source == "herb":
        from ingest.herb import load_herb

        docs, extracted, person_names = load_herb(products, schema, verbose=verbose)
    else:
        docs = read_folder(Path(data_dir), schema, verbose) if data_dir else load_corpus(schema=schema)
        extracted = tier_a_claims(docs, schema)

    if tier_b:
        from ingest.extract import extract_corpus  # imported lazily: needs a configured LLM
        from llm import LLMError

        try:
            free_text = extract_corpus(docs, schema, limit=limit, verbose=verbose)
        except LLMError as exc:
            # Explicit and fatal: --tier-b was asked for, so quietly falling back
            # to tier A would misrepresent what ended up in the graph.
            raise SystemExit(f"tier B unavailable — {exc}\nRun without --tier-b for the deterministic pipeline.")

        # Prose does not speak in canonical keys; align before anything downstream
        # treats these as facts about known entities.
        report = AlignmentReport()
        known = known_from_tier_a(extracted, {d.key: d.title for d in docs if d.title})
        extracted += align(free_text, known, schema, report)
        if verbose:
            print(
                f"alignment      {len(report.aligned)} surfaces aligned, "
                f"{len(report.flipped)} reoriented, {len(report.dropped)} dropped, "
                f"{len(set(report.unaligned))} new entities"
            )
            for surface, canonical, why in report.aligned[:6]:
                print(f"    '{surface}' -> '{canonical}'  [{why}]")
            for what, why in report.flipped[:6]:
                print(f"    {what}: {why}")

    resolution = resolve_people(docs, extracted)
    claims = build_claims(docs, extracted, resolution, schema)
    decisions = arbitrate(claims, schema)
    g = assemble(docs, claims, resolution, decisions, schema, person_names)

    if verbose:
        print(f"documents      {len(docs)}")
        print(
            f"claims         {len(claims)}  "
            f"(tier A {sum(1 for c in claims if c.tier == 'A')}, "
            f"tier B {sum(1 for c in claims if c.tier == 'B')})"
        )
        print(f"entities       {len(resolution.clusters)} people, {resolution and len(resolution.vetoes)} merges vetoed")
        print(f"arbitrations   {len(decisions)}")
        for d in decisions:
            print(f"    {d.relation}: {d.reason}")
        print(f"nodes          {g.node_count}  ({', '.join(f'{k} {len(v)}' for k, v in sorted(g.nodes.items()))})")
        print(f"edges          {g.edge_count}  ({', '.join(f'{k} {len(v)}' for k, v in sorted(g.edges.items()))})")

    if not dry_run:
        with HydraClient() as client:
            write(g, client)
        if verbose:
            print("\nwritten to HydraDB")
    return g


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the Arbiter graph in HydraDB")
    ap.add_argument("--dry-run", action="store_true", help="assemble but do not write")
    ap.add_argument("--tier-b", action="store_true", help="run LLM extraction over free text (needs a configured LLM)")
    ap.add_argument("--limit", type=int, default=None, help="cap tier B documents")
    ap.add_argument("--source", choices=["seed", "herb"], default="seed", help="which corpus to ingest")
    ap.add_argument("--products", type=int, default=None, help="HERB only: how many products (whole products, never sampled documents)")
    args = ap.parse_args()
    run(dry_run=args.dry_run, tier_b=args.tier_b, limit=args.limit, source=args.source, products=args.products)
