"""Salesforce HERB reader.

    python -m ingest.herb --fetch            # download the dataset (~29 MB)
    python -m ingest.herb --products 3       # inspect what 3 products yield

HERB ships one JSON per product containing every artifact for that product,
plus company-wide metadata. This module explodes it into the same RawDoc
envelope every other source uses, so entity resolution, arbitration, gates and
the CLI work unchanged.

Two structural differences from the tool corpus drive the design:

* **People are already identifiers.** Slack, documents and transcripts use
  `eid_13fdff84`; GitHub PRs use a different scheme (`EMP_615921487`). The
  resolution problem here is cross-system identity linking rather than name
  spelling, so eids are kept as the canonical key — which is also the form
  HERB's own ground-truth answers use — and human names ride along as a
  property.
* **The org chart is structured HR data.** `salesforce_team.json` is a real
  hierarchy, so REPORTS_TO is deterministic tier A rather than something an LLM
  has to infer from prose.

Sampling is by whole product, never by random document: products are the unit
that keeps a team, its threads, its contradictions and its questions together.
Random document sampling would shred exactly the structure being tested.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from graph.models import ExtractedClaim, RawDoc, Schema, load_schema
from ingest.parse import apply_rules, parse_record

HERB_DIR = Path(__file__).resolve().parents[1] / "data" / "herb"
BASE_URL = "https://huggingface.co/datasets/Salesforce/HERB/resolve/main"
METADATA = ["employee.json", "salesforce_team.json", "customers_data.json"]

EID_RE = re.compile(r"\b(?:eid_[0-9a-f]{6,}|EMP_[0-9]{6,})\b")


# --- fetching ---------------------------------------------------------------


def product_files() -> list[Path]:
    return sorted((HERB_DIR / "products").glob("*.json")) or sorted(HERB_DIR.glob("*Force*.json")) or sorted(
        p for p in HERB_DIR.glob("*.json") if p.name not in METADATA
    )


def fetch(products: int | None = None) -> None:
    """Download metadata and product files with curl."""
    (HERB_DIR / "products").mkdir(parents=True, exist_ok=True)
    names = _remote_product_names()
    if products:
        names = names[:products]
    targets = [(f"{BASE_URL}/metadata/{m}", HERB_DIR / m) for m in METADATA]
    targets += [(f"{BASE_URL}/products/{n}", HERB_DIR / "products" / n) for n in names]

    for url, dest in targets:
        if dest.exists() and dest.stat().st_size > 0:
            continue
        print(f"  fetching {dest.name}")
        subprocess.run(["curl", "-sSL", "-o", str(dest), url], check=True, timeout=300)


def _remote_product_names() -> list[str]:
    import httpx

    resp = httpx.get(f"https://huggingface.co/api/datasets/Salesforce/HERB/tree/main/products", timeout=60)
    resp.raise_for_status()
    return sorted(item["path"].split("/")[-1] for item in resp.json() if item.get("type") == "file")


# --- metadata ---------------------------------------------------------------


def load_employees() -> dict[str, dict]:
    path = HERB_DIR / "employee.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def org_claims(schema: Schema) -> list[ExtractedClaim]:
    """REPORTS_TO straight from the org chart — no inference required."""
    path = HERB_DIR / "salesforce_team.json"
    if not path.exists():
        return []
    trees = json.loads(path.read_text(encoding="utf-8"))
    claims: list[ExtractedClaim] = []

    def walk(node: dict) -> None:
        manager = node.get("employee_id")
        for key in ("engineering_leads", "engineers", "reports", "team"):
            for report in node.get(key) or []:
                if not isinstance(report, dict):
                    continue
                if report.get("employee_id") and manager:
                    claims.append(
                        ExtractedClaim(
                            subject_surface=report["employee_id"],
                            subject_type="Person",
                            predicate="REPORTS_TO",
                            object_surface=manager,
                            object_type="Person",
                            source_tool="herb_org",
                            source_doc_id="salesforce_team",
                            asserted_at="",
                            evidence_span=f"org chart: {report.get('role', 'report')} under {node.get('role', 'manager')}",
                            tier="A",
                        )
                    )
                walk(report)

    for tree in trees if isinstance(trees, list) else [trees]:
        walk(tree)
    return claims


# --- products ---------------------------------------------------------------


def _slack_records(product: dict) -> list[dict]:
    """Flatten each message and its thread replies into one record.

    Reply *authors* are carried through as participants, not just their text:
    the people answering in a thread are exactly who HERB's questions mean by
    "key reviewers", and folding replies into the body alone would discard them.
    """
    out = []
    for entry in product.get("slack", []):
        message = (entry.get("Message") or {}).get("User") or {}
        replies = entry.get("ThreadReplies") or []
        reply_text, repliers = [], []
        for reply in replies:
            user = (reply.get("User") or {}) if isinstance(reply, dict) else {}
            reply_text.append(user.get("text") or (reply if isinstance(reply, str) else ""))
            if user.get("userId"):
                repliers.append(user["userId"])
        record = dict(entry)
        record["Message"] = {
            "User": {**message, "text": " ".join([message.get("text", ""), *reply_text]).strip()}
        }
        record["participants"] = repliers
        out.append(record)
    return out


DOC_REF_RE = re.compile(r"/docs/([a-z0-9_\-]+)", re.I)


def _cross_references(docs: list[RawDoc]) -> list[ExtractedClaim]:
    """Artifact-to-artifact citations.

    HERB threads link documents by id inside Slack-style URLs
    (`<https://.../docs/onforcex_market_research_report|Market Research Report>`).
    Those links are the only path from a document to the people who discussed
    it, so without them "who reviewed this report" is unanswerable.
    """
    by_doc_id = {d.doc_id: d for d in docs}

    # HERB has no thread structure: ThreadReplies is empty everywhere, and a
    # conversation is instead a run of top-level messages sharing a channel and
    # a date (ids 20260611-0-…, 20260611-1-…). Grouping them is what turns "who
    # reviewed this document" into a reachable question — the people discussing
    # it are the siblings of whoever posted the link.
    conversations: dict[tuple[str, str], list[RawDoc]] = {}
    for doc in docs:
        if doc.tool == "herb_slack":
            conversations.setdefault((doc.title, doc.created_at[:10]), []).append(doc)

    def reference(source: RawDoc, target: RawDoc, why: str) -> ExtractedClaim:
        return ExtractedClaim(
            subject_surface=source.key, subject_type="Artifact",
            predicate="REFERENCES", object_surface=target.key, object_type="Artifact",
            source_tool=source.tool, source_doc_id=source.doc_id,
            asserted_at=source.created_at, evidence_span=why, tier="A",
        )

    claims: list[ExtractedClaim] = []
    for doc in docs:
        text = f"{doc.title} {doc.body}"
        for token in set(DOC_REF_RE.findall(text)):
            target = by_doc_id.get(token)
            if not target or target.key == doc.key:
                continue
            claims.append(reference(doc, target, f"links to {target.title or target.doc_id}"))
            # The whole conversation is about the document, not just the message
            # carrying the URL.
            for sibling in conversations.get((doc.title, doc.created_at[:10]), []):
                if sibling.key != doc.key:
                    claims.append(
                        reference(sibling, target, f"same conversation as the message sharing {target.doc_id}")
                    )
    return claims


SECTIONS = {
    "herb_slack": _slack_records,
    "herb_doc": lambda p: p.get("documents", []),
    "herb_transcript": lambda p: p.get("meeting_transcripts", []),
    "herb_chat": lambda p: p.get("meeting_chats", []),
    "herb_url": lambda p: p.get("urls", []),
    "herb_pr": lambda p: p.get("prs", []),
}


def read_product(path: Path, schema: Schema) -> tuple[list[RawDoc], list[ExtractedClaim]]:
    product = json.loads(path.read_text(encoding="utf-8"))
    name = path.stem
    docs: list[RawDoc] = []
    claims: list[ExtractedClaim] = []

    for tool, extract in SECTIONS.items():
        for record in extract(product) or []:
            if not isinstance(record, dict) or not record.get("id"):
                continue
            doc = parse_record(tool, record, schema)
            docs.append(doc)
            claims.extend(apply_rules(doc, schema))
            claims.extend(_reference_claims(doc, record, name))

    # The product itself is the thing everything here is about.
    for doc in docs:
        claims.append(
            ExtractedClaim(
                subject_surface=name,
                subject_type="Project",
                predicate="DISCUSSED_IN",
                object_surface=doc.key,
                object_type="Artifact",
                source_tool=doc.tool,
                source_doc_id=doc.doc_id,
                asserted_at=doc.created_at,
                evidence_span=f"{name} artifact",
                tier="A",
            )
        )
    claims.extend(_cross_references(docs))
    claims.extend(_team_claims(product, name))
    return docs, claims


def _reference_claims(doc: RawDoc, record: dict, product: str) -> list[ExtractedClaim]:
    """Employee ids named in text, plus participants and PR reviewers.

    These are what make multi-hop possible: HERB's own questions ask for the
    employee ids behind a document, and those ids appear as mentions rather
    than as fields.
    """
    claims: list[ExtractedClaim] = []

    def mention(eid: str, predicate: str = "MENTIONS", evidence: str = "") -> None:
        claims.append(
            ExtractedClaim(
                subject_surface=doc.key,
                subject_type="Artifact",
                predicate=predicate,
                object_surface=eid,
                object_type="Person",
                source_tool=doc.tool,
                source_doc_id=doc.doc_id,
                asserted_at=doc.created_at,
                evidence_span=evidence or f"referenced in {doc.tool}",
                tier="A",
            )
        )

    body = f"{doc.title} {doc.body}"
    for eid in dict.fromkeys(EID_RE.findall(body)):
        if eid != doc.author_raw:
            mention(eid, evidence=f"named in {doc.tool} text")

    for participant in record.get("participants") or []:
        if isinstance(participant, str) and EID_RE.fullmatch(participant):
            mention(participant, evidence="meeting participant")

    for review in record.get("reviews") or []:
        login = ((review or {}).get("user") or {}).get("login")
        if login:
            claims.append(
                ExtractedClaim(
                    subject_surface=doc.key,
                    subject_type="Artifact",
                    predicate="REVIEWED_BY",
                    object_surface=login,
                    object_type="Person",
                    source_tool=doc.tool,
                    source_doc_id=doc.doc_id,
                    asserted_at=review.get("submitted_at", doc.created_at),
                    evidence_span=f"PR review: {review.get('state', '')}",
                    tier="A",
                )
            )
    return claims


def _team_claims(product: dict, name: str) -> list[ExtractedClaim]:
    claims = []
    for eid in product.get("team") or []:
        if isinstance(eid, str):
            claims.append(
                ExtractedClaim(
                    subject_surface=eid, subject_type="Person",
                    predicate="MEMBER_OF", object_surface=name, object_type="Project",
                    source_tool="herb_org", source_doc_id=f"team:{name}",
                    evidence_span=f"listed on the {name} team", tier="A",
                )
            )
    return claims


# --- entry point ------------------------------------------------------------


def load_herb(products: int | None = None, schema: Schema | None = None, verbose: bool = True):
    """Returns (docs, claims, person_names)."""
    schema = schema or load_schema()
    files = product_files()
    if not files:
        raise SystemExit("no HERB products found — run: python -m ingest.herb --fetch")
    if products:
        files = files[:products]

    employees = load_employees()
    docs: list[RawDoc] = []
    claims: list[ExtractedClaim] = list(org_claims(schema))

    for path in files:
        product_docs, product_claims = read_product(path, schema)
        docs.extend(product_docs)
        claims.extend(product_claims)
        if verbose:
            print(f"  {path.stem:<22} {len(product_docs):>5} docs  {len(product_claims):>6} claims")

    person_names = {eid: info.get("name", "") for eid, info in employees.items()}
    if verbose:
        surfaces = {c.subject_surface for c in claims if c.subject_type == "Person"}
        surfaces |= {c.object_surface for c in claims if c.object_type == "Person"}
        eids = {s for s in surfaces if s.startswith("eid_")}
        emps = {s for s in surfaces if s.startswith("EMP_")}
        print(f"\n  {len(files)} product(s), {len(docs)} documents, {len(claims)} tier-A claims")
        print(f"  identities: {len(eids)} eid_* ({len(eids & set(employees))} in employee.json), {len(emps)} EMP_*")
    return docs, claims, person_names


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    ap = argparse.ArgumentParser(description="Read the Salesforce HERB dataset")
    ap.add_argument("--fetch", action="store_true", help="download the dataset first")
    ap.add_argument("--products", type=int, default=None, help="how many products to read")
    args = ap.parse_args()

    if args.fetch:
        fetch(args.products)
    docs, claims, names = load_herb(args.products)

    by_pred: dict[str, int] = {}
    for c in claims:
        by_pred[c.predicate] = by_pred.get(c.predicate, 0) + 1
    print("\n  claims by predicate")
    for pred, n in sorted(by_pred.items(), key=lambda kv: -kv[1]):
        print(f"    {pred:<14} {n:>6}")
