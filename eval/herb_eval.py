"""Evaluate against HERB's own questions.

    python eval/herb_eval.py --products 1

Scores the graph on the dataset's ground truth rather than questions we wrote,
which is the point: our seed suite can be tuned to pass, HERB's cannot.

HERB's answerable questions ask for *sets* of employee ids ("find the authors
and key reviewers of the Market Research Report"), so they are scored as
retrieval — precision, recall and F1 over the employee ids present in the
retrieved evidence — rather than as a single rendered sentence. Unanswerable
questions are scored on whether the system declines, which is the behaviour no
similarity-search baseline can produce at all.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from answer.engine import Engine  # noqa: E402
from ingest.herb import product_files  # noqa: E402

KEY_RE = re.compile(r"person:(eid|emp)-([0-9a-fA-F]+)")
RESULTS = Path(__file__).parent / "results"


def ids_from_answer(ans) -> set[str]:
    """Employee ids appearing anywhere in the retrieved evidence."""
    found: set[str] = set()
    for ev in ans.evidence + ans.superseded + ans.contested:
        for key in (ev.claim.subject_key, ev.claim.object_key):
            m = KEY_RE.fullmatch(key or "")
            if m:
                prefix = "eid_" if m.group(1) == "eid" else "EMP_"
                found.add(prefix + m.group(2))
    return found


def load_questions(products: int | None) -> tuple[list[dict], list[dict]]:
    answerable, unanswerable = [], []
    for path in product_files()[: products or None]:
        data = json.loads(path.read_text(encoding="utf-8"))
        for q in data.get("answerable_questions", []):
            truth = {t for t in (q.get("ground_truth") or []) if isinstance(t, str) and t.startswith(("eid_", "EMP_"))}
            if truth:
                answerable.append({"question": q["question"], "truth": truth,
                                   "type": q.get("type", "?"), "product": path.stem})
        for q in data.get("unanswerable_questions", []):
            text = q if isinstance(q, str) else q.get("question", "")
            if text:
                unanswerable.append({"question": text, "product": path.stem})
    return answerable, unanswerable


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate against HERB's own ground truth")
    ap.add_argument("--products", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="cap questions per category")
    args = ap.parse_args()

    answerable, unanswerable = load_questions(args.products)
    if args.limit:
        answerable, unanswerable = answerable[: args.limit], unanswerable[: args.limit]
    engine = Engine()

    rows, by_type = [], defaultdict(list)
    for q in answerable:
        ans = engine.ask(q["question"], use_model=False)
        predicted = ids_from_answer(ans)
        hits = predicted & q["truth"]
        recall = len(hits) / len(q["truth"])
        precision = len(hits) / len(predicted) if predicted else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        row = {**q, "truth": sorted(q["truth"]), "predicted": len(predicted), "hits": len(hits),
               "recall": recall, "precision": precision, "f1": f1,
               "abstained": ans.abstained, "gate": ans.gate}
        rows.append(row)
        by_type[q["type"]].append(row)

    declined = 0
    unans_rows = []
    for q in unanswerable:
        ans = engine.ask(q["question"], use_model=False)
        declined += ans.abstained
        unans_rows.append({**q, "abstained": ans.abstained, "gate": ans.gate, "answer": ans.text[:120]})

    print(f"\nHERB evaluation — {args.products} product(s)")
    print(f"{len(answerable)} answerable, {len(unanswerable)} unanswerable\n")

    print(f"{'type':<14}{'n':>4}{'recall':>9}{'precision':>11}{'F1':>7}{'any-hit':>9}")
    print("-" * 54)
    for qtype, group in sorted(by_type.items()):
        n = len(group)
        print(f"{qtype:<14}{n:>4}{sum(r['recall'] for r in group) / n:>9.0%}"
              f"{sum(r['precision'] for r in group) / n:>11.0%}"
              f"{sum(r['f1'] for r in group) / n:>7.0%}"
              f"{sum(1 for r in group if r['hits']) / n:>9.0%}")
    if rows:
        n = len(rows)
        print("-" * 54)
        print(f"{'overall':<14}{n:>4}{sum(r['recall'] for r in rows) / n:>9.0%}"
              f"{sum(r['precision'] for r in rows) / n:>11.0%}"
              f"{sum(r['f1'] for r in rows) / n:>7.0%}"
              f"{sum(1 for r in rows if r['hits']) / n:>9.0%}")
        print(f"\nover-abstained on answerable: {sum(1 for r in rows if r['abstained'])}/{n}")

    if unanswerable:
        print(f"unanswerable declined:        {declined}/{len(unanswerable)} ({declined / len(unanswerable):.0%})")
        for r in unans_rows[:5]:
            mark = "ok " if r["abstained"] else "MISS"
            print(f"  [{mark}] {r['question'][:88]}")
            if not r["abstained"]:
                print(f"         answered: {r['answer']}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "herb.json").write_text(
        json.dumps({"answerable": rows, "unanswerable": unans_rows}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
