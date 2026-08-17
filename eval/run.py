"""Evaluation harness: Arbiter vs a similarity-search baseline.

    python eval/run.py            # graph only, no API key needed
    python eval/run.py --model    # also render answers with the LLM

Scoring is deliberately generous to the baseline. Arbiter must produce a
correct *answer*; the baseline only has to *retrieve a document containing the
answer string* in its top-k — no extraction, no reasoning, no penalty for
picking the wrong one of several retrieved candidates. If the baseline still
loses, it is not because the comparison was rigged.

Abstention is scored as a first-class outcome:
  precision — of the questions it declined, how many were genuinely unanswerable
  recall    — of the genuinely unanswerable questions, how many it declined
A system that answers everything scores 0 on both, which is the point.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from answer.engine import Engine
from ingest.parse import load_corpus

QUESTIONS = Path(__file__).parent / "questions.yaml"
RESULTS = Path(__file__).parent / "results"
TOP_K = 5


# --- lexical (similarity search) baseline ------------------------------------


class LexicalBaseline:
    """TF-IDF retrieval over the same corpus — the 'just embed it' comparison.

    Lexical rather than dense so it runs with no API key. For these questions
    that favours the baseline if anything: the questions share vocabulary with
    the documents, which is exactly the case term matching handles best.
    """

    def __init__(self) -> None:
        self.docs = load_corpus()
        self.texts = [f"{d.title} {d.body} {d.author_raw} {d.doc_id}".lower() for d in self.docs]
        self.tokenized = [self._tokens(t) for t in self.texts]
        df: Counter = Counter()
        for toks in self.tokenized:
            df.update(set(toks))
        n = len(self.tokenized)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def search(self, query: str, k: int = TOP_K) -> list[int]:
        q = self._tokens(query)
        scores = []
        for i, toks in enumerate(self.tokenized):
            tf = Counter(toks)
            score = sum(tf[t] * self.idf.get(t, 0.0) for t in q)
            norm = math.sqrt(len(toks)) or 1
            scores.append((score / norm, i))
        return [i for s, i in sorted(scores, reverse=True)[:k] if s > 0]

    def would_find(self, query: str, expected: list[str]) -> bool:
        hits = self.search(query)
        blob = " ".join(self.texts[i] for i in hits)
        return all(e.lower() in blob for e in expected) if expected else False


# --- runner ------------------------------------------------------------------


def active_schema(explicit: str = ""):
    """Score against whatever ontology the graph was actually built with.

    After `cli.py init`, that is an induced schema rather than the hand-written
    one, and evaluating with the wrong vocabulary would measure nothing.
    """
    from graph.models import load_schema

    path = explicit
    if not path:
        try:
            state = json.loads((Path(__file__).resolve().parents[1] / ".arbiter" / "state.json").read_text())
            path = state.get("schema", "")
        except (OSError, ValueError):
            path = ""
    return load_schema(Path(path)) if path and Path(path).exists() else load_schema()


def run(use_model: bool = False, schema_path: str = "") -> dict:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    engine = Engine(schema=active_schema(schema_path))
    baseline = LexicalBaseline()

    # Questions answerable only from free text are skipped when the graph was
    # built without tier B — scoring them against a tier-A-only graph would
    # report a failure that is really a missing ingestion step.
    tier_b_claims = engine.client.query("MATCH (cl:Claim) WHERE cl.tier = $t RETURN count(*) AS c", {"t": "B"})
    has_tier_b = bool(tier_b_claims and tier_b_claims[0].get("c"))
    if not has_tier_b:
        skipped = [q["id"] for q in questions if q.get("requires_tier_b")]
        questions = [q for q in questions if not q.get("requires_tier_b")]
        print(f"tier B absent — skipping {len(skipped)} question(s): {', '.join(skipped)}")

    rows, latencies = [], []
    for q in questions:
        started = time.time()
        ans = engine.ask(q["question"], as_of=q.get("as_of", ""), use_model=use_model)
        latencies.append((time.time() - started) * 1000)

        expected_status = q["expect"]
        ok = ans.status == expected_status
        if ok and expected_status == "answered":
            ok = all(c.lower() in ans.text.lower() for c in q.get("contains", []))
        if ok and expected_status == "abstained" and q.get("gate"):
            ok = ans.gate == q["gate"]

        # The baseline never abstains, so on unanswerable questions it is
        # always wrong; on answerable ones it wins if the text is retrievable.
        base_ok = False if expected_status == "abstained" else baseline.would_find(
            q["question"], q.get("contains", [])
        )

        rows.append({
            "id": q["id"], "category": q["category"], "question": q["question"],
            "expected": expected_status, "got": ans.status, "gate": ans.gate,
            "ok": ok, "baseline_ok": base_ok, "answer": ans.text,
            "latency_ms": ans.latency_ms, "citations": len(ans.evidence),
        })

    return summarize(rows, latencies)


def summarize(rows: list[dict], latencies: list[float]) -> dict:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)

    tp = sum(1 for r in rows if r["expected"] == "abstained" and r["got"] == "abstained")
    fp = sum(1 for r in rows if r["expected"] == "answered" and r["got"] == "abstained")
    fn = sum(1 for r in rows if r["expected"] == "abstained" and r["got"] == "answered")
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0

    ordered = sorted(latencies)
    p50 = ordered[len(ordered) // 2] if ordered else 0
    p95 = ordered[int(len(ordered) * 0.95) - 1] if len(ordered) > 1 else p50

    print(f"\n{'category':<14}{'n':>4}{'arbiter':>10}{'baseline':>11}")
    print("-" * 39)
    for cat in ("lookup", "alias", "conflict", "unanswerable"):
        cat_rows = by_cat.get(cat, [])
        if not cat_rows:
            continue
        a = sum(r["ok"] for r in cat_rows) / len(cat_rows)
        b = sum(r["baseline_ok"] for r in cat_rows) / len(cat_rows)
        print(f"{cat:<14}{len(cat_rows):>4}{a:>9.0%}{b:>11.0%}")
    total_a = sum(r["ok"] for r in rows) / len(rows)
    total_b = sum(r["baseline_ok"] for r in rows) / len(rows)
    print("-" * 39)
    print(f"{'overall':<14}{len(rows):>4}{total_a:>9.0%}{total_b:>11.0%}")

    print(f"\nabstention   precision {precision:.0%}   recall {recall:.0%}   "
          f"(over-abstained {fp}, hallucination-risk {fn})")
    print(f"latency      p50 {p50:.0f} ms   p95 {p95:.0f} ms")

    failures = [r for r in rows if not r["ok"]]
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for r in failures:
            print(f"  [{r['id']}] {r['question']}")
            print(f"      expected {r['expected']}" + (f" via gate {r.get('gate')}" if r["expected"] == "abstained" else ""))
            print(f"      got      {r['got']}: {r['answer'][:110]}")

    result = {
        "overall": total_a, "baseline_overall": total_b,
        "by_category": {c: {"n": len(v), "arbiter": sum(r["ok"] for r in v) / len(v),
                            "baseline": sum(r["baseline_ok"] for r in v) / len(v)} for c, v in by_cat.items()},
        "abstention": {"precision": precision, "recall": recall, "over_abstained": fp, "hallucination_risk": fn},
        "latency_ms": {"p50": p50, "p95": p95},
        "rows": rows,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwritten to {(RESULTS / 'latest.json').relative_to(Path.cwd())}" if Path.cwd() in (RESULTS / "latest.json").parents else "")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run the Arbiter evaluation")
    ap.add_argument("--model", action="store_true", help="render answers with the LLM")
    ap.add_argument("--schema", default="", help="score against a specific ontology file")
    args = ap.parse_args()
    res = run(use_model=args.model, schema_path=args.schema)
    raise SystemExit(0 if res["overall"] == 1.0 else 1)
