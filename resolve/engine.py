"""Entity resolution: surfaces -> canonical entities, with stored evidence.

Pipeline: collect mentions -> build candidates -> block -> score pairs ->
apply vetoes -> union-find -> pick a canonical name -> keep every alias and
the reason each merge happened.

Negative evidence is what separates this from fuzzy matching. Three vetoes,
each absolute:
  email_conflict     two surfaces with different known emails
  surname_conflict   two full names with different surnames
  addressed_other    one surface addresses the other by name in its own text
                     (people do not thank themselves)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from graph.models import ExtractedClaim, RawDoc, person_key, slugify
from resolve.features import Pair, score_pair
from resolve.normalize import blocking_keys, is_email, is_handle, is_initial, norm, surface_tokens


@dataclass(frozen=True)
class CandidateKey:
    surface_norm: str
    email: str

    def __str__(self) -> str:
        return f"{self.surface_norm}|{self.email}" if self.email else self.surface_norm


@dataclass
class Candidate:
    key: CandidateKey
    surfaces: set[str] = field(default_factory=set)
    email: str = ""
    threads: set[str] = field(default_factory=set)
    docs: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    mentions: int = 0

    @property
    def display(self) -> str:
        """The most informative observed surface form."""
        return max(self.surfaces, key=lambda s: (len(surface_tokens(s)) > 1, not is_email(s), len(s)))


@dataclass
class Cluster:
    canonical: str
    key: str
    surfaces: list[str]
    emails: list[str]
    tools: list[str]
    mention_count: int
    evidence: list[str]

    def alias_surfaces(self) -> list[str]:
        return [s for s in self.surfaces if norm(s) != norm(self.canonical)]


@dataclass
class Resolution:
    clusters: list[Cluster]
    by_surface: dict[str, list[Cluster]]
    merges: list[tuple[str, str, Pair]]
    vetoes: list[tuple[str, str, Pair]]
    adjudicate: list[tuple[str, str, Pair]]

    def lookup(self, surface: str, email: str = "") -> Cluster | None:
        """Resolve a surface to its cluster; email disambiguates shared names."""
        options = self.by_surface.get(norm(surface)) or []
        if not options:
            return None
        if len(options) == 1:
            return options[0]
        if email:
            for cluster in options:
                if email.lower() in [e.lower() for e in cluster.emails]:
                    return cluster
        return max(options, key=lambda c: c.mention_count)


# --- mention collection -----------------------------------------------------


def collect_candidates(docs: list[RawDoc], claims: list[ExtractedClaim]) -> dict[CandidateKey, Candidate]:
    """Every observed person surface, keyed by (normalized surface, email)."""
    cands: dict[CandidateKey, Candidate] = {}

    def add(surface: str, email: str, tool: str, doc_key: str, thread: str) -> None:
        surface = (surface or "").strip()
        if not surface:
            return
        if is_email(surface) and not email:
            email = surface
        key = CandidateKey(norm(surface), (email or "").lower())
        cand = cands.setdefault(key, Candidate(key=key, email=(email or "").lower()))
        cand.surfaces.add(surface)
        cand.tools.add(tool)
        cand.docs.add(doc_key)
        cand.mentions += 1
        if thread:
            cand.threads.add(thread)

    doc_by_key = {d.key: d for d in docs}
    for doc in docs:
        thread = f"{doc.tool}:{doc.title}" if doc.tool == "slack" else doc.key
        add(doc.author_raw, doc.author_email, doc.tool, doc.key, thread)
        for participant in doc.participants_raw:
            add(participant, participant if is_email(participant) else "", doc.tool, doc.key, thread)

    for claim in claims:
        doc = doc_by_key.get(f"artifact:{claim.source_tool}:{slugify(claim.source_doc_id)}")
        thread = (f"{doc.tool}:{doc.title}" if doc and doc.tool == "slack" else (doc.key if doc else ""))
        if claim.subject_type == "Person":
            add(claim.subject_surface, "", claim.source_tool, doc.key if doc else "", thread)
        if claim.object_type == "Person":
            add(claim.object_surface, "", claim.source_tool, doc.key if doc else "", thread)
    return cands


def addressed_pairs(docs: list[RawDoc]) -> set[tuple[str, str]]:
    """Normalized surface pairs where one addresses the other in its own text.

    Direct evidence of distinctness: an author naming someone else in a
    document they wrote cannot be that person.
    """
    known: set[str] = set()
    for doc in docs:
        for surface in [doc.author_raw, *doc.participants_raw]:
            for tok in surface_tokens(surface):
                if len(tok) > 2 and not is_initial(tok):
                    known.add(tok)

    out: set[tuple[str, str]] = set()
    for doc in docs:
        if not doc.author_raw or not doc.body:
            continue
        # Identity includes the email's tokens: someone signing "Nair here"
        # from priya.nair@ is naming themselves, not addressing another person.
        author_toks = set(surface_tokens(doc.author_raw)) | set(surface_tokens(doc.author_email))
        body_toks = set(norm(doc.body).split())
        for tok in body_toks & known:
            if tok in author_toks:
                continue  # self-reference
            out.add((norm(doc.author_raw), tok))
    return out


# --- resolution -------------------------------------------------------------


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def resolve_people(docs: list[RawDoc], claims: list[ExtractedClaim]) -> Resolution:
    cands = collect_candidates(docs, claims)
    addressed = addressed_pairs(docs)

    # Blocking: only compare candidates that share a cheap key.
    buckets: dict[str, list[CandidateKey]] = defaultdict(list)
    for key, cand in cands.items():
        for bkey in blocking_keys(cand.display, cand.email):
            buckets[bkey].append(key)

    pairs: dict[tuple[str, str], Pair] = {}
    for members in buckets.values():
        if len(members) < 2 or len(members) > 200:  # skip degenerate buckets
            continue
        for a, b in combinations(sorted(set(members), key=str), 2):
            ck = (str(a), str(b))
            if ck in pairs:
                continue
            ca, cb = cands[a], cands[b]
            distinct = any(
                (norm(sa), tok) in addressed
                for sa in ca.surfaces
                for tok in surface_tokens(cb.display)
                if len(tok) > 2
            ) or any(
                (norm(sb), tok) in addressed
                for sb in cb.surfaces
                for tok in surface_tokens(ca.display)
                if len(tok) > 2
            )
            pairs[ck] = score_pair(
                ca.display, cb.display, ca.email, cb.email,
                shared_threads=len(ca.threads & cb.threads),
                same_thread_distinct_speakers=distinct,
            )

    uf = _UnionFind()
    for key in cands:
        uf.find(str(key))

    # Pass 1 — collect every veto before a single union happens. Vetoes score
    # 0.0, so processing in score order would apply them last, after the
    # merges they were meant to prevent had already fused the clusters.
    blocked: set[frozenset[str]] = set()
    vetoes = []
    for (a, b), pair in pairs.items():
        if pair.veto:
            vetoes.append((a, b, pair))
            blocked.add(frozenset({a, b}))

    # Pass 2 — merge highest-confidence pairs first. A veto between *any* two
    # members blocks the union of their whole clusters, so distinctness
    # evidence propagates transitively.
    members: dict[str, set[str]] = {str(k): {str(k)} for k in cands}
    merges, adjudicate = [], []
    for (a, b), pair in sorted(pairs.items(), key=lambda kv: -kv[1].score):
        if pair.veto or pair.decision == "reject":
            continue
        if pair.decision == "adjudicate":
            adjudicate.append((a, b, pair))
            continue
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            continue
        if any(frozenset({x, y}) in blocked for x in members[ra] for y in members[rb]):
            continue
        uf.union(a, b)
        root, absorbed = (ra, rb) if uf.find(a) == ra else (rb, ra)
        members[root] |= members.pop(absorbed)
        merges.append((a, b, pair))

    # Build clusters.
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for key, cand in cands.items():
        grouped[uf.find(str(key))].append(cand)

    # Evidence is user-facing, so keep only merges that actually joined two
    # different surface forms, and only the strongest reason for each.
    evidence_by_root: dict[str, list[str]] = defaultdict(list)
    for a, b, pair in merges:
        left, right = a.split("|")[0], b.split("|")[0]
        if left == right or not pair.evidence:
            continue
        line = f"{left} = {right}: {pair.evidence[0]}"
        root = uf.find(a)
        if line not in evidence_by_root[root]:
            evidence_by_root[root].append(line)

    clusters: list[Cluster] = []
    for root, members in grouped.items():
        # Prefer a written full name over an initial, an email, or a handle:
        # "Wei Chen" reads better than "wei-chen" or "wei.chen@northwind.com".
        canonical = max(
            (s for m in members for s in m.surfaces),
            key=lambda s: (
                len(surface_tokens(s)) > 1,
                not any(is_initial(t) for t in surface_tokens(s)),
                not is_email(s),
                not is_handle(s),
                len(s),
            ),
        )
        clusters.append(
            Cluster(
                canonical=canonical,
                key=person_key(canonical),
                surfaces=sorted({s for m in members for s in m.surfaces}),
                emails=sorted({m.email for m in members if m.email}),
                tools=sorted({t for m in members for t in m.tools}),
                mention_count=sum(m.mentions for m in members),
                evidence=evidence_by_root.get(root, []),
            )
        )

    by_surface: dict[str, list[Cluster]] = defaultdict(list)
    for cluster in clusters:
        for surface in cluster.surfaces:
            by_surface[norm(surface)].append(cluster)

    return Resolution(
        clusters=sorted(clusters, key=lambda c: -c.mention_count),
        by_surface=dict(by_surface),
        merges=merges,
        vetoes=vetoes,
        adjudicate=adjudicate,
    )
