"""Conflict arbitration: which of two contradictory claims is current.

The policy is deterministic, published, and tunable from ontology/schema.yaml.
No LLM decides what is true — a weighted score over five observable features
does, and every decision keeps the losing claim plus a reason string.

    score = 0.35*authority + 0.30*recency + 0.15*specificity
          + 0.15*corroboration - 0.05*hedging

Claims compete only within one (subject, predicate) and only for temporal
predicates. Claims agreeing on the same object corroborate instead.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph.models import Claim, Schema, load_schema, parse_ts

HEDGE_PENALTY_TIER = {"A": 0.0, "B": 0.0}  # hedging comes from text, set per claim


@dataclass
class Decision:
    winner: str            # claim key
    loser: str             # claim key
    relation: str          # SUPERSEDES | CONTRADICTS
    reason: str


def _recency_scores(claims: list[Claim]) -> dict[str, float]:
    """Rank within the competing set: newest 1.0, oldest 0.0, undated 0.0."""
    dated = [(c.key, parse_ts(c.asserted_at)) for c in claims]
    stamps = sorted({ts for _, ts in dated if ts})
    if not stamps:
        return {k: 0.0 for k, _ in dated}
    if len(stamps) == 1:
        return {k: (1.0 if ts else 0.0) for k, ts in dated}
    oldest, newest = stamps[0], stamps[-1]
    span = (newest - oldest).total_seconds() or 1.0
    return {k: ((ts - oldest).total_seconds() / span if ts else 0.0) for k, ts in dated}


def score_claims(claims: list[Claim], schema: Schema | None = None) -> None:
    """Assign `score` to every claim in one competing set, in place."""
    schema = schema or load_schema()
    w = schema.arbitration["weights"]
    recency = _recency_scores(claims)

    # Independent sources asserting the same object corroborate each other.
    by_object: dict[str, set[str]] = defaultdict(set)
    for c in claims:
        by_object[c.object_key].add(c.source_tool)

    for c in claims:
        c.corroboration = len(by_object[c.object_key])
        corroboration = min(1.0, (c.corroboration - 1) / 2.0)
        c.score = round(
            w["authority"] * c.authority
            + w["recency"] * recency.get(c.key, 0.0)
            + w["specificity"] * c.specificity
            + w["corroboration"] * corroboration
            + w["hedging"] * c.hedging,
            4,
        )


def arbitrate(claims: list[Claim], schema: Schema | None = None) -> list[Decision]:
    """Resolve every conflict in the claim set; mutates statuses in place."""
    schema = schema or load_schema()
    margin = float(schema.arbitration.get("contested_margin", 0.05))

    groups: dict[tuple[str, str], list[Claim]] = defaultdict(list)
    for c in claims:
        groups[(c.subject_key, c.predicate)].append(c)

    decisions: list[Decision] = []
    for (subject, predicate), group in groups.items():
        score_claims(group, schema)

        if not schema.is_temporal(predicate):
            continue  # additive predicate: AUTHORED, MENTIONS - never supersede
        if not schema.is_functional(predicate):
            continue  # multi-valued: two tickets, two accounts - not a conflict
        if len({c.object_key for c in group}) < 2:
            continue  # everyone agrees; nothing to arbitrate

        ranked = sorted(group, key=lambda c: (-c.score, c.asserted_at))
        winner = ranked[0]
        winner.status = "current"

        for loser in ranked[1:]:
            if loser.object_key == winner.object_key:
                loser.status = "current"  # same answer from another source
                continue
            gap = round(winner.score - loser.score, 4)
            if gap < margin:
                loser.status = "contested"
                relation = "CONTRADICTS"
                reason = (
                    f"contested: {winner.source_tool} {winner.score:.2f} vs "
                    f"{loser.source_tool} {loser.score:.2f} (gap {gap:.2f} < {margin})"
                )
            else:
                loser.status = "superseded"
                relation = "SUPERSEDES"
                reason = (
                    f"{winner.source_tool} ({winner.asserted_at[:10]}, authority "
                    f"{winner.authority:.2f}, score {winner.score:.2f}) beats "
                    f"{loser.source_tool} ({loser.asserted_at[:10]}, authority "
                    f"{loser.authority:.2f}, score {loser.score:.2f})"
                )
            decisions.append(Decision(winner.key, loser.key, relation, reason))

    return decisions


def as_of(claims: list[Claim], when: str) -> list[Claim]:
    """Re-arbitrate using only claims asserted on or before `when`.

    This is what makes "what did we believe in March?" answerable: nothing is
    deleted, so the past is still fully represented in the graph.
    """
    cutoff = parse_ts(when)
    if not cutoff:
        return claims
    visible = [c.model_copy(deep=True) for c in claims if (parse_ts(c.asserted_at) or cutoff) <= cutoff]
    for c in visible:
        c.status = "current"
    arbitrate(visible)
    return visible
