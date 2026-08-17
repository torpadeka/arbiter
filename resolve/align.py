"""Align free-text claims to the canonical ontology.

Tier A produces claims from structured fields, so its surfaces are already
canonical. Tier B reads prose, and prose does not speak in keys:

    (@priya) -OWNS-> (the Atlas migration)
    (the schema cutover piece of Atlas) -OWNED_BY-> (@soham)

Left alone, `the Atlas migration` becomes `project:the-atlas-migration` — a
second node for a project that already exists, which never meets the real one
in a competing set, so the contradiction is never detected. Entity resolution
handles this for people; this module is the equivalent for everything else,
plus the orientation rules that decide which way a claim should point.

Three passes, each conservative:
  1. surface  — strip determiners, then align to a known entity only on strong
                evidence. A wrong alignment fuses two real things, exactly like
                an over-merge in entity resolution.
  2. orientation — flip claims whose types violate domain/range but satisfy it
                reversed.
  3. functional form — rewrite a multi-valued predicate to its functional
                inverse (OWNS -> OWNED_BY) so conflicting claims compete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from graph.models import ExtractedClaim, Schema
from resolve.normalize import norm

DETERMINERS = {"the", "a", "an", "our", "this", "that", "these", "those", "my", "their", "its"}
FILLER_TAIL = re.compile(r"\b(piece|part|portion|side|bit)\s+of\b", re.I)

ALIGN_RATIO = 90        # token_set_ratio required to treat two surfaces as one thing
MIN_SHARED_TOKEN = 4    # and they must share a distinctive token this long


@dataclass
class AlignmentReport:
    aligned: list[tuple[str, str, str]] = field(default_factory=list)   # (surface, canonical, why)
    flipped: list[tuple[str, str]] = field(default_factory=list)        # (claim, why)
    unaligned: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)        # (claim, why)


def strip_determiners(surface: str) -> str:
    text = FILLER_TAIL.sub("", str(surface or ""))
    tokens = [t for t in text.split() if norm(t) not in DETERMINERS]
    return " ".join(tokens).strip() or str(surface or "").strip()


def _tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if t not in DETERMINERS}


def best_match(surface: str, node_type: str, known: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Match a surface to a known entity of the same type.

    `known` maps normalized name -> (display name, node type). Type must match:
    without it, "Acme Corp" (an Account) absorbs a Project-typed surface and
    "Atlas launch" lands on a ticket title, minting entities that are neither.
    """
    surface_tokens = _tokens(surface)
    if not surface_tokens:
        return None
    normalized = " ".join(sorted(surface_tokens))

    best: tuple[float, str, str, str] | None = None
    for known_norm, (display, known_type) in known.items():
        known_tokens = _tokens(known_norm)
        if not known_tokens:
            continue
        shared = {t for t in surface_tokens & known_tokens if len(t) >= MIN_SHARED_TOKEN}
        if not shared:
            continue
        if known_tokens <= surface_tokens:
            # "the Atlas migration" contains all of "atlas migration"
            score, why = 100.0, f"contains '{display}'"
        else:
            score = fuzz.token_set_ratio(normalized, " ".join(sorted(known_tokens)))
            why = f"token_set {score:.0f} with '{display}'"

        if known_type != node_type:
            # A structured field already established what this thing is, so its
            # type outranks the extractor's guess — but only on an exact
            # containment match, never on fuzzy similarity.
            if score < 100:
                continue
            why += f" (retyped {node_type} -> {known_type})"
        if score >= ALIGN_RATIO and (best is None or score > best[0] or (score == best[0] and known_type == node_type)):
            best = (score, display, why, known_type)
    return (best[1], best[2], best[3]) if best else None


def align(
    claims: list[ExtractedClaim],
    known_entities: dict[str, str],
    schema: Schema,
    report: AlignmentReport | None = None,
) -> list[ExtractedClaim]:
    """Canonicalize tier B claims. Tier A claims pass through untouched."""
    report = report or AlignmentReport()
    out: list[ExtractedClaim] = []

    for claim in claims:
        if claim.tier == "A":
            out.append(claim)
            continue

        c = claim.model_copy(deep=True)

        # --- 1. surfaces -------------------------------------------------
        for side in ("subject", "object"):
            surface = getattr(c, f"{side}_surface")
            node_type = getattr(c, f"{side}_type")
            cleaned = strip_determiners(surface)
            if node_type != "Person":
                match = best_match(cleaned, node_type, known_entities)
                if match:
                    canonical, why, matched_type = match
                    if norm(canonical) != norm(surface) or matched_type != node_type:
                        report.aligned.append((surface, canonical, why))
                    cleaned = canonical
                    setattr(c, f"{side}_type", matched_type)
                elif cleaned:
                    report.unaligned.append(f"{cleaned} ({node_type})")
            setattr(c, f"{side}_surface", cleaned)

        spec = schema.predicates.get(c.predicate)
        if not spec:
            out.append(c)  # UNMAPPED passes through for the alignment pass
            continue

        # --- 2. orientation ----------------------------------------------
        fits = c.subject_type in spec["domain"] and c.object_type in spec["range"]
        reversed_fits = c.object_type in spec["domain"] and c.subject_type in spec["range"]
        if not fits and reversed_fits:
            c = _swap(c)
            report.flipped.append((c.predicate, f"types matched only reversed ({c.subject_type} -> {c.object_type})"))
            fits = True

        if not fits:
            report.dropped.append((c.predicate, f"{c.subject_type} -> {c.object_type} violates domain/range"))
            continue

        # --- 3. same relation, different word ------------------------------
        remap = (spec.get("remap") or {}).get(c.subject_type)
        if remap and remap in schema.predicates:
            target = schema.predicates[remap]
            if c.subject_type in target["domain"] and c.object_type in target["range"]:
                report.flipped.append((f"{c.predicate} -> {remap}", f"said of a {c.subject_type}"))
                c.predicate = remap
                spec = target

        # --- 4. functional form -------------------------------------------
        inverse = spec.get("inverse")
        if inverse and not schema.is_functional(c.predicate) and schema.is_functional(inverse):
            inverse_spec = schema.predicates[inverse]
            if c.object_type in inverse_spec["domain"] and c.subject_type in inverse_spec["range"]:
                original = c.predicate
                c = _swap(c)
                c.predicate = inverse
                report.flipped.append((f"{original} -> {inverse}", "rewritten to the functional direction"))

        out.append(c)

    return out


def _swap(claim: ExtractedClaim) -> ExtractedClaim:
    c = claim.model_copy(deep=True)
    c.subject_surface, c.object_surface = claim.object_surface, claim.subject_surface
    c.subject_type, c.object_type = claim.object_type, claim.subject_type
    return c


def known_from_tier_a(
    claims: list[ExtractedClaim], docs_titles: dict[str, str] | None = None
) -> dict[str, tuple[str, str]]:
    """Canonical non-person surfaces already established by structured fields.

    Returns normalized name -> (display name, node type). Document titles are
    registered as Artifacts, so a prose mention of a ticket title aligns to the
    ticket and never to a project of the same name.
    """
    known: dict[str, tuple[str, str]] = {}
    for c in claims:
        if c.tier != "A":
            continue
        for surface, node_type in ((c.subject_surface, c.subject_type), (c.object_surface, c.object_type)):
            if node_type in {"Person", "Artifact"} or not surface:
                continue
            known.setdefault(norm(surface), (surface, node_type))
    # Documents are matched on their title but aligned to their *key*: the
    # loader resolves an Artifact surface by key, so returning the title would
    # mint a second artifact named after the first.
    for key, title in (docs_titles or {}).items():
        known.setdefault(norm(title), (key, "Artifact"))
    return known
