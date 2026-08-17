"""The read path: traverse -> gate -> arbitrate -> generate -> verify.

Order matters. Every gate runs *before* the model is called, so an unanswerable
question costs no tokens and cannot be answered by a confident guess. The model
only ever sees claims that were already retrieved from the graph, and whatever
it writes is checked back against them.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from arbiter.policy import arbitrate
from graph.hydra import HydraClient
from graph.models import Claim, Schema, load_schema, node_id, parse_ts
from answer.plan import Entity, EntityIndex, QueryPlan, plan
from llm import LLM, available as llm_available

# Predicate names are written for the graph; these read as English in answers
# and abstention messages ("nothing recorded about its budget", not "budget is").
PREDICATE_PHRASE = {
    "BUDGET_IS": "budget",
    "STATUS_IS": "status",
    "DUE_ON": "due date",
    "SCHEDULED_FOR": "scheduled date",
    "PRIORITY_IS": "priority",
    "REPORTS_TO": "reporting line",
    "ASSIGNED_TO": "assignee",
    "OWNED_BY": "owner",
    "RESOLVED_BY": "resolver",
    "REVIEWED_BY": "reviewer",
    "RISK_OF": "risks",
    "BLOCKS": "blockers",
    "DEPENDS_ON": "dependencies",
    "MEMBER_OF": "membership",
    "CUSTOMER_OF": "customer relationship",
    "WORKS_ON": "work",
    "AUTHORED": "authorship",
    "DISCUSSED_IN": "discussion",
}


def phrase(predicate: str) -> str:
    return PREDICATE_PHRASE.get(predicate, predicate.replace("_", " ").lower())


CLAIM_FIELDS = (
    "cl.key AS key, cl.predicate AS predicate, cl.subject_key AS subject_key, "
    "cl.object_key AS object_key, cl.object_literal AS object_literal, "
    "cl.source_tool AS source_tool, cl.source_artifact AS source_artifact, "
    "cl.asserted_at AS asserted_at, cl.status AS status, cl.score AS score, "
    "cl.authority AS authority, cl.evidence_span AS evidence_span, cl.tier AS tier, "
    "cl.corroboration AS corroboration"
)


@dataclass
class Evidence:
    claim: Claim
    hops: int
    via: str = ""          # how we arrived: the predicate traversed
    from_key: str = ""     # entity we came from

    @property
    def other_key(self) -> str:
        return self.claim.object_key if self.claim.subject_key == self.from_key else self.claim.subject_key


@dataclass
class Answer:
    question: str
    status: str                     # answered | abstained
    text: str
    gate: str = ""                  # entity | coverage | sufficiency | verification
    evidence: list[Evidence] = field(default_factory=list)
    superseded: list[Evidence] = field(default_factory=list)
    contested: list[Evidence] = field(default_factory=list)
    path: list[tuple[str, str, str]] = field(default_factory=list)  # (from, predicate, to)
    entities: list[Entity] = field(default_factory=list)
    predicates: list[str] = field(default_factory=list)
    as_of: str = ""
    grounded_by_model: bool = False
    latency_ms: int = 0
    labels: dict[str, str] = field(default_factory=dict)  # entity key -> display name

    @property
    def abstained(self) -> bool:
        return self.status == "abstained"


def _row_to_claim(row: dict) -> Claim:
    return Claim(
        key=row["key"],
        predicate=row.get("predicate") or "",
        subject_key=row.get("subject_key") or "",
        object_key=row.get("object_key") or "",
        object_literal=row.get("object_literal") or "",
        source_artifact_key=row.get("source_artifact") or "",
        source_tool=row.get("source_tool") or "",
        asserted_at=row.get("asserted_at") or "",
        authority=float(row.get("authority") or 0.5),
        score=float(row.get("score") or 0.0),
        corroboration=int(row.get("corroboration") or 1),
        status=row.get("status") or "current",
        evidence_span=row.get("evidence_span") or "",
        tier=row.get("tier") or "A",
    )


class Engine:
    def __init__(self, client: HydraClient | None = None, schema: Schema | None = None) -> None:
        self.client = client or HydraClient()
        self.schema = schema or load_schema()
        self.index = EntityIndex(self.client)
        self.names = {e.key: e.name for e in self.index.entities}

    # --- retrieval ---------------------------------------------------------

    def claims_touching(self, entity_key: str) -> list[Claim]:
        """Every claim with this entity on either side."""
        nid = node_id(entity_key)
        rows = []
        for edge in ("ABOUT", "OBJECT"):
            rows += self.client.query(
                f"MATCH (cl:Claim)-[:{edge}]->(e {{id: $id}}) RETURN {CLAIM_FIELDS}", {"id": nid}
            )
        seen, claims = set(), []
        for r in rows:
            if r["key"] not in seen:
                seen.add(r["key"])
                claims.append(_row_to_claim(r))
        return claims

    def gather(self, plan_: QueryPlan, max_hops: int = 3) -> tuple[list[Evidence], list[Evidence]]:
        """Breadth-first walk from the anchor, collecting claims.

        Returns (hits, seen) — hits are claims matching the asked-about
        predicate, seen is everything encountered (used to explain abstention).
        """
        if not plan_.anchor:
            return [], []

        # Every matched entity is an anchor, not just the best-scoring one. A
        # question naming both a document and its product ("the authors of the
        # Market Research Report for ActionGenie") has its answer on the
        # document; anchoring only on the product finds nothing and abstains.
        targets = set(plan_.predicates)
        # Asking for a *set* of people includes those named in the discussion,
        # not only those in an authorship field: "key reviewers" are usually
        # mentioned rather than recorded as a reviewer anywhere.
        if plan_.set_mode and any(
            "Person" in (self.schema.predicates.get(t, {}).get("range") or []) for t in targets
        ):
            targets.add("MENTIONS")
        frontier = [(e.key, "") for e in plan_.entities]
        visited: set[str] = set()
        hits: list[Evidence] = []
        seen: list[Evidence] = []

        for hop in range(max_hops):
            next_frontier: list[tuple[str, str]] = []
            for entity_key, arrived_via in frontier:
                if entity_key in visited:
                    continue
                visited.add(entity_key)
                for claim in self.claims_touching(entity_key):
                    ev = Evidence(claim=claim, hops=hop, from_key=entity_key, via=arrived_via)
                    seen.append(ev)
                    # A predicate-targeted question is about *this* entity. A
                    # matching claim found one hop away belongs to someone else:
                    # "who does Wei report to" must not answer with Sam's
                    # manager just because Sam is adjacent. Absence here is a
                    # real answer — that is what gate 2 is for.
                    if not targets:
                        hits.append(ev)
                    elif hop == 0 and claim.predicate in targets:
                        hits.append(ev)
                    other = ev.other_key
                    if other and other not in visited:
                        next_frontier.append((other, claim.predicate))
            if hits and not plan_.set_mode:
                break  # nearest evidence wins; don't wander further
            if targets:
                break  # no claim about this entity covers the question
            frontier = next_frontier
            if not frontier:
                break

        if plan_.set_mode and targets:
            hits.extend(self.gather_referencing(plan_, targets, visited))
        return hits, seen

    def gather_referencing(self, plan_: QueryPlan, targets: set[str], visited: set[str]) -> list[Evidence]:
        """People reached through artifacts that cite an anchor document.

        A question like "the authors and key reviewers of the Market Research
        Report" is answered by everyone in the conversation that cites the
        report, not just by the document's own author field. Restricted to
        REFERENCES so this widens along an explicit citation edge rather than
        drifting across the whole product.
        """
        out: list[Evidence] = []
        for entity in plan_.entities:
            if not entity.key.startswith("artifact:"):
                continue
            rows = self.client.query(
                "MATCH (a)-[:REFERENCES]->(b {id: $id}) RETURN a.key AS key LIMIT 200",
                {"id": node_id(entity.key)},
            )
            for row in rows:
                citing = row.get("key")
                if not citing or citing in visited:
                    continue
                visited.add(citing)
                for claim in self.claims_touching(citing):
                    if claim.predicate in targets:
                        out.append(Evidence(claim=claim, hops=1, from_key=citing, via="REFERENCES"))
        return out

    # --- arbitration + as-of ----------------------------------------------

    def resolve_conflicts(
        self, hits: list[Evidence], as_of: str = ""
    ) -> tuple[list[Evidence], list[Evidence], list[Evidence]]:
        """Split evidence into current / superseded / contested.

        With `as_of`, claims asserted after the cutoff are dropped and the
        remainder is re-arbitrated — nothing was deleted at write time, so the
        past is still fully answerable.
        """
        visible = hits
        if as_of:
            cutoff = parse_ts(as_of)
            visible = [e for e in hits if not (parse_ts(e.claim.asserted_at) or cutoff) > cutoff]
            claims = [e.claim.model_copy(deep=True) for e in visible]
            for c in claims:
                c.status = "current"
            arbitrate(claims, self.schema)
            by_key = {c.key: c for c in claims}
            visible = [Evidence(by_key[e.claim.key], e.hops, e.via, e.from_key) for e in visible if e.claim.key in by_key]

        current = [e for e in visible if e.claim.status == "current"]
        superseded = [e for e in visible if e.claim.status == "superseded"]
        contested = [e for e in visible if e.claim.status == "contested"]
        return current, superseded, contested

    # --- gates -------------------------------------------------------------

    def label(self, key: str) -> str:
        if not key:
            return "?"
        if key in self.names:
            return self.names[key]
        tail = key.split(":", 1)[-1]
        # Don't de-slug values that are meant to keep their hyphens (dates, ids).
        return tail if any(ch.isdigit() for ch in tail) else tail.replace("-", " ")

    def ask(self, question: str, as_of: str = "", max_hops: int = 3, use_model: bool = True) -> Answer:
        started = time.time()
        p = plan(question, self.index, self.schema, as_of=as_of)
        answer = Answer(
            question=question, status="abstained", text="", entities=p.entities,
            predicates=p.predicates, as_of=p.as_of,
        )

        # Gate 1 — do the things being asked about exist at all?
        if not p.anchor:
            unknown = " ".join(p.unmatched_terms[:4])
            subject = f'"{unknown}"' if unknown else "the subject of that question"
            answer.gate = "entity"
            answer.text = f"Not in the data: no record of {subject} in the corpus."
            answer.latency_ms = int((time.time() - started) * 1000)
            return answer

        # A question whose wording maps to no predicate in the vocabulary is one
        # this system cannot claim to answer. Returning whatever claims happen to
        # touch the entity produces confident nonsense — "who shared competitor
        # demos?" answered with "Fiona Brown — member of → ActionGenie".
        if not p.predicates:
            answer.gate = "coverage"
            answer.text = (
                f"Not in the data: {p.anchor.name} is in the corpus, but the question does not map "
                "to any recorded relationship."
            )
            answer.latency_ms = int((time.time() - started) * 1000)
            return answer

        # The question may name an entity and a predicate we know, yet still ask
        # about something the corpus never records ("team members who shared
        # demos of competitor products"). Answering the part we recognize and
        # ignoring the rest is how a confident wrong answer gets made.
        if len(p.unmatched_terms) >= 2:
            unknown = ", ".join(list(dict.fromkeys(p.unmatched_terms))[:4])
            answer.gate = "coverage"
            answer.text = (
                f"Not in the data: found {p.anchor.name}, but nothing recorded about "
                f"{unknown}."
            )
            answer.latency_ms = int((time.time() - started) * 1000)
            return answer

        hits, seen = self.gather(p, max_hops=max_hops)
        current, superseded, contested = self.resolve_conflicts(hits, p.as_of)

        # Gate 2 — is the asked-about predicate covered for those entities?
        if not current:
            anchor = p.anchor.name
            if p.predicates:
                # Only what touches the anchor directly — listing predicates
                # found three hops away would misrepresent what is known here.
                direct = sorted({e.claim.predicate for e in seen if e.hops == 0})
                extra = (
                    f" Recorded for {anchor}: {', '.join(phrase(k) for k in direct[:6])}."
                    if direct else ""
                )
                answer.text = (
                    f"Not in the data: found {anchor}, but no "
                    f"{phrase(p.predicates[0])} is recorded.{extra}"
                )
            elif superseded:
                answer.text = f"Not in the data as of {p.as_of[:10]}: every statement about {anchor} was asserted later."
            else:
                answer.text = f"Not in the data: {anchor} exists in the corpus but has no recorded claims."
            answer.gate = "coverage"
            answer.superseded = superseded
            answer.latency_ms = int((time.time() - started) * 1000)
            return answer

        current.sort(key=lambda e: (e.hops, -e.claim.score))
        top = current[0]

        # Gate 3 — is the best evidence strong enough to stand behind?
        threshold = float(self.schema.retrieval.get("sufficiency_threshold", 0.35))
        if top.claim.score < threshold:
            answer.gate = "sufficiency"
            answer.text = (
                f"Not in the data with sufficient confidence: the only statement is "
                f"an unconfirmed mention in {top.claim.source_tool} "
                f"({top.claim.asserted_at[:10]}, score {top.claim.score:.2f} < {threshold})."
            )
            answer.evidence = current
            answer.superseded = superseded
            answer.latency_ms = int((time.time() - started) * 1000)
            return answer

        answer.status = "answered"
        answer.labels = {
            key: self.label(key)
            for ev in current + superseded + contested
            for key in (ev.claim.subject_key, ev.claim.object_key)
            if key
        }
        answer.evidence = current
        answer.superseded = superseded
        answer.contested = contested
        answer.path = self.build_path(p.anchor.key, top)
        answer.text = self.render(p, current)

        if use_model and llm_available():
            grounded = self.generate(p, current, superseded)
            if grounded:
                answer.text, answer.grounded_by_model = grounded, True

        answer.latency_ms = int((time.time() - started) * 1000)
        return answer

    def build_path(self, anchor_key: str, ev: Evidence) -> list[tuple[str, str, str]]:
        subject, obj = ev.claim.subject_key, ev.claim.object_key
        hop = (self.label(subject), ev.claim.predicate, self.label(obj or ev.claim.object_literal))
        if ev.from_key and ev.from_key not in (subject, obj):
            return [(self.label(anchor_key), ev.via or "…", self.label(ev.from_key)), hop]
        return [hop]

    # --- generation --------------------------------------------------------

    def render(self, p: QueryPlan, current: list[Evidence]) -> str:
        """Deterministic answer, used when no model is configured.

        Keeps the read path fully functional without an API key — the graph,
        not the model, is what produces the answer.
        """
        top = current[0]
        subject = self.label(top.claim.subject_key)
        obj = self.label(top.claim.object_key) or top.claim.object_literal
        predicate = top.claim.predicate.replace("_", " ").lower()
        line = f"{subject} : {predicate} : {obj}"
        # Corroborating sources agree on the object; listing it again reads as a
        # second answer rather than as extra support for the same one.
        others = []
        for ev in current[1:]:
            other = self.label(ev.claim.object_key) or ev.claim.object_literal
            if other and other != obj and other not in others:
                others.append(other)
        if others:
            line += f"  (also: {', '.join(others[:3])})"
        elif len(current) > 1:
            line += f"  [{len(current)} sources agree]"
        return line

    def generate(self, p: QueryPlan, current: list[Evidence], superseded: list[Evidence]) -> str:
        """Natural-language rendering, strictly grounded in retrieved claims."""

        def fmt(ev: Evidence) -> str:
            c = ev.claim
            return (
                f"[{c.key}] {self.label(c.subject_key)} {c.predicate} "
                f"{self.label(c.object_key) or c.object_literal} "
                f"(source: {c.source_tool} {c.source_artifact_key}, asserted {c.asserted_at[:10]}, "
                f"status {c.status}, score {c.score:.2f})"
                + (f' evidence: "{c.evidence_span[:160]}"' if c.evidence_span else "")
            )

        context = "\n".join(fmt(e) for e in current[:12])
        if superseded:
            context += "\n\nSUPERSEDED (do not state as current; mention only if the question is about history):\n"
            context += "\n".join(fmt(e) for e in superseded[:6])

        system = (
            "You answer questions about a company using ONLY the claims provided. Rules:\n"
            "1. Use only the claims given. Never add outside knowledge or inference.\n"
            "2. Cite the claim id in square brackets after each fact, exactly as given.\n"
            "3. If the claims do not answer the question, say so plainly.\n"
            "4. Two or three sentences at most. Lead with the answer.\n"
            "5. If a superseded claim contradicts the current one, note in one clause "
            "that the earlier statement was superseded."
        )
        client = LLM(model_env="ANSWER_MODEL")
        try:
            text = client.text(system, f"Question: {p.question}\n\nCLAIMS:\n{context}")
        finally:
            client.close()
        # An unverifiable rendering is discarded, not shown: the deterministic
        # answer is already correct, so a model failure must never downgrade it.
        return text if self.verify(text, current + superseded) else ""

    # --- verification ------------------------------------------------------

    @staticmethod
    def verify(text: str, evidence: list[Evidence]) -> bool:
        """Every cited claim id must be one we actually retrieved.

        A citation the graph never produced means the model invented support,
        so the generated text is discarded and the deterministic rendering
        stands instead.
        """
        import re

        cited = set(re.findall(r"\[(claim:[0-9a-f]+)\]", text))
        if not cited:
            return False
        known = {e.claim.key for e in evidence}
        return cited.issubset(known)
