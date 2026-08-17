"""Query planning: natural language -> entities, predicate class, time constraint.

Deterministic on purpose. The planner decides *where to look*, and if an LLM
decided that, an unanswerable question could be "answered" by the planner
wandering to a different entity. Keeping it mechanical means the abstention
gates in engine.py test something real: the graph either has a claim covering
the asked-about predicate, or it does not.

It also means the whole read path runs with no API key — only the final
natural-language rendering needs a model, and the CLI can skip that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rapidfuzz import fuzz

from graph.hydra import HydraClient
from graph.models import Schema, load_schema
from resolve.normalize import norm

# Question phrasing -> candidate predicates, most specific first. Multi-word
# keys are checked as phrases so "reports to" doesn't fire on a bare "report".
PREDICATE_HINTS: list[tuple[str, list[str]]] = [
    ("reports to", ["REPORTS_TO"]),
    ("report to", ["REPORTS_TO"]),
    ("manager", ["REPORTS_TO"]),
    ("assigned", ["ASSIGNED_TO"]),
    ("assignee", ["ASSIGNED_TO"]),
    ("owns", ["OWNED_BY", "OWNS"]),
    ("owner", ["OWNED_BY", "OWNS"]),
    ("responsible", ["OWNED_BY", "ASSIGNED_TO"]),
    ("in charge", ["OWNED_BY", "ASSIGNED_TO"]),
    ("carrying", ["OWNED_BY", "ASSIGNED_TO"]),
    ("budget", ["BUDGET_IS"]),
    ("cost", ["BUDGET_IS"]),
    ("status", ["STATUS_IS"]),
    ("state of", ["STATUS_IS"]),
    ("due", ["DUE_ON", "SCHEDULED_FOR"]),
    ("deadline", ["DUE_ON", "SCHEDULED_FOR"]),
    ("launch", ["SCHEDULED_FOR", "DUE_ON"]),
    ("ship", ["SCHEDULED_FOR", "DUE_ON"]),
    ("when", ["SCHEDULED_FOR", "DUE_ON"]),
    ("blocking", ["BLOCKS"]),
    ("blocked", ["BLOCKS"]),
    ("blocks", ["BLOCKS"]),
    ("depends", ["DEPENDS_ON"]),
    ("priority", ["PRIORITY_IS"]),
    ("risk", ["RISK_OF"]),
    ("customer", ["CUSTOMER_OF"]),
    ("works on", ["WORKS_ON"]),
    ("working on", ["WORKS_ON"]),
    ("work on", ["WORKS_ON"]),
    ("member", ["MEMBER_OF"]),
    ("wrote", ["AUTHORED"]),
    ("author", ["AUTHORED"]),
    ("reviewed", ["REVIEWED_BY"]),
    ("reviewer", ["REVIEWED_BY"]),
    ("approved", ["REVIEWED_BY"]),
    ("mentioned", ["MENTIONS"]),
    ("discussed", ["DISCUSSED_IN", "MENTIONS"]),
    ("involved", ["MENTIONS", "MEMBER_OF"]),
    ("team member", ["MEMBER_OF"]),
    ("worked on", ["WORKS_ON", "MEMBER_OF"]),
    ("escalat", ["ESCALATED_TO"]),
    ("decided", ["DECIDED"]),
    ("resolved", ["RESOLVED_BY"]),
]

ENTITY_LABELS = ["Person", "Project", "Account", "Team", "Decision", "Artifact", "Topic"]
STOPWORDS = {
    # grammar
    "who", "what", "when", "where", "which", "whom", "whose", "the", "is", "are", "was",
    "were", "of", "on", "for", "to", "in", "at", "a", "an", "does", "do", "did", "and",
    "or", "s", "it", "that", "how", "much", "many", "currently", "now", "by", "with",
    "from", "this", "these", "those", "there", "be", "been", "has", "have", "had",
    # predicate words, already consumed as hints
    "owns", "own", "assigned", "status", "due", "author", "authors", "reviewer",
    "reviewers", "reviewed", "reports", "report", "member", "members", "team",
    # question framing and meta-vocabulary — asking *for* something, not *about* it
    "find", "list", "show", "give", "tell", "provide", "identify", "please", "all",
    "any", "employee", "employees", "id", "ids", "identifier", "identifiers", "name",
    "names", "person", "people", "product", "products", "key", "main", "primary",
    "related", "associated", "involved", "during", "about", "regarding", "their",
    # generic names for the artifacts themselves, not the thing being asked
    "pull", "request", "pr", "ticket", "issue", "document", "doc", "file", "page",
    "thread", "channel", "message", "meeting", "deal", "record", "number", "item",
}


@dataclass
class Entity:
    id: int
    key: str
    name: str
    label: str
    matched_via: str = ""
    matched_text: str = ""  # the surface in the question that matched this entity

    @property
    def kind(self) -> str:
        return self.key.split(":", 1)[0]


# Phrasings that ask for a *set* of people rather than one fact. These change
# retrieval shape: "who is ENG-4471 assigned to" wants one answer, "find the
# employee IDs of the reviewers" wants everyone who qualifies.
SET_CUES = (
    "ids", "employee id", "employees", "who are", "list", "find ", "all ",
    "members", "reviewers", "authors", "everyone", "people", "which people",
    "team members", "participants",
)


@dataclass
class QueryPlan:
    question: str
    entities: list[Entity] = field(default_factory=list)
    predicates: list[str] = field(default_factory=list)
    as_of: str = ""
    unmatched_terms: list[str] = field(default_factory=list)
    set_mode: bool = False

    @property
    def anchor(self) -> Entity | None:
        return self.entities[0] if self.entities else None


class EntityIndex:
    """Names and aliases of everything in the graph, for query-time matching."""

    def __init__(self, client: HydraClient) -> None:
        self.entities: list[Entity] = []
        for label in ENTITY_LABELS:
            rows = client.query(f"MATCH (n:{label}) RETURN n.id AS id, n.key AS key, n.name AS name")
            for r in rows:
                if r.get("key"):
                    self.entities.append(Entity(id=r["id"], key=r["key"], name=r.get("name") or r["key"], label=label))

        # Aliases resolve to the person they belong to, so a question asking
        # about "@soham" finds Sam without the asker knowing they are the same.
        self.alias_targets: dict[str, Entity] = {}
        by_id = {e.id: e for e in self.entities}
        for row in client.query("MATCH (a:Alias)-[:ALIAS_OF]->(p) RETURN a.name AS alias, p.id AS pid"):
            target = by_id.get(row.get("pid"))
            if target and row.get("alias"):
                self.alias_targets[norm(row["alias"])] = target

    def match(self, question: str, limit: int = 4) -> tuple[list[Entity], list[str]]:
        """Entities mentioned in the question, best first, plus unmatched terms."""
        q = norm(question)
        q_tokens = [t for t in q.split() if t not in STOPWORDS and len(t) > 1]
        scored: dict[str, tuple[float, Entity]] = {}

        # Longest alias wins: an email contains a first name, and crediting the
        # short one leaves the rest of the address looking unexplained.
        for surface, target in sorted(self.alias_targets.items(), key=lambda kv: len(kv[0])):
            if surface and surface in q:
                scored[target.key] = (
                    1.0,
                    Entity(**{**target.__dict__, "matched_via": f"alias '{surface}'", "matched_text": surface}),
                )

        for ent in self.entities:
            name = norm(ent.name)
            # Both tails matter: 'artifact:jira:eng-4471' should match a question
            # asking about "ENG-4471" as well as one naming the tool path.
            candidates = {name, norm(ent.key.split(":", 1)[-1]), norm(ent.key.rsplit(":", 1)[-1])}
            best, via, text = 0.0, "", ""
            for candidate in candidates:
                if not candidate:
                    continue
                if candidate in q:
                    # Longer literal matches win: "atlas migration" over "atlas".
                    score = 0.9 + min(0.09, len(candidate) / 200)
                    if score > best:
                        best, via, text = score, f"name '{ent.name}'", candidate
                    continue
                cand_tokens = [t for t in candidate.split() if t not in STOPWORDS]
                if cand_tokens and all(t in q_tokens for t in cand_tokens):
                    if 0.8 > best:
                        best, via, text = 0.8, f"tokens of '{ent.name}'", candidate
                    continue
                ratio = fuzz.token_set_ratio(candidate, " ".join(q_tokens)) / 100
                if ratio > 0.9 and ratio > best:
                    best, via, text = ratio * 0.75, f"fuzzy '{ent.name}' ({ratio:.2f})", candidate
            if best:
                prior = scored.get(ent.key)
                if not prior or best > prior[0]:
                    scored[ent.key] = (best, Entity(**{**ent.__dict__, "matched_via": via, "matched_text": text}))

        ranked = [e for _, e in sorted(scored.values(), key=lambda kv: -kv[0])][:limit]

        # Tokens are "explained" by any matched entity's name or its key tail,
        # so a question naming a ticket as "ENG-4471" counts as covered even
        # though the entity's display name is its title.
        matched_tokens: set[str] = set()
        for e in ranked:
            matched_tokens |= set(norm(e.name).split())
            matched_tokens |= set(norm(e.key).split())
            # The surface that actually matched counts as explained: a question
            # asking about "@soham" is fully accounted for even though the
            # entity's canonical name is "Sam Ratnaparkhi".
            matched_tokens |= set(norm(e.matched_text).split())
        unmatched = [t for t in q_tokens if t not in matched_tokens and not _is_hint_token(t)]
        return ranked, unmatched


def _is_hint_token(token: str) -> bool:
    return any(token in phrase for phrase, _ in PREDICATE_HINTS)


def detect_predicates(question: str, schema: Schema) -> list[str]:
    q = norm(question)
    out: list[str] = []
    for phrase, predicates in PREDICATE_HINTS:
        if phrase in q:
            for p in predicates:
                if p in schema.predicates and p not in out:
                    out.append(p)
    return out


AS_OF_RE = re.compile(r"as of ([0-9]{4}-[0-9]{2}-[0-9]{2}|[a-z]+ [0-9]{1,2},? [0-9]{4})", re.I)


def detect_as_of(question: str) -> str:
    m = AS_OF_RE.search(question)
    if not m:
        return ""
    raw = m.group(1)
    for fmt in ("%Y-%m-%d", "%B %d %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw.replace(",", ""), fmt.replace(",", "")).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return ""


def plan(question: str, index: EntityIndex, schema: Schema | None = None, as_of: str = "") -> QueryPlan:
    schema = schema or load_schema()
    entities, unmatched = index.match(question)
    lowered = question.lower()
    return QueryPlan(
        question=question,
        entities=entities,
        predicates=detect_predicates(question, schema),
        as_of=as_of or detect_as_of(question),
        unmatched_terms=unmatched,
        set_mode=any(cue in lowered for cue in SET_CUES),
    )
