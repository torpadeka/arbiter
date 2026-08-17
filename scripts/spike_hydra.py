"""Reproducible HydraDB capability probe + adapter smoke test.

Run:  python scripts/spike_hydra.py
Needs a local node (scripts/hydradb_up.ps1) and `pip install -r requirements.txt`.

Prints two sections:
  SUPPORTED   — the adapter's operations, exercised end to end.
  BOUNDARY    — forms the engine rejects, with its verbatim message, so the
                constraints in docs/hydradb-capabilities.md stay verifiable
                rather than folklore.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.hydra import HydraClient, HydraError, sid  # noqa: E402

# Fixtures are written under their own labels (SpikePerson, not Person) and
# their own key namespace. The query engine reads Person/Project/Artifact/…, so
# running the spike against a live graph cannot shadow real entities — an
# earlier version wrote a fixture "Sam Ratnaparkhi" as a Person and silently
# broke every question about the real one.
NS = "spike"


def skey(name: str) -> str:
    return f"{NS}:{name}"


def key(name: str) -> int:
    return sid(skey(name))


def supported(c: HydraClient) -> None:
    print("=== SUPPORTED ===")

    people = [
        {"id": key("person:sam"), "key": skey("person:sam"), "name": "Spike Person A", "kind": "person"},
        {"id": key("person:priya"), "key": skey("person:priya"), "name": "Spike Person B", "kind": "person"},
    ]
    projects = [{"id": key("project:atlas"), "key": skey("project:atlas"), "name": "Spike Project", "kind": "project"}]
    aliases = [
        {"id": key("alias:@soham"), "key": skey("alias:@soham"), "name": "@spike-a"},
        {"id": key("alias:S. Ratnaparkhi"), "key": skey("alias:S. Ratnaparkhi"), "name": "S. Spike"},
    ]
    claims = [
        {
            "id": key("claim:c1"),
            "key": skey("claim:c1"),
            "predicate": "ASSIGNED_TO",
            "asserted_at": "2026-03-02T10:00:00Z",
            "authority": 0.5,
            "source_tool": "slack",
            "status": "superseded",
        },
        {
            "id": key("claim:c2"),
            "key": skey("claim:c2"),
            "predicate": "ASSIGNED_TO",
            "asserted_at": "2026-04-11T09:22:00Z",
            "authority": 1.0,
            "source_tool": "jira",
            "status": "current",
        },
    ]

    print(f"  upsert SpikePerson  -> {c.upsert_nodes('SpikePerson', people)}")
    print(f"  upsert SpikeProject -> {c.upsert_nodes('SpikeProject', projects)}")
    print(f"  upsert SpikeAlias   -> {c.upsert_nodes('SpikeAlias', aliases)}")
    print(f"  upsert SpikeClaim   -> {c.upsert_nodes('SpikeClaim', claims)}")

    # Aliases are nodes, not an array property (the engine rejects list values).
    print(f"  edges ALIAS_OF     -> {c.create_edges('ALIAS_OF', [(a['id'], key('person:sam')) for a in aliases])}")
    # Fast edges carry no properties; status lives in the relationship type.
    print(f"  edges ASSIGNED_TO  -> {c.create_edges('ASSIGNED_TO', [(key('person:sam'), key('project:atlas'))])}")
    print(f"  edges (superseded) -> {c.create_edges('ASSIGNED_TO_SUPERSEDED', [(key('person:priya'), key('project:atlas'))])}")
    # Provenance: Claim -> subject / object.
    print(f"  edges ABOUT        -> {c.create_edges('ABOUT', [(key('claim:c2'), key('person:sam')), (key('claim:c1'), key('person:priya'))])}")
    print(f"  edges OBJECT       -> {c.create_edges('OBJECT', [(key('claim:c1'), key('project:atlas')), (key('claim:c2'), key('project:atlas'))])}")
    print(f"  edges SUPERSEDES   -> {c.create_edges('SUPERSEDES', [(key('claim:c2'), key('claim:c1'))])}")

    print(f"  neighbors(alias)   -> {c.neighbors(key('alias:@soham'), ['ALIAS_OF'])}")
    print(f"  expand 1..3        -> {c.expand(key('person:sam'), 'ASSIGNED_TO', max_len=3)}")

    print(f"  count(SpikePerson) -> {c.count('SpikePerson')}")

    paths = c.shortest_paths(key("alias:@soham"), key("project:atlas"), ["ALIAS_OF", "ASSIGNED_TO"], max_len=4)
    print(f"  SPpaths            -> {len(paths)} path(s)")
    for p in paths:
        hops = " -> ".join(
            f"{n['properties'].get('name', n['id'])}" for n in p["nodes"]
        )
        rels = ", ".join(r["type"] for r in p["relationships"])
        print(f"      {hops}   [{rels}]")


def boundary(c: HydraClient) -> None:
    """Confirm the documented constraints still hold on this build."""
    print("\n=== BOUNDARY (each must fail) ===")
    probes = [
        ("list property value", "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:X, n.a = row.a",
         {"rows": [{"id": 999001, "a": ["x", "y"]}]}),
        ("properties on batched edge", "UNWIND $rows AS row CREATE (a {id: row.src})-[:T {w: row.w}]->(b {id: row.dst})",
         {"rows": [{"src": 999001, "dst": 999002, "w": 1}]}),
        ("MERGE on relationship", "UNWIND $rows AS row MERGE (a {id: row.src})-[:T]->(b {id: row.dst})",
         {"rows": [{"src": 999001, "dst": 999002}]}),
        ("CREATE ... RETURN", "CREATE (a {id: 999003})-[:T]->(b {id: 999004}) RETURN a.id AS id", None),
        ("untyped relationship", "MATCH (a {id: $id})-[r]->(b) RETURN b.id AS id", {"id": 999001}),
        ("count(n) form", "MATCH (n:SpikePerson) RETURN count(n) AS c", None),
        ("function in RETURN", "MATCH (a {id: $id})-[r:ALIAS_OF]->(b) RETURN type(r) AS rel", {"id": 999001}),
        ("bare node MATCH", "MATCH (n) RETURN n.id AS id LIMIT 1", None),
        ("string node id", "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:X", {"rows": [{"id": "abc"}]}),
    ]
    for name, cypher, params in probes:
        try:
            c.query(cypher, params)
            print(f"  [!] {name}: UNEXPECTEDLY ACCEPTED — capability matrix is stale")
        except HydraError as e:
            msg = str(e).splitlines()[0]
            print(f"  [ok] {name}: {msg}")


if __name__ == "__main__":
    with HydraClient() as client:
        client.ping()
        print("connected\n")
        supported(client)
        boundary(client)
        print("\nspike complete")
