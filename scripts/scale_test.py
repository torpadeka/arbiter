"""Scale test: how far does the pipeline actually go?

    python scripts/scale_test.py                    # parse -> resolve -> arbitrate, no writes
    python scripts/scale_test.py --write --docs 20000   # include graph writes at one size

Answers the question that decides HERB scope: can tier A absorb ~39k documents,
and what does the graph cost in time and disk? Measures a curve rather than one
point, because the failure modes scale differently — entity resolution is
pairwise-ish within blocks, graph writes are linear but bounded by a 30s
server-side query timeout.

The generated corpus mimics HERB's shape: ~530 employees, 30 products, nine
tools, and every person appearing under several surface forms, so entity
resolution faces realistic blocking pressure instead of a toy alias list.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arbiter.policy import arbitrate  # noqa: E402
from graph.hydra import HydraClient  # noqa: E402
from graph.models import load_schema  # noqa: E402
from ingest.load import assemble, build_claims, write  # noqa: E402
from ingest.parse import load_corpus, tier_a_claims  # noqa: E402
from resolve.engine import resolve_people  # noqa: E402

SCALE_DIR = Path(__file__).resolve().parents[1] / "data" / "scale"

FIRST = ["sam", "priya", "wei", "dana", "alex", "jordan", "maya", "omar", "nina", "raj",
         "lena", "tom", "ana", "yuki", "kofi", "ivan", "sara", "liam", "zoe", "hugo"]
LAST = ["ratnaparkhi", "nair", "chen", "okafor", "silva", "novak", "haddad", "olsen",
        "moreau", "tanaka", "ferreira", "kowalski", "adeyemi", "rossi", "singh",
        "mbeki", "larsen", "duarte", "kim", "petrov"]
WORDS = ["atlas", "beacon", "cinder", "delta", "ember", "falcon", "gamma", "harbor",
         "ionic", "juniper", "kestrel", "lumen", "mosaic", "nimbus", "onyx", "pilot"]
STATUSES = ["In Progress", "Done", "In Review", "Blocked", "Planned"]


def people(count: int, rng: random.Random) -> list[dict]:
    """Employees, each with several surface forms — the ER workload."""
    out = []
    for i in range(count):
        first, last = FIRST[i % len(FIRST)], LAST[(i // len(FIRST)) % len(LAST)]
        suffix = "" if i < len(FIRST) * len(LAST) else str(i)
        full = f"{first.capitalize()} {last.capitalize()}{suffix}"
        out.append({
            "full": full,
            "email": f"{first}.{last}{suffix}@northwind.com",
            "handle": f"@{first}{suffix}",
            "initial": f"{first[0].upper()}. {last.capitalize()}{suffix}",
            "login": f"{first}-{last[0]}{suffix}",
        })
    return out


def generate(n_docs: int, seed: int = 42) -> Path:
    """Write a synthetic corpus of n_docs across nine tools."""
    rng = random.Random(seed)
    raw = SCALE_DIR / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)

    staff = people(530, rng)
    projects = [f"{w.capitalize()} {s}" for w in WORDS for s in ("Migration", "Platform")][:30]
    accounts = [f"{w.capitalize()} Corp" for w in WORDS]

    per_tool = max(1, n_docs // 9)
    buckets: dict[str, list[dict]] = {t: [] for t in
                                      ("slack", "gmail", "jira", "linear", "github",
                                       "confluence", "drive", "hubspot", "fireflies")}

    for i in range(per_tool):
        p, q = rng.choice(staff), rng.choice(staff)
        proj, acct = rng.choice(projects), rng.choice(accounts)
        day = 1 + (i % 28)
        month = 1 + (i % 12)
        ts = f"2026-{month:02d}-{day:02d}T10:00:00Z"

        buckets["slack"].append({
            "ts": f"17724{i:06d}.000{i % 1000:03d}", "ts_iso": ts,
            "channel": f"eng-{proj.split()[0].lower()}", "user": p["handle"],
            "user_email": p["email"],
            "text": f"{q['full']} is picking up the {proj} rollout. Ping {p['initial']} if blocked."})
        buckets["gmail"].append({
            "message_id": f"msg-{i}", "subject": f"{proj} status", "date": ts,
            "from": p["email"], "to": [q["email"]],
            "body": f"{proj} update. {q['full']} owns delivery; escalate to {p['full']}."})
        buckets["jira"].append({
            "key": f"ENG-{1000 + i}", "fields": {
                "summary": f"{proj} - workstream {i % 40}",
                "description": f"Work item for {proj}.",
                "created": ts, "updated": ts,
                "reporter": {"displayName": p["full"]},
                "assignee": {"displayName": q["initial"]},
                "project": {"name": proj, "key": proj.split()[0][:4].upper()},
                "status": {"name": STATUSES[i % len(STATUSES)]},
                "duedate": f"2026-{month:02d}-28"}})
        buckets["linear"].append({
            "identifier": f"LIN-{i}", "title": f"{proj} milestone {i % 20}",
            "description": f"Milestone for {proj}.", "createdAt": ts, "updatedAt": ts,
            "creator": {"name": p["full"]}, "assignee": {"name": q["full"]},
            "project": {"name": proj}, "state": {"name": STATUSES[i % len(STATUSES)]},
            "team": {"key": "PLATFORM"}, "dueDate": f"2026-{month:02d}-27"})
        buckets["github"].append({
            "number": 1000 + i, "title": f"{proj} change {i % 50}",
            "body": f"Implements part of {proj}.", "created_at": ts, "merged_at": ts,
            "user": {"login": p["login"]}, "reviewer": {"login": q["login"]},
            "repo": f"northwind/{proj.split()[0].lower()}"})
        buckets["confluence"].append({
            "id": f"conf-{i}", "title": f"{proj} design note {i % 30}", "space": "ENG",
            "created": ts, "updated": ts, "author": p["full"],
            "body": f"Design for {proj}. {q['full']} reviews. Owner is {p['initial']}."})
        buckets["drive"].append({
            "file_id": f"file-{i}", "name": f"{proj}-doc-{i % 30}.md", "drive_id": "eng-shared",
            "createdTime": ts, "modifiedTime": ts, "owner": p["full"],
            "content": f"Notes on {proj} covering rollout and risk."})
        buckets["hubspot"].append({
            "deal_id": f"deal-{i}", "dealname": f"{acct} expansion", "company": acct,
            "product": proj, "owner": q["full"], "pipeline": "enterprise",
            "createdate": ts, "hs_lastmodifieddate": ts,
            "notes": f"{acct} renewal depends on {proj}."})
        buckets["fireflies"].append({
            "meeting_id": f"ff-{i}", "title": f"{proj} sync", "date": ts,
            "organizer": p["full"], "attendees": [q["full"], p["full"]],
            "transcript": f"{p['full']}: who owns {proj}? {q['full']}: I do now."})

    total = 0
    for tool, records in buckets.items():
        with (raw / f"{tool}.jsonl").open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        total += len(records)
    return raw


def bucket_bytes() -> int:
    """Size of the graph in the object store, via mc."""
    try:
        out = subprocess.run(
            ["docker", "run", "--rm", "--network", "arbiter-net", "--entrypoint", "sh",
             "quay.io/minio/mc", "-c",
             "mc alias set l http://arbiter-minio:9000 minioadmin minioadmin > /dev/null && "
             "mc du --json l/hydradb"],
            capture_output=True, text=True, timeout=120,
        )
        for line in out.stdout.strip().splitlines()[::-1]:
            if line.strip().startswith("{"):
                return int(json.loads(line).get("size", 0))
    except Exception:
        pass
    return 0


def run_size(n_docs: int, do_write: bool, schema) -> dict:
    raw = generate(n_docs)
    timings: dict[str, float] = {}

    t = time.perf_counter()
    docs = load_corpus(raw, schema)
    timings["parse"] = time.perf_counter() - t

    t = time.perf_counter()
    extracted = tier_a_claims(docs, schema)
    timings["extract_A"] = time.perf_counter() - t

    t = time.perf_counter()
    resolution = resolve_people(docs, extracted)
    timings["resolve"] = time.perf_counter() - t

    t = time.perf_counter()
    claims = build_claims(docs, extracted, resolution, schema)
    decisions = arbitrate(claims, schema)
    timings["arbitrate"] = time.perf_counter() - t

    t = time.perf_counter()
    graph = assemble(docs, claims, resolution, decisions, schema)
    timings["assemble"] = time.perf_counter() - t

    row = {
        "docs": len(docs), "claims": len(claims), "people": len(resolution.clusters),
        "nodes": graph.node_count, "edges": graph.edge_count,
        "conflicts": len(decisions), **timings,
    }

    if do_write:
        subprocess.run(["powershell", "-File", str(Path(__file__).parent / "reset_graph.ps1")],
                       capture_output=True, timeout=300)
        before = bucket_bytes()
        t = time.perf_counter()
        with HydraClient() as client:
            write(graph, client)
        row["write"] = time.perf_counter() - t
        row["bytes"] = bucket_bytes() - before

    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure pipeline throughput at scale")
    ap.add_argument("--docs", type=int, nargs="*", default=[1000, 5000, 20000, 40000])
    ap.add_argument("--write", action="store_true", help="also write to HydraDB and measure disk")
    args = ap.parse_args()

    schema = load_schema()
    rows = []
    for n in args.docs:
        print(f"\n=== {n} documents ===", flush=True)
        try:
            row = run_size(n, args.write, schema)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            break
        rows.append(row)
        stages = " ".join(f"{k} {row[k]:.1f}s" for k in
                          ("parse", "extract_A", "resolve", "arbitrate", "assemble") if k in row)
        print(f"  {row['docs']} docs -> {row['claims']} claims, {row['people']} people, "
              f"{row['nodes']} nodes, {row['edges']} edges, {row['conflicts']} conflicts")
        print(f"  {stages}")
        if "write" in row:
            print(f"  write {row['write']:.1f}s, graph {row['bytes'] / 1e6:.1f} MB")

    if rows:
        print(f"\n{'docs':>7}{'claims':>9}{'nodes':>9}{'edges':>9}{'CPU s':>8}{'write s':>9}{'MB':>8}")
        for r in rows:
            cpu = sum(r[k] for k in ("parse", "extract_A", "resolve", "arbitrate", "assemble") if k in r)
            print(f"{r['docs']:>7}{r['claims']:>9}{r['nodes']:>9}{r['edges']:>9}{cpu:>8.1f}"
                  f"{r.get('write', 0):>9.1f}{r.get('bytes', 0) / 1e6:>8.1f}")


if __name__ == "__main__":
    main()
