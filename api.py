"""HTTP API for the Arbiter UI.

    python -m uvicorn api:app --port 8000

Thin wrapper over the same `answer.engine.Engine` the CLI uses, so the UI and
the terminal cannot disagree about what the graph says. Nothing is computed
here that is not computed there.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from answer.engine import Engine
from graph.hydra import HydraClient
from graph.models import Schema, load_schema

app = FastAPI(title="Arbiter", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE = Path(__file__).resolve().parent / ".arbiter" / "state.json"
_engine: Engine | None = None


def active_schema() -> Schema:
    """Whatever ontology the graph was built with, induced or hand-written."""
    import json

    try:
        path = json.loads(STATE.read_text(encoding="utf-8")).get("schema", "")
        if path and Path(path).exists():
            return load_schema(Path(path))
    except (OSError, ValueError):
        pass
    return load_schema()


def engine() -> Engine:
    """Built once: the entity index is a full scan and must not run per request."""
    global _engine
    if _engine is None:
        _engine = Engine(schema=active_schema())
    return _engine


class AskRequest(BaseModel):
    question: str
    as_of: str = ""
    use_model: bool = False
    max_hops: int = 3


@app.get("/api/health")
def health() -> dict:
    try:
        HydraClient().count("Claim")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@app.get("/api/stats")
def stats() -> dict:
    client = HydraClient()
    labels = ["Person", "Alias", "Artifact", "Claim", "Project", "Account", "Topic", "Group"]
    schema = active_schema()
    return {
        "counts": {label: client.count(label) for label in labels},
        "predicates": sorted(schema.predicates),
        "sources": sorted(schema.sources),
    }


@app.get("/api/entities")
def entities(limit: int = 40) -> dict:
    rows = HydraClient().query(
        "MATCH (p:Person) RETURN p.key AS key, p.name AS name, p.email AS email, "
        "p.alias_count AS aliases, p.mention_count AS mentions, p.tools AS tools, "
        "p.merge_evidence AS evidence"
    )

    def as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    people = sorted(rows, key=lambda r: -as_int(r.get("mentions")))[:limit]
    return {"people": [{
        "key": r.get("key"), "name": r.get("name"), "email": r.get("email") or "",
        "aliases": as_int(r.get("aliases")), "mentions": as_int(r.get("mentions")),
        "tools": (r.get("tools") or "").split(",") if r.get("tools") else [],
        "evidence": [e for e in (r.get("evidence") or "").split(" | ") if e],
    } for r in people]}


@app.post("/api/ask")
def ask(request: AskRequest) -> dict:
    started = time.time()
    answer = engine().ask(
        request.question, as_of=request.as_of, max_hops=request.max_hops, use_model=request.use_model
    )

    def claim(ev) -> dict:
        c = ev.claim
        return {
            "id": c.key.split(":")[-1][:8],
            "key": c.key,
            "predicate": c.predicate,
            "subject": answer.labels.get(c.subject_key) or c.subject_key.split(":")[-1],
            "object": answer.labels.get(c.object_key) or c.object_literal or c.object_key.split(":")[-1],
            "source_tool": c.source_tool,
            "source": c.source_artifact_key.split(":", 1)[-1],
            "asserted_at": c.asserted_at[:10],
            "score": round(c.score, 2),
            "authority": c.authority,
            "status": c.status,
            "evidence": c.evidence_span,
            "hops": ev.hops,
        }

    return {
        "question": answer.question,
        "status": answer.status,
        "gate": answer.gate,
        "answer": answer.text,
        "grounded_by_model": answer.grounded_by_model,
        "as_of": answer.as_of,
        "latency_ms": int((time.time() - started) * 1000),
        "entities": [
            {"name": e.name, "label": e.label, "matched_via": e.matched_via} for e in answer.entities
        ],
        "predicates": answer.predicates,
        "path": [{"from": a, "predicate": p, "to": b} for a, p, b in answer.path],
        "citations": [claim(e) for e in answer.evidence],
        "superseded": [claim(e) for e in answer.superseded],
        "contested": [claim(e) for e in answer.contested],
    }
