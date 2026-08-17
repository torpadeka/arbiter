"""Print the entity-resolution result for the current corpus.

    python scripts/show_resolution.py

Shows each canonical person, the aliases folded into it, the evidence for
each merge, and every veto that blocked one. The vetoes matter as much as the
merges: over-merging two real people is the worst failure mode this system
has, and silent over-merges look exactly like success.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.parse import load_corpus, tier_a_claims  # noqa: E402
from resolve.engine import resolve_people  # noqa: E402

if __name__ == "__main__":
    docs = load_corpus()
    claims = tier_a_claims(docs)
    res = resolve_people(docs, claims)

    print(f"{len(docs)} documents, {len(claims)} tier-A claims\n")
    print(f"=== {len(res.clusters)} canonical entities ===")
    for c in res.clusters:
        aliases = c.alias_surfaces()
        print(f"\n  {c.canonical}   [{c.key}]")
        print(f"    mentions : {c.mention_count} across {', '.join(c.tools)}")
        if c.emails:
            print(f"    emails   : {', '.join(c.emails)}")
        if aliases:
            print(f"    aliases  : {', '.join(aliases)}")
        for line in c.evidence:
            print(f"    evidence : {line}")

    print(f"\n=== {len(res.vetoes)} vetoed merges (over-merge prevented) ===")
    for a, b, pair in res.vetoes:
        print(f"  {a.split('|')[0]:<22} != {b.split('|')[0]:<22} [{pair.veto}] {pair.evidence[0] if pair.evidence else ''}")

    if res.adjudicate:
        print(f"\n=== {len(res.adjudicate)} uncertain pairs (LLM adjudication band) ===")
        for a, b, pair in res.adjudicate:
            print(f"  {a.split('|')[0]:<22} ?= {b.split('|')[0]:<22} score={pair.score} {'; '.join(pair.evidence)}")
