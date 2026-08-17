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
_engine_claims: int = -1


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
    global _engine, _engine_claims
    _engine = None
    _engine_claims = -1
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
    """Cached, but invalidated when the graph underneath it changes.

    The entity index is a full scan, so it cannot be rebuilt per request. It also
    cannot be cached forever: a graph rebuilt from the CLI while this process is
    running would leave the index describing a corpus that no longer exists, and
    the symptom is the worst kind, confidently reporting no record of something
    plainly present. A claim count is cheap and settles it.
    """
    global _engine, _engine_claims
    try:
        current = HydraClient().count("Claim")
    except Exception:
        current = _engine_claims
    if _engine is None or current != _engine_claims:
        load_schema.cache_clear()
        _engine = Engine(schema=active_schema())
        _engine_claims = current
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
    use_builtin: bool = False   # build with the ontology shipped here, not a derived one
    data_dir: str = ""          # only needed when building without deriving first


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

    if not request.use_builtin and not state.get("schema"):
        raise HTTPException(status_code=400, detail="no ontology yet: derive one from the folder first")

    folder = request.data_dir or state.get("data_dir") or str(ROOT / "data" / "raw")

    def work() -> dict:
        from ingest.load import run as load_run

        if request.use_builtin:
            print(f"building with the ontology shipped with this project, over {folder}")
        graph = load_run(
            tier_b=request.tier_b,
            source="seed",
            # None means the curated ontology in ontology/schema.yaml.
            schema_path=None if request.use_builtin else state.get("schema"),
            data_dir=folder,
        )
        if request.use_builtin:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({"data_dir": folder}, indent=2), encoding="utf-8")
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


READABLE_SUFFIXES = {".jsonl", ".ndjson", ".json", ".csv", ".md", ".txt", ".text"}


def _readable_count(folder: Path, cap: int = 400) -> int:
    """Readable files at most two levels down, bounded.

    Bounded on purpose: an unbounded walk of a large tree would hang the folder
    browser on the first click into a home directory.
    """
    total = 0
    try:
        for child in folder.iterdir():
            if child.is_file() and child.suffix.lower() in READABLE_SUFFIXES:
                total += 1
            elif child.is_dir() and not child.name.startswith("."):
                try:
                    for grandchild in child.iterdir():
                        if grandchild.is_file() and grandchild.suffix.lower() in READABLE_SUFFIXES:
                            total += 1
                            if total >= cap:
                                return total
                except (PermissionError, OSError):
                    continue
            if total >= cap:
                return total
    except (PermissionError, OSError):
        return total
    return total


@app.get("/api/browse")
def browse(path: str = "") -> dict:
    """List folders so the UI can offer a picker.

    A browser cannot give a server a filesystem path: a directory input hands
    over file contents, not locations, and ingestion reads from disk. So the
    server does the browsing and the UI navigates it.
    """
    target = Path(path).expanduser() if path else ROOT
    if not target.exists() or not target.is_dir():
        target = ROOT
    target = target.resolve()

    folders = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                folders.append({"name": child.name, "path": str(child), "files": _readable_count(child)})
    except (PermissionError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"cannot read that folder: {exc}") from None

    here = 0
    try:
        here = sum(1 for f in target.iterdir() if f.is_file() and f.suffix.lower() in READABLE_SUFFIXES)
    except (PermissionError, OSError):
        pass

    parent = str(target.parent) if target.parent != target else None
    return {
        "path": str(target),
        "parent": parent,
        "folders": folders,
        "files_here": here,
        "shortcuts": [
            {"name": "project", "path": str(ROOT)},
            {"name": "example data", "path": str(ROOT / "data" / "raw")},
            {"name": "home", "path": str(Path.home())},
        ],
    }


@app.get("/api/ontology")
def ontology() -> dict:
    """The rules the loaded graph is actually running on.

    Served rather than hardcoded in the UI so the explanation page describes the
    ontology in force, including an induced one, instead of a description that
    can drift away from the code.
    """
    schema = active_schema()
    arb = schema.arbitration
    return {
        "predicates": [
            {
                "name": name,
                "domain": spec.get("domain", []),
                "range": spec.get("range", []),
                "cardinality": spec.get("cardinality", "many"),
                "temporal": bool(spec.get("temporal")),
                "observed": spec.get("observed_objects_per_subject"),
            }
            for name, spec in sorted(schema.predicates.items())
        ],
        "weights": arb.get("weights", {}),
        "authority": dict(sorted(arb.get("source_authority", {}).items(), key=lambda kv: -kv[1])),
        "contested_margin": arb.get("contested_margin"),
        "sufficiency_threshold": schema.retrieval.get("sufficiency_threshold"),
        "max_hops": schema.retrieval.get("max_hops"),
        "sources": sorted(schema.sources),
        "structural_edges": schema.structural_edges,
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


# Question shapes keyed on words in a predicate name, so this works for a
# hand-written vocabulary and an induced one alike. Two phrasings per shape,
# because direction decides the sentence: AUTHORED runs person to document, so
# its subject is the author, and asking "who authored Wei Chen" is nonsense.
QUESTION_SHAPES: list[tuple[tuple[str, ...], str, str]] = [
    # needles, subject is a thing, subject is a person
    (("assign",), "who is {s} assigned to?", "what is assigned to {s}?"),
    (("report",), "who reports to {s}?", "who does {s} report to?"),
    (("own",), "who owns {s}?", "what does {s} own?"),
    (("author", "wrote"), "who authored {s}?", "what has {s} authored?"),
    (("review", "approv"), "who reviewed {s}?", "what has {s} reviewed?"),
    (("status", "state"), "what is the status of {s}?", "what is the status of {s}?"),
    (("due", "schedul", "launch"), "when is {s} due?", "when is {s} due?"),
    (("block",), "what blocks {s}?", "what does {s} block?"),
    (("budget", "cost"), "what is the budget for {s}?", "what is the budget for {s}?"),
    (("member", "team"), "who is on {s}?", "what is {s} a member of?"),
    (("customer", "account", "company"), "which customer is {s} linked to?", "which customers does {s} cover?"),
    (("mention", "discuss", "reference", "involve"), "what does {s} mention?", "where is {s} mentioned?"),
    (("work",), "who works on {s}?", "what does {s} work on?"),
    (("priorit",), "what is the priority of {s}?", "what is the priority of {s}?"),
    (("escalat",), "who is {s} escalated to?", "what is escalated to {s}?"),
]


def phrase_question(predicate: str, subject: str, subject_is_person: bool = False) -> str:
    words = predicate.lower().replace("_", " ")
    for needles, thing_first, person_first in QUESTION_SHAPES:
        if any(n in words for n in needles):
            return (person_first if subject_is_person else thing_first).format(s=subject)
    return f"what is the {words} of {subject}?"


@app.get("/api/suggestions")
def suggestions(limit: int = 6) -> dict:
    """Questions drawn from the graph that is actually loaded.

    Hardcoded examples are worse than none: after loading a different corpus
    they all miss, which reads as a broken system rather than a different
    dataset. These are built from real subjects, and deliberately include a
    disagreement and something the corpus cannot answer.
    """
    client = HydraClient()
    eng = engine()
    schema = active_schema()

    def name_of(key: str) -> str:
        label = eng.label(key)
        return label if label and label != "?" else key.split(":")[-1].replace("-", " ")

    fields = ("c.key AS key, c.predicate AS predicate, c.subject_key AS subject_key, "
              "c.status AS status")
    current = client.query(
        f"MATCH (c:Claim) WHERE c.status = $s RETURN {fields} ORDER BY key LIMIT 600",
        {"s": "current"},
    )
    overruled = client.query(
        f"MATCH (c:Claim) WHERE c.status = $s RETURN {fields} ORDER BY key LIMIT 40",
        {"s": "superseded"},
    )

    out: list[dict] = []
    seen_predicates: set[str] = set()

    # Lead with a disagreement if the corpus contains one: it is the most
    # revealing question anyone can ask of this system.
    for row in overruled:
        key = row.get("subject_key") or ""
        subject = name_of(key)
        if subject:
            out.append({
                "q": phrase_question(row["predicate"], subject, key.startswith("person:")),
                "tag": "sources disagree",
            })
            seen_predicates.add(row["predicate"])
            break

    # Then a spread across different relationships, one per predicate.
    for row in current:
        predicate = row.get("predicate") or ""
        if predicate in seen_predicates or predicate in {"MENTIONS", "DISCUSSED_IN"}:
            continue
        key = row.get("subject_key") or ""
        subject = name_of(key)
        if not subject or len(subject) > 48:
            continue
        seen_predicates.add(predicate)
        out.append({
            "q": phrase_question(predicate, subject, key.startswith("person:")),
            "tag": "look up",
        })
        if len(out) >= limit * 3:
            break

    # Finish with something the corpus provably cannot answer: a relationship in
    # the vocabulary that no statement uses, asked of a subject that does exist.
    used = {r.get("predicate") for r in current} | {r.get("predicate") for r in overruled}
    unused = [p for p in schema.predicate_names if p not in used]
    # For a question that should have no answer, the subject matters. Prefer a
    # project or a person over a document, and avoid names that contain a word
    # the planner reads as a relationship: an artifact called "Atlas launch"
    # makes "launch" look like part of the question.
    collisions = {"launch", "due", "status", "owner", "assigned", "report", "budget",
                  "block", "review", "author", "member", "work", "priority", "escalate"}

    def anchor_rank(key: str) -> tuple:
        name = name_of(key).lower()
        dirty = any(word in collisions for word in name.split())
        return (dirty, key.startswith("artifact:"), len(name))

    anchors = sorted(
        {r["subject_key"] for r in current if r.get("subject_key")},
        key=anchor_rank,
    )[:6]
    for predicate in (unused or ["BUDGET_IS"])[:4]:
        for anchor_key in anchors:
            anchor = name_of(anchor_key)
            if anchor and len(anchor) <= 48:
                out.append({
                    "q": phrase_question(predicate, anchor, anchor_key.startswith("person:")),
                    "tag": "no answer exists",
                })
                break

    # Verify before offering. A generated question can miss for reasons no
    # template can foresee: here an artifact is literally named "Atlas launch",
    # and "launch" is a word the planner reads as a predicate, so a question
    # meant to have no answer quietly found one. Asking each candidate is
    # cheap, and a suggestion that misbehaves on screen is not.
    buckets: dict[str, list[dict]] = {"sources disagree": [], "look up": [], "no answer exists": []}
    for candidate in out:
        tag = candidate["tag"]
        # Enough of each kind to fill the reserved slots, and no more: every
        # candidate costs a real traversal to verify.
        if len(buckets[tag]) >= (limit if tag == "look up" else 2):
            continue
        try:
            answer = engine().ask(candidate["q"], use_model=False)
        except Exception:
            continue
        if (tag != "no answer exists") == answer.abstained:
            continue
        if tag == "sources disagree" and not answer.superseded:
            tag = "look up"
        buckets[tag].append({**candidate, "tag": tag})

    # A disagreement and a question with no answer are the two most revealing
    # things this system does, so each gets a reserved slot rather than
    # competing with straightforward lookups for space.
    picked = buckets["sources disagree"][:1] + buckets["no answer exists"][:1]
    picked += buckets["look up"][: max(0, limit - len(picked))]
    return {"suggestions": picked[:limit]}


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
