"""HTTP API for the Arbiter UI.

    python -m uvicorn api:app --port 8000

Thin wrapper over the same `answer.engine.Engine` the CLI uses, so the UI and
the terminal cannot disagree about what the graph says. Nothing is computed
here that is not computed there.
"""

from __future__ import annotations

import io
import itertools
import json
import subprocess
import threading
import time
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
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

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".arbiter" / "state.json"
_engine: Engine | None = None


# --- background jobs --------------------------------------------------------
# Induction and ingest take minutes, so they run on a thread and stream their
# progress. The pipeline already prints a readable narrative of what it is
# doing, so that output is captured line by line rather than reinvented.


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"          # running | done | failed
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str = ""


_jobs: dict[str, Job] = {}
_job_ids = itertools.count(1)


class _LineWriter(io.TextIOBase):
    """Appends whole lines to a job's log as the pipeline prints them."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.job.log.append(line.rstrip())
        return len(text)


def start_job(kind: str, work: Callable[[], dict]) -> Job:
    job = Job(id=f"{kind}-{next(_job_ids)}", kind=kind)
    _jobs[job.id] = job

    def run() -> None:
        try:
            with redirect_stdout(_LineWriter(job)):
                job.result = work()
            job.status = "done"
        except BaseException as exc:  # a failed demo step must still report
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"[:500]
            job.log.append(job.error)

    threading.Thread(target=run, daemon=True).start()
    return job


def reset_engine() -> None:
    """Drop cached state so the next question sees the new graph and ontology."""
    global _engine
    _engine = None
    load_schema.cache_clear()


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


class InduceRequest(BaseModel):
    data_dir: str = "data/raw"
    vocab_docs: int = 40


class IngestRequest(BaseModel):
    tier_b: bool = False
    source: str = "folder"      # folder | herb
    products: int | None = None


# --- pipeline: reset, induce, ingest ----------------------------------------


def wipe_graph() -> None:
    """Empty the graph. The engine has no DELETE, so the object store is wiped."""
    print("wiping the object store the graph lives in")
    done = subprocess.run(
        ["powershell", "-File", str(ROOT / "scripts" / "reset_graph.ps1")],
        capture_output=True, text=True, timeout=600,
    )
    for line in (done.stdout or "").splitlines():
        if line.strip():
            print(line.strip())
    if done.returncode != 0:
        raise RuntimeError((done.stderr or "reset failed")[:300])
    reset_engine()


def graph_summary(graph) -> dict:
    return {
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "by_label": {k: len(v) for k, v in sorted(graph.nodes.items())},
        "by_edge": {k: len(v) for k, v in sorted(graph.edges.items())},
    }


@app.post("/api/reset")
def reset() -> dict:
    def work() -> dict:
        wipe_graph()
        return {"reset": True}

    return {"job": start_job("reset", work).id}


@app.post("/api/demo/herb")
def demo_herb(request: IngestRequest) -> dict:
    """One click: wipe, download the dataset if needed, build with the adapter.

    Separate from the folder flow on purpose. This path uses a purpose-written
    reader rather than an induced ontology, so it is a prepared demonstration
    rather than a demonstration of adapting to unseen data.
    """
    def work() -> dict:
        from ingest.herb import fetch, product_files
        from ingest.load import run as load_run

        wipe_graph()
        want = request.products or 5
        have = len(product_files())
        if have < want:
            print(f"fetching HERB from huggingface: {have} of {want} products present")
            fetch(want)
            print(f"fetched, {len(product_files())} product files available")
        graph = load_run(tier_b=request.tier_b, source="herb", products=want)
        reset_engine()
        return graph_summary(graph)

    return {"job": start_job("ingest", work).id}


@app.post("/api/induce")
def induce(request: InduceRequest) -> dict:
    """Derive an ontology from a folder: no schema written by hand."""
    data_dir = (ROOT / request.data_dir).resolve() if not Path(request.data_dir).is_absolute() else Path(request.data_dir)
    if not data_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"{data_dir} is not a directory")

    def work() -> dict:
        from ingest.induce import run as induce_run

        out = ROOT / "ontology" / "generated.yaml"
        schema = induce_run(data_dir, out, vocab_docs=request.vocab_docs)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"schema": str(out), "data_dir": str(data_dir)}, indent=2), encoding="utf-8")
        reset_engine()
        return {
            "sources": len(schema["sources"]),
            "predicates": [
                {"name": name, "domain": spec["domain"], "range": spec["range"],
                 "cardinality": spec["cardinality"], "temporal": spec["temporal"],
                 "observed": spec.get("observed_objects_per_subject")}
                for name, spec in schema["predicates"].items()
            ],
            "rules": sum(len(block["rules"]) for block in schema["sources"].values()),
            "authority": schema["arbitration"]["source_authority"],
        }

    return {"job": start_job("induce", work).id}


@app.post("/api/ingest")
def ingest(request: IngestRequest) -> dict:
    """Parse, resolve, arbitrate and write the graph."""
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}

    if not state.get("schema"):
        raise HTTPException(status_code=400, detail="no ontology yet: induce one from the folder first")

    def work() -> dict:
        from ingest.load import run as load_run

        graph = load_run(
            tier_b=request.tier_b,
            source="seed",
            schema_path=state.get("schema"),
            data_dir=state.get("data_dir"),
        )
        reset_engine()
        return graph_summary(graph)

    return {"job": start_job("ingest", work).id}


@app.get("/api/job/{job_id}")
def job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    return {"id": job.id, "kind": job.kind, "status": job.status,
            "log": job.log, "result": job.result, "error": job.error}


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

    # Whether prose reading is even possible, so the UI can explain instead of
    # offering a switch that silently fails.
    try:
        from llm import LLMConfig, available

        provider = LLMConfig.from_env().provider if available() else ""
    except Exception:
        provider = ""

    return {
        "counts": {label: client.count(label) for label in labels},
        "predicates": sorted(schema.predicates),
        "sources": sorted(schema.sources),
        "ai": {"configured": bool(provider), "provider": provider},
    }


@app.get("/api/entities")
def entities(limit: int = 40) -> dict:
    rows = HydraClient().scan(
        "(p:Person)",
        "p.key AS key, p.name AS name, p.email AS email, p.alias_count AS aliases, "
        "p.mention_count AS mentions, p.tools AS tools, p.merge_evidence AS evidence",
        "p.key",
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
