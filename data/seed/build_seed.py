"""Build the seed corpus: nine tools, native record shapes, planted ground truth.

    python data/seed/build_seed.py

Writes data/raw/<tool>.jsonl. Deterministic — no randomness, no clock reads —
so eval expectations stay stable across runs.

The real corpus (Salesforce HERB / EnterpriseRAG-Bench) plugs in behind the
same parsers. This exists so entity resolution, arbitration, abstention and
the eval harness can be built and measured today, against facts we control.

PLANTED GROUND TRUTH
--------------------
Aliases (must merge):
  Sam Ratnaparkhi  <- "Sam", "@soham", "S. Ratnaparkhi", "soham-r", sam.ratnaparkhi@northwind.com
  Priya Nair       <- "Priya", "@priya", priya.nair@northwind.com

Over-merge trap (must NOT merge):
  Priya Nair vs Priya Nandakumar — same first name, both appear as "Priya N."
  in slack C-atlas thread ts 1772... with different user ids and emails.

Conflict A — who owns the Atlas migration:
  slack  Mar 02 (authority 0.50): Priya Nair says she is taking it
  jira   Apr 11 (authority 1.00): assignee = S. Ratnaparkhi
  => winner: Sam Ratnaparkhi (higher authority AND later). Slack claim kept,
     status=superseded, reachable via ASSIGNED_TO_SUPERSEDED.

Conflict B — Atlas launch date:
  confluence Feb 18 (0.80): "launches May 3"
  slack      Apr 04 (0.50, hedged "i think maybe"): June 12
  linear     Jun 01 (1.00): due 2026-07-12
  => winner: 2026-07-12. As-of 2026-03-01 must instead answer May 3.

Multi-hop (3 hops, crosses an alias):
  Acme Corp -CUSTOMER_OF-> Atlas Migration -ASSIGNED_TO- Sam -REPORTS_TO-> Dana Okafor
  "Who does the owner of the Atlas migration report to?" => Dana Okafor

Unanswerable (must abstain):
  - Atlas budget: no BUDGET_IS claim exists anywhere in the corpus.
  - "Project Zephyr": no such entity -> gate 1.
  - Wei Chen's manager: never stated -> gate 2.

Near-duplicates: drive file-101 and file-102 are the same runbook, v1 and v2.
"""

from __future__ import annotations

import json
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "raw"

SLACK = [
    # Conflict A, losing side: Priya claims Atlas on Mar 2.
    {"ts": "1772449200.000100", "ts_iso": "2026-03-02T09:00:00Z", "channel": "eng-atlas",
     "user": "@priya", "user_email": "priya.nair@northwind.com",
     "text": "I'm taking the Atlas migration - will own it through launch."},
    {"ts": "1772449800.000200", "ts_iso": "2026-03-02T09:10:00Z", "channel": "eng-atlas",
     "user": "@soham", "user_email": "sam.ratnaparkhi@northwind.com",
     "text": "Thanks Priya. I'll pick up the schema cutover piece of Atlas."},
    # Over-merge trap: two different Priyas, same thread, different identities.
    {"ts": "1772450400.000300", "ts_iso": "2026-03-02T09:20:00Z", "channel": "eng-atlas",
     "user": "Priya N.", "user_email": "priya.nandakumar@northwind.com",
     "text": "Priya N. here from Security - we'll need a review before cutover."},
    {"ts": "1772450700.000400", "ts_iso": "2026-03-02T09:25:00Z", "channel": "eng-atlas",
     "user": "Priya N.", "user_email": "priya.nair@northwind.com",
     "text": "Two Priyas on one thread, sorry - Nair here, I meant the migration itself."},
    # Conflict B, hedged middle claim.
    {"ts": "1775304000.000500", "ts_iso": "2026-04-04T12:00:00Z", "channel": "eng-atlas",
     "user": "Sam", "user_email": "sam.ratnaparkhi@northwind.com",
     "text": "i think maybe we slip Atlas launch to June 12, not sure yet"},
    {"ts": "1776600000.000600", "ts_iso": "2026-04-19T12:00:00Z", "channel": "eng-atlas",
     "user": "@wei", "user_email": "wei.chen@northwind.com",
     "text": "Beacon is blocking Atlas on the connector rewrite."},
    {"ts": "1779192000.000700", "ts_iso": "2026-05-19T12:00:00Z", "channel": "eng-atlas",
     "user": "@wei", "user_email": "wei.chen@northwind.com",
     "text": "Connector rewrite merged - Beacon no longer blocks Atlas."},
    {"ts": "1779364800.000800", "ts_iso": "2026-05-21T12:00:00Z", "channel": "sales-acme",
     "user": "@wei", "user_email": "wei.chen@northwind.com",
     "text": "Acme Corp asked about Atlas timelines again on the QBR."},
    {"ts": "1780574400.000900", "ts_iso": "2026-06-04T12:00:00Z", "channel": "eng-atlas",
     "user": "S. Ratnaparkhi", "user_email": "sam.ratnaparkhi@northwind.com",
     "text": "Cutover runbook v2 is up on Drive, superseding v1."},
]

GMAIL = [
    {"message_id": "msg-9001", "subject": "Atlas migration kickoff",
     "date": "2026-02-20T08:00:00Z", "from": "dana.okafor@northwind.com",
     "to": ["sam.ratnaparkhi@northwind.com", "priya.nair@northwind.com"],
     "body": "Kicking off Atlas. Sam and Priya, you two are the core pair. Report status to me weekly."},
    {"message_id": "msg-9002", "subject": "Re: Atlas migration kickoff",
     "date": "2026-02-20T09:30:00Z", "from": "sam.ratnaparkhi@northwind.com",
     "to": ["dana.okafor@northwind.com"],
     "body": "Understood - I report to you on this. Will send Friday updates."},
    {"message_id": "msg-9003", "subject": "Acme renewal + Atlas dependency",
     "date": "2026-05-22T15:00:00Z", "from": "wei.chen@northwind.com",
     "to": ["dana.okafor@northwind.com"],
     "body": "Acme Corp renewal hinges on Atlas shipping. Vertex Labs is not affected."},
]

JIRA = [
    # Conflict A, winning side: Jira assignee updated Apr 11.
    {"key": "ENG-4471", "fields": {
        "summary": "Atlas migration - schema cutover",
        "description": "Cut over the legacy schema to Atlas. Owner tracked here.",
        "created": "2026-02-18T10:00:00Z", "updated": "2026-04-11T09:22:00Z",
        "reporter": {"displayName": "Priya Nair"},
        "assignee": {"displayName": "S. Ratnaparkhi"},
        "project": {"name": "Atlas Migration", "key": "ATLAS"},
        "status": {"name": "In Progress"}, "duedate": None}},
    {"key": "ENG-4482", "fields": {
        "summary": "Beacon connector rewrite",
        "description": "Rewrite the connector; Atlas is blocked until this lands.",
        "created": "2026-04-19T10:00:00Z", "updated": "2026-05-19T12:30:00Z",
        "reporter": {"displayName": "Wei Chen"},
        "assignee": {"displayName": "Wei Chen"},
        "project": {"name": "Beacon", "key": "BCN"},
        "status": {"name": "Done"}, "duedate": "2026-05-19"}},
    {"key": "ENG-4500", "fields": {
        "summary": "Atlas security review",
        "description": "Security review ahead of cutover.",
        "created": "2026-03-03T10:00:00Z", "updated": "2026-06-10T10:00:00Z",
        "reporter": {"displayName": "Priya Nandakumar"},
        "assignee": {"displayName": "Priya Nandakumar"},
        "project": {"name": "Atlas Migration", "key": "ATLAS"},
        "status": {"name": "In Review"}, "duedate": None}},
]

LINEAR = [
    # Conflict B, losing side: the original milestone, set Feb 18 (May 3 launch).
    # Structured on both sides, so the conflict is arbitrable from tier A alone.
    {"identifier": "ATL-9", "title": "Atlas launch (original plan)", "description": "Initial launch milestone.",
     "createdAt": "2026-02-18T10:00:00Z", "updatedAt": "2026-02-18T10:00:00Z",
     "creator": {"name": "Priya Nair"}, "assignee": {"name": "Priya Nair"},
     "project": {"name": "Atlas Migration"}, "state": {"name": "Superseded"},
     "team": {"key": "PLATFORM"}, "dueDate": "2026-05-03"},
    # Conflict B, winning side: due date reset Jun 1.
    {"identifier": "ATL-12", "title": "Atlas launch", "description": "Ship Atlas to all tenants.",
     "createdAt": "2026-02-18T10:00:00Z", "updatedAt": "2026-06-01T08:00:00Z",
     "creator": {"name": "Dana Okafor"}, "assignee": {"name": "Sam Ratnaparkhi"},
     "project": {"name": "Atlas Migration"}, "state": {"name": "In Progress"},
     "team": {"key": "PLATFORM"}, "dueDate": "2026-07-12"},
    {"identifier": "BCN-4", "title": "Connector rewrite", "description": "Unblocks Atlas.",
     "createdAt": "2026-04-19T10:00:00Z", "updatedAt": "2026-05-19T12:00:00Z",
     "creator": {"name": "Wei Chen"}, "assignee": {"name": "Wei Chen"},
     "project": {"name": "Beacon"}, "state": {"name": "Done"},
     "team": {"key": "PLATFORM"}, "dueDate": "2026-05-19"},
]

GITHUB = [
    {"number": 812, "title": "Atlas schema cutover", "body": "Implements the cutover for Atlas Migration.",
     "created_at": "2026-04-02T10:00:00Z", "merged_at": "2026-04-10T18:00:00Z",
     "user": {"login": "soham-r"}, "reviewer": {"login": "wei-chen"}, "repo": "northwind/atlas"},
    {"number": 830, "title": "Connector rewrite", "body": "Beacon connector rewrite. Unblocks Atlas.",
     "created_at": "2026-04-20T10:00:00Z", "merged_at": "2026-05-19T11:00:00Z",
     "user": {"login": "wei-chen"}, "reviewer": {"login": "soham-r"}, "repo": "northwind/beacon"},
]

CONFLUENCE = [
    # Conflict B, oldest claim: May 3 launch.
    {"id": "conf-501", "title": "Atlas Migration Plan", "space": "ENG",
     "created": "2026-02-18T10:00:00Z", "updated": "2026-02-18T10:00:00Z",
     "author": "Priya Nair",
     "body": "Atlas launches May 3, 2026. Core pair is Sam and Priya. Dana Okafor sponsors."},
    {"id": "conf-540", "title": "Atlas Cutover Runbook", "space": "ENG",
     "created": "2026-03-10T10:00:00Z", "updated": "2026-06-04T10:00:00Z",
     "author": "S. Ratnaparkhi",
     "body": "Runbook for the Atlas cutover. Escalate to Dana Okafor if the cutover exceeds the window."},
]

DRIVE = [
    {"file_id": "file-101", "name": "atlas-runbook-v1.md", "drive_id": "eng-shared",
     "createdTime": "2026-03-10T10:00:00Z", "modifiedTime": "2026-03-10T10:00:00Z",
     "owner": "Sam Ratnaparkhi",
     "content": "Atlas cutover runbook v1. Step 1 freeze writes. Step 2 copy schema. Step 3 verify."},
    # Near-duplicate of v1, one step changed.
    {"file_id": "file-102", "name": "atlas-runbook-v2.md", "drive_id": "eng-shared",
     "createdTime": "2026-06-04T10:00:00Z", "modifiedTime": "2026-06-04T10:00:00Z",
     "owner": "S. Ratnaparkhi",
     "content": "Atlas cutover runbook v2. Step 1 freeze writes. Step 2 copy schema. Step 3 verify and page Dana."},
]

HUBSPOT = [
    {"deal_id": "deal-77", "dealname": "Acme Corp renewal", "company": "Acme Corp",
     "product": "Atlas Migration", "owner": "Wei Chen", "pipeline": "enterprise",
     "createdate": "2026-01-15T10:00:00Z", "hs_lastmodifieddate": "2026-05-22T10:00:00Z",
     "notes": "Renewal depends on Atlas shipping. Escalation path runs through Dana Okafor."},
    {"deal_id": "deal-91", "dealname": "Vertex Labs expansion", "company": "Vertex Labs",
     "product": "Beacon", "owner": "Wei Chen", "pipeline": "enterprise",
     "createdate": "2026-03-01T10:00:00Z", "hs_lastmodifieddate": "2026-04-02T10:00:00Z",
     "notes": "Expansion tied to Beacon connector work."},
]

FIREFLIES = [
    {"meeting_id": "ff-31", "title": "Atlas weekly", "date": "2026-04-11T16:00:00Z",
     "organizer": "Dana Okafor",
     "attendees": ["Sam Ratnaparkhi", "Priya Nair", "Wei Chen"],
     "transcript": ("Dana: Who is carrying Atlas now? "
                    "Sam: I am - it moved to me in Jira this morning. "
                    "Priya: Correct, I'm on the security review with Priya Nandakumar instead.")},
    {"meeting_id": "ff-40", "title": "Acme QBR", "date": "2026-05-21T16:00:00Z",
     "organizer": "Wei Chen", "attendees": ["Dana Okafor", "Wei Chen"],
     "transcript": "Wei: Acme asked for Atlas dates. Dana: Give them the Linear date once it is set."},
]

CORPUS = {
    "slack": SLACK, "gmail": GMAIL, "jira": JIRA, "linear": LINEAR, "github": GITHUB,
    "confluence": CONFLUENCE, "drive": DRIVE, "hubspot": HUBSPOT, "fireflies": FIREFLIES,
}


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    total = 0
    for tool, records in CORPUS.items():
        path = RAW / f"{tool}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        total += len(records)
        print(f"  {tool:<11} {len(records):>3} docs -> {path.relative_to(RAW.parents[1])}")
    print(f"\nseed corpus: {total} documents across {len(CORPUS)} tools")


if __name__ == "__main__":
    main()
