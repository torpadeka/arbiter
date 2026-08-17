"""Tier B: LLM triple extraction, constrained to the canonical vocabulary.

    python -m ingest.extract --limit 50

The predicate enum is compiled from ontology/schema.yaml into a JSON Schema and
enforced by the API via `output_config.format`, so the extractor *cannot* emit a
predicate outside the vocabulary — ontology alignment is a structural guarantee
here, not a prompt instruction the model may ignore. Anything it cannot express
in the vocabulary goes to `predicate_raw` with predicate `UNMAPPED`, which the
alignment pass clusters and proposes as new predicates.

Cost control:
  * Only documents with real free text are sent (tier A already covers fields).
  * The instruction block is a stable prefix with `cache_control`, so every call
    after the first reads it at ~0.1x input price.
  * Results are cached on a content hash; re-runs are free.
  * Concurrency is bounded by EXTRACT_CONCURRENCY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from graph.models import ExtractedClaim, RawDoc, Schema, load_schema
from ingest.parse import load_corpus
from llm import LLM

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
MIN_BODY_CHARS = 40
UNMAPPED = "UNMAPPED"


# --- schema-driven prompt + output format -----------------------------------


def output_schema(schema: Schema) -> dict:
    """JSON Schema constraining the model to the canonical vocabulary."""
    node_types = sorted(schema.node_types)
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Surface form exactly as written in the text"},
                        "subject_type": {"type": "string", "enum": node_types},
                        "predicate": {"type": "string", "enum": [*schema.predicate_names, UNMAPPED]},
                        "predicate_raw": {
                            "type": "string",
                            "description": f"Only when predicate is {UNMAPPED}: the relationship in 1-3 words, else empty",
                        },
                        "object": {"type": "string"},
                        "object_type": {"type": "string", "enum": node_types},
                        "asserted_at": {
                            "type": "string",
                            "description": "ISO-8601 date the statement is about, or empty to use the document date",
                        },
                        "evidence": {"type": "string", "description": "The exact sentence the claim came from"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "subject", "subject_type", "predicate", "predicate_raw",
                        "object", "object_type", "asserted_at", "evidence", "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }


def instructions(schema: Schema) -> str:
    """Stable prefix — cached, so it is worth being thorough here."""
    lines = [
        "You extract factual claims from enterprise documents into a fixed ontology.",
        "",
        "PREDICATES (domain -> range). Use only these:",
    ]
    for name in schema.predicate_names:
        spec = schema.predicates[name]
        lines.append(
            f"  {name}: {'|'.join(spec['domain'])} -> {'|'.join(spec['range'])}"
            f"  [{spec.get('cardinality', 'many')}]"
        )
    lines += [
        "",
        "RULES",
        "1. Extract only what the text asserts. Never infer, complete, or assume.",
        "2. Keep surface forms verbatim: write '@soham' if the text says '@soham'. Entity",
        "   resolution happens downstream and needs the original strings.",
        "3. Respect domain and range. If a statement does not fit any predicate's types,",
        f"   use {UNMAPPED} and put the relationship in predicate_raw.",
        "4. Dates: normalize to ISO-8601 (2026-05-03). A statement about when something",
        "   happens goes in `object`; asserted_at is when the claim was made, and is",
        "   usually empty (the document date is used).",
        "5. Hedged statements ('I think', 'maybe') are still claims — extract them and",
        "   lower confidence. The arbiter scores hedging separately.",
        "6. Questions, greetings and speculation about the future are not claims.",
        "7. Prefer the functional direction for one-valued predicates: a ticket has one",
        "   assignee, so write (ticket) ASSIGNED_TO (person), not the reverse.",
        "8. Return an empty list rather than a low-quality guess.",
    ]
    return "\n".join(lines)


def render_doc(doc: RawDoc) -> str:
    return (
        f"tool: {doc.tool}\nkind: {doc.kind}\nid: {doc.doc_id}\n"
        f"author: {doc.author_raw}\ndate: {doc.created_at}\n"
        f"participants: {', '.join(doc.participants_raw)}\n"
        f"title: {doc.title}\n\n{doc.body}"
    )


# --- extraction -------------------------------------------------------------


def _cache_path(doc: RawDoc, model: str, schema_version: int) -> Path:
    digest = hashlib.blake2b(
        f"{model}|{schema_version}|{render_doc(doc)}".encode("utf-8"), digest_size=16
    ).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def extract_doc(
    doc: RawDoc, schema: Schema, client: LLM, model: str, limiter: RateLimiter | None = None
) -> list[ExtractedClaim]:
    cache = _cache_path(doc, model, schema.version)
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        if limiter:
            limiter.wait()  # cached documents never consume quota
        payload = client.structured(instructions(schema), render_doc(doc), output_schema(schema))
        rows = payload.get("claims", []) if isinstance(payload, dict) else []
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    # Validate client-side regardless of provider. Where the API enforces the
    # schema this is redundant; on providers with only loose JSON mode it is
    # the thing that keeps the vocabulary closed.
    claims: list[ExtractedClaim] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        predicate = row.get("predicate", UNMAPPED)
        if predicate != UNMAPPED and not schema.validate_predicate(predicate):
            continue
        if row.get("subject_type") not in schema.node_types or row.get("object_type") not in schema.node_types:
            continue
        if not row.get("subject") or not row.get("object"):
            continue
        claims.append(
            ExtractedClaim(
                subject_surface=row["subject"],
                subject_type=row["subject_type"],
                predicate=predicate,
                object_surface=row["object"],
                object_type=row["object_type"],
                source_tool=doc.tool,
                source_doc_id=doc.doc_id,
                asserted_at=row.get("asserted_at") or doc.created_at,
                asserted_by_surface=doc.author_raw,
                confidence=float(row.get("confidence", 0.8)),
                evidence_span=row.get("evidence", "")[:400],
                tier="B",
                predicate_raw=row.get("predicate_raw", ""),
            )
        )
    return claims


class RateLimiter:
    """Requests-per-minute gate.

    Free tiers reject bursts outright, and a rejected request still costs
    wall-clock through the retry backoff. Pacing up front is cheaper than
    retrying: with the content-hash cache, a paced run is also a one-time cost.
    """

    def __init__(self, rpm: int) -> None:
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def worth_extracting(doc: RawDoc) -> bool:
    """Tier A already covers structured fields; only free text needs an LLM."""
    return len((doc.body or "").strip()) >= MIN_BODY_CHARS


def extract_corpus(
    docs: list[RawDoc],
    schema: Schema | None = None,
    limit: int | None = None,
    concurrency: int | None = None,
    verbose: bool = True,
) -> list[ExtractedClaim]:
    schema = schema or load_schema()
    client = LLM(model_env="EXTRACT_MODEL")
    model = client.cfg.model
    # Free tiers rate-limit hard; default concurrency and pacing are deliberately low.
    concurrency = concurrency or int(os.getenv("EXTRACT_CONCURRENCY", "2"))
    limiter = RateLimiter(int(os.getenv("EXTRACT_RPM", "10")))
    limit = limit if limit is not None else int(os.getenv("TIER_B_DOC_LIMIT", "15000"))

    targets = [d for d in docs if worth_extracting(d)][:limit]
    skipped = len(docs) - len(targets)
    if verbose:
        enforcement = "schema-enforced" if client.strict_schema else "schema-in-prompt + client-side validation"
        print(
            f"tier B: {len(targets)} documents ({skipped} skipped), "
            f"provider={client.cfg.provider}, model={model}, concurrency={concurrency} [{enforcement}]"
        )
    claims: list[ExtractedClaim] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(extract_doc, d, schema, client, model, limiter): d for d in targets}
        for i, future in enumerate(as_completed(futures), 1):
            doc = futures[future]
            try:
                claims.extend(future.result())
            except Exception as exc:  # one bad document must not kill the run
                failures += 1
                if verbose:
                    print(f"  ! {doc.key}: {type(exc).__name__}: {exc}")
            if verbose and i % 25 == 0:
                print(f"  {i}/{len(targets)} documents, {len(claims)} claims")

    if verbose:
        unmapped = sum(1 for c in claims if c.predicate == UNMAPPED)
        print(f"tier B done: {len(claims)} claims, {unmapped} unmapped, {failures} failures")
    return claims


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Tier B LLM extraction")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=None)
    args = ap.parse_args()

    schema = load_schema()
    docs = load_corpus(schema=schema)
    claims = extract_corpus(docs, schema, limit=args.limit, concurrency=args.concurrency)

    by_pred: dict[str, int] = {}
    for c in claims:
        by_pred[c.predicate] = by_pred.get(c.predicate, 0) + 1
    for pred, n in sorted(by_pred.items(), key=lambda kv: -kv[1]):
        print(f"  {pred:<16} {n:>4}")
    for c in claims[:10]:
        print(f"  ({c.subject_surface}) -{c.predicate}-> ({c.object_surface})  [{c.source_tool}] {c.evidence_span[:60]}")
