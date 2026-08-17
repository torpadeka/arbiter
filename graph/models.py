"""Domain models, key conventions, and the ontology loader.

Keys are human-readable strings (`person:sam-ratnaparkhi`); engine ids are
`sid(key)`. Everything written to HydraDB must be scalar-valued — see
docs/hydradb-capabilities.md — so models expose explicit `*_row()` methods
rather than dumping nested dicts.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from graph.hydra import sid

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "ontology" / "schema.yaml"


# --- ontology ---------------------------------------------------------------


class Schema(BaseModel):
    version: int
    node_types: dict[str, dict]
    artifact_kinds: list[str]
    predicates: dict[str, dict]
    structural_edges: list[str]
    arbitration: dict
    retrieval: dict
    sources: dict[str, dict]

    @property
    def predicate_names(self) -> list[str]:
        return sorted(self.predicates)

    def is_temporal(self, predicate: str) -> bool:
        return bool(self.predicates.get(predicate, {}).get("temporal", False))

    def is_functional(self, predicate: str) -> bool:
        """One current value per subject — the precondition for a conflict."""
        return self.predicates.get(predicate, {}).get("cardinality") == "one"

    def authority(self, tool: str) -> float:
        return float(self.arbitration["source_authority"].get(tool, 0.5))

    def validate_predicate(self, predicate: str) -> bool:
        return predicate in self.predicates


@lru_cache(maxsize=1)
def load_schema(path: Path | str = SCHEMA_PATH) -> Schema:
    return Schema(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))


# --- keys -------------------------------------------------------------------


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "unknown"


def person_key(canonical: str) -> str:
    return f"person:{slugify(canonical)}"


def alias_key(surface: str) -> str:
    return f"alias:{slugify(surface)}"


def entity_key(node_type: str, name: str) -> str:
    return f"{node_type.lower()}:{slugify(name)}"


def artifact_key(tool: str, doc_id: str) -> str:
    return f"artifact:{tool}:{slugify(doc_id)}"


def group_key(name: str) -> str:
    return f"group:{slugify(name)}"


def claim_key(subject: str, predicate: str, obj: str, source: str, asserted_at: str) -> str:
    digest = hashlib.blake2b(
        "|".join([subject, predicate, obj, source, asserted_at or ""]).encode("utf-8"),
        digest_size=8,
    ).hexdigest()
    return f"claim:{digest}"


def node_id(key: str) -> int:
    return sid(key)


# --- documents --------------------------------------------------------------


class RawDoc(BaseModel):
    """Normalized envelope produced by every parser."""

    doc_id: str
    tool: str
    kind: str
    title: str = ""
    body: str = ""
    author_raw: str = ""
    author_email: str = ""
    participants_raw: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    permalink: str = ""
    acl_groups: list[str] = Field(default_factory=list)
    structured: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return artifact_key(self.tool, self.doc_id)

    def artifact_row(self) -> dict:
        """Scalar-only node row for HydraDB."""
        return {
            "id": node_id(self.key),
            "key": self.key,
            "name": (self.title or self.doc_id)[:300],
            "tool": self.tool,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at or self.created_at,
            "permalink": self.permalink,
            "author_raw": self.author_raw,
            "excerpt": (self.body or "")[:500],
        }


# --- claims -----------------------------------------------------------------


class ExtractedClaim(BaseModel):
    """A statement as extracted, before entity resolution.

    Subject and object are still surface strings ("@soham", "Sam"); the
    resolver rewrites them to canonical keys.
    """

    subject_surface: str
    subject_type: str
    predicate: str
    object_surface: str
    object_type: str
    source_tool: str
    source_doc_id: str
    asserted_at: str = ""
    asserted_by_surface: str = ""
    confidence: float = 1.0
    evidence_span: str = ""
    tier: str = "A"  # A = deterministic field map, B = LLM extraction
    predicate_raw: str = ""  # set when the extractor could not map to the enum


class Claim(BaseModel):
    """A resolved claim: canonical subject/object, scored, ready to write."""

    key: str
    predicate: str
    subject_key: str
    object_key: str
    object_literal: str = ""
    source_artifact_key: str
    source_tool: str
    asserted_by_key: str = ""
    asserted_at: str = ""
    authority: float = 0.5
    confidence: float = 1.0
    specificity: float = 0.5
    corroboration: int = 1
    hedging: float = 0.0
    score: float = 0.0
    status: str = "current"  # current | superseded | contested
    evidence_span: str = ""
    tier: str = "A"

    @property
    def id(self) -> int:
        return node_id(self.key)

    def node_row(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "predicate": self.predicate,
            "subject_key": self.subject_key,
            "object_key": self.object_key,
            "object_literal": self.object_literal[:300],
            "source_tool": self.source_tool,
            "source_artifact": self.source_artifact_key,
            "asserted_at": self.asserted_at,
            "authority": self.authority,
            "confidence": self.confidence,
            "score": self.score,
            "corroboration": self.corroboration,
            "status": self.status,
            "evidence_span": self.evidence_span[:400],
            "tier": self.tier,
        }

    def fast_edge_type(self) -> str:
        """Relationship type for the denormalized edge.

        Batched edges carry no properties, so status is encoded in the type.
        """
        return self.predicate if self.status != "superseded" else f"{self.predicate}_SUPERSEDED"


# --- time -------------------------------------------------------------------


def parse_ts(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse; corpora are inconsistent about timezones."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.split(".")[0], text[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def iso(value: str) -> str:
    dt = parse_ts(value)
    return dt.isoformat() if dt else ""
