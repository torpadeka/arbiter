"""Pair scoring for entity resolution.

Returns both a score and the evidence behind it. The evidence is not
diagnostics — it is a product feature: the CLI shows *why* two surfaces were
judged the same person, which is what makes a merge auditable instead of
magic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz.distance import JaroWinkler

from resolve.normalize import (
    first_token,
    is_handle,
    is_initial,
    last_token,
    surface_tokens,
)

# Thresholds bound the LLM's involvement: it only sees the uncertain band.
AUTO_MERGE = 0.75
REJECT = 0.45


@dataclass
class Pair:
    score: float
    evidence: list[str] = field(default_factory=list)
    veto: str = ""

    @property
    def decision(self) -> str:
        if self.veto:
            return "reject"
        if self.score >= AUTO_MERGE:
            return "merge"
        if self.score < REJECT:
            return "reject"
        return "adjudicate"


def score_pair(
    a_surface: str,
    b_surface: str,
    a_email: str = "",
    b_email: str = "",
    shared_threads: int = 0,
    same_thread_distinct_speakers: bool = False,
) -> Pair:
    """Score two candidate identities.

    Vetoes are checked first and are absolute — no similarity score can
    override direct evidence that two surfaces are different people.
    """
    ta, tb = surface_tokens(a_surface), surface_tokens(b_surface)
    ev: list[str] = []

    # --- vetoes -----------------------------------------------------------
    if a_email and b_email and a_email.lower() != b_email.lower():
        return Pair(0.0, [f"distinct emails: {a_email} vs {b_email}"], veto="email_conflict")

    if same_thread_distinct_speakers:
        return Pair(0.0, ["spoke as distinct participants in one thread"], veto="distinct_speakers")

    # Different surnames, neither an initial -> different people, however
    # similar the first names are (Priya Nair vs Priya Nandakumar).
    la, lb = last_token(ta), last_token(tb)
    if len(ta) > 1 and len(tb) > 1 and la and lb and not is_initial(la) and not is_initial(lb):
        if la != lb and JaroWinkler.similarity(la, lb) < 0.92:
            return Pair(0.0, [f"different surnames: {la} vs {lb}"], veto="surname_conflict")

    # --- positive evidence -------------------------------------------------
    score = 0.0

    if a_email and b_email and a_email.lower() == b_email.lower():
        score += 1.0
        ev.append(f"same email: {a_email}")

    # One-sided email evidence: an address whose local part spells out the
    # other side's full name identifies them (priya.nandakumar@ == "Priya
    # Nandakumar"), even though only one side carries an address.
    for email, other_toks, other_surface in ((a_email, tb, b_surface), (b_email, ta, a_surface)):
        etoks = set(surface_tokens(email)) if email else set()
        if etoks and other_toks and set(other_toks) <= etoks and any(len(t) > 2 for t in other_toks):
            score += 0.6
            ev.append(f"email local part spells the name: {email.split('@')[0]} ~ {other_surface}")
            break

    if ta and tb and ta == tb:
        score += 0.8
        ev.append(f"identical normalized name: {' '.join(ta)}")
    elif la and lb and la == lb:
        # Same surname; check the given name or its initial.
        fa, fb = first_token(ta), first_token(tb)
        if fa == fb:
            score += 0.7
            ev.append(f"same surname and given name: {fa} {la}")
        elif is_initial(fa) or is_initial(fb):
            if fa[:1] == fb[:1]:
                score += 0.55
                ev.append(f"surname {la} with matching initial {fa[:1]}.")
        else:
            sim = JaroWinkler.similarity(fa, fb)
            if sim > 0.85:
                score += 0.45
                ev.append(f"surname {la}, given names similar ({sim:.2f})")

    # Handle forms abbreviate the surname to an initial: soham-r == Sam
    # Ratnaparkhi. Restricted to handles (no whitespace) on purpose — the same
    # leniency applied to written names would fuse "Priya N." with
    # "Priya Nandakumar" on no evidence beyond a shared first name.
    elif ta and tb and first_token(ta) == first_token(tb) and (is_handle(a_surface) or is_handle(b_surface)):
        short, long_ = (la, lb) if is_initial(la) else (lb, la)
        if is_initial(short) and long_.startswith(short):
            score += 0.55
            ev.append(f"handle abbreviates surname: {first_token(ta)} {short}. -> {long_}")

    # Token overlap catches handles: soham-r <-> @soham
    overlap = set(ta) & set(tb)
    strong_overlap = {t for t in overlap if len(t) > 2}
    if strong_overlap:
        gain = min(0.35, 0.18 * len(strong_overlap))
        score += gain
        ev.append(f"shared name tokens: {', '.join(sorted(strong_overlap))}")

    if ta and tb:
        sim = JaroWinkler.similarity(" ".join(ta), " ".join(tb))
        if sim > 0.90:
            score += 0.25
            ev.append(f"Jaro-Winkler {sim:.2f}")
        elif sim > 0.80:
            score += 0.10
            ev.append(f"Jaro-Winkler {sim:.2f}")

    # Weak corroboration only — never enough to merge on its own.
    if shared_threads:
        gain = min(0.10, 0.02 * shared_threads)
        score += gain
        ev.append(f"co-occurred in {shared_threads} thread(s)")

    return Pair(round(min(score, 1.0), 3), ev)
