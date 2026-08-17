"""Thin HydraDB adapter.

Wraps the OSS engine's HTTP JSON API behind the four operations the rest of
Arbiter needs: upsert_nodes, create_edges, neighbors, shortest_paths.

Every quirk encoded here was verified by probing a live node; see
docs/hydradb-capabilities.md for the full matrix and the verbatim engine
errors. The three that shape this file:

  1. Node ids must be integers -> `sid()` hashes string keys to int64.
  2. Property values must be scalars -> lists become nodes+edges, not arrays.
  3. Batched edges take one fixed type and no properties -> provenance lives
     on Claim nodes; status is encoded in the relationship type.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import httpx

try:  # .env is convenience, not a dependency
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

SCALARS = (str, int, float, bool, type(None))


class HydraError(RuntimeError):
    """An error returned by the engine, carrying its message verbatim."""


def sid(key: str) -> int:
    """Stable 63-bit node id derived from a string key.

    The engine requires integer ids. Callers keep the human-readable key as a
    `key` property so ids stay round-trippable and debuggable.
    """
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "big") >> 1


@dataclass
class HydraConfig:
    base_url: str = os.getenv("HYDRA_HTTP_URL", "http://127.0.0.1:8443")
    token: str = os.getenv("HYDRA_TOKEN", "local-development-token-32-bytes")
    namespace: str = os.getenv("HYDRA_NAMESPACE", "default")
    graph: str = os.getenv("HYDRA_GRAPH", "default")
    cell_id: str = os.getenv("HYDRA_CELL_ID", "cell-0")
    timeout: float = 60.0


def _unwrap(cell: Any) -> Any:
    """Unwrap one typed result cell into a plain Python value."""
    if not isinstance(cell, dict):
        return cell
    if "type" in cell and "value" in cell:
        if cell["type"] == "path":
            return _unwrap_path(cell["value"])
        return cell["value"]
    # Path-internal properties use a different tagging scheme: {"String": "Sam"}
    if len(cell) == 1:
        (only,) = cell.values()
        return only
    return cell


def _unwrap_path(path: dict) -> dict:
    """Normalize an engine path value into {nodes: [...], relationships: [...]}."""
    return {
        "nodes": [
            {
                "id": n.get("id"),
                "labels": n.get("labels", []),
                "properties": {k: _unwrap(v) for k, v in (n.get("properties") or {}).items()},
            }
            for n in path.get("nodes", [])
        ],
        "relationships": [
            {
                "type": r.get("edge_type"),
                "src": r.get("src"),
                "dst": r.get("dst"),
                "properties": {k: _unwrap(v) for k, v in (r.get("properties") or {}).items()},
            }
            for r in path.get("relationships", [])
        ],
    }


def _check_scalar(row: dict, where: str) -> None:
    for k, v in row.items():
        if not isinstance(v, SCALARS):
            raise HydraError(
                f"{where}: field {k!r} is {type(v).__name__}; the engine accepts scalars only. "
                "Model collections as nodes + edges (e.g. (:Alias)-[:ALIAS_OF]->(:Person)) "
                "or serialize to a string."
            )


class HydraClient:
    """Client for one graph in one cell."""

    def __init__(self, config: HydraConfig | None = None) -> None:
        self.cfg = config or HydraConfig()
        self._http = httpx.Client(
            base_url=self.cfg.base_url,
            timeout=self.cfg.timeout,
            headers={
                "Authorization": f"Bearer {self.cfg.token}",
                "X-Graph-Namespace": self.cfg.namespace,
                "Content-Type": "application/json",
            },
        )
        self.last_bookmark: str | None = None

    # --- core -------------------------------------------------------------

    def query(self, cypher: str, parameters: dict | None = None, attempts: int = 3) -> list[dict]:
        """Run one statement, returning rows as dicts of plain Python values.

        Retries transient failures. A cold object store — the first writes after
        a restart or a bucket wipe — can exceed the server's 30s query timeout,
        which would otherwise abort a load half-written.
        """
        body: dict[str, Any] = {"cell_id": self.cfg.cell_id, "query": cypher}
        if parameters:
            body["parameters"] = parameters  # note: `parameters`, not `params`

        payload: dict = {}
        for attempt in range(attempts):
            try:
                resp = self._http.post(f"/v1/graphs/{self.cfg.graph}/query", json=body)
                payload = resp.json()
            except (httpx.TransportError, ValueError) as exc:
                if attempt == attempts - 1:
                    raise HydraError(f"transport failure after {attempts} attempts: {exc}") from None
                time.sleep(2 ** attempt)
                continue

            err = payload.get("error") or {}
            transient = str(err.get("code")) in {"query_timeout", "unavailable", "internal"}
            if transient and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            break

        if "error" in payload:
            err = payload["error"]
            raise HydraError(f"{err.get('code')}: {err.get('message')}\n  query: {cypher.strip()[:300]}")

        self.last_bookmark = payload.get("bookmark") or self.last_bookmark
        cols = payload.get("columns") or []
        return [{c: _unwrap(v) for c, v in zip(cols, row)} for row in payload.get("rows") or []]

    def ping(self) -> bool:
        # Node-only MATCH needs an id, label, or property predicate; a label
        # with no members returns empty rather than erroring.
        self.query("MATCH (n:_Health) RETURN n.id AS id LIMIT 1")
        return True

    # --- writes -----------------------------------------------------------

    def upsert_nodes(self, label: str, rows: Sequence[dict], batch_size: int = 500) -> int:
        """Batch upsert nodes of one label.

        Each row needs an integer `id`; remaining fields become properties and
        must be scalars. The engine requires MERGE-by-id followed by exactly
        one SET label, so `label` is applied to every row in the batch.
        """
        rows = list(rows)
        if not rows:
            return 0
        prop_keys = sorted({k for r in rows for k in r if k != "id"})
        sets = ", ".join(f"n.{k} = row.{k}" for k in prop_keys)
        cypher = f"UNWIND $rows AS row MERGE (n {{id: row.id}}) SET n:{label}" + (f", {sets}" if sets else "")

        written = 0
        for i in range(0, len(rows), batch_size):
            chunk = rows[i : i + batch_size]
            for r in chunk:
                if not isinstance(r.get("id"), int):
                    raise HydraError(f"upsert_nodes({label}): row id must be int, got {r.get('id')!r}")
                _check_scalar(r, f"upsert_nodes({label})")
            # Absent keys would read as null and blank existing values; normalize.
            norm = [{"id": r["id"], **{k: r.get(k) for k in prop_keys}} for r in chunk]
            self.query(cypher, {"rows": norm})
            written += len(chunk)
        return written

    def create_edges(self, rel_type: str, pairs: Iterable[tuple[int, int]], batch_size: int = 1000) -> int:
        """Batch create property-free edges of one type.

        The engine rejects properties and MERGE on batched edges, so callers
        deduplicate first and keep metadata on the connected Claim node.
        """
        rows = [{"src": s, "dst": d} for s, d in pairs]
        if not rows:
            return 0
        cypher = f"UNWIND $rows AS row CREATE (a {{id: row.src}})-[:{rel_type}]->(b {{id: row.dst}})"
        for i in range(0, len(rows), batch_size):
            self.query(cypher, {"rows": rows[i : i + batch_size]})
        return len(rows)

    def create_edge(self, rel_type: str, src: int, dst: int, props: dict | None = None) -> None:
        """Create a single edge, optionally with properties (one per statement)."""
        props = props or {}
        _check_scalar(props, f"create_edge({rel_type})")
        rendered = ""
        if props:
            rendered = " {" + ", ".join(f"{k}: ${k}" for k in props) + "}"
        cypher = f"CREATE (a {{id: $src}})-[:{rel_type}{rendered}]->(b {{id: $dst}})"
        self.query(cypher, {"src": src, "dst": dst, **props})

    # --- reads ------------------------------------------------------------

    def neighbors(
        self,
        node_id: int,
        rel_types: Sequence[str],
        direction: str = "out",
        limit: int = 100,
    ) -> list[dict]:
        """One-hop neighbours across the given relationship types."""
        if not rel_types:
            raise HydraError("neighbors() needs at least one relationship type; untyped patterns are rejected")

        # RETURN accepts only <binding>.<property> or count(*), so type(r) is
        # unavailable; query per type and tag the rows client-side instead.
        out: list[dict] = []
        for rel in rel_types:
            pattern = {
                "out": f"(a {{id: $id}})-[:{rel}]->(b)",
                "in": f"(a {{id: $id}})<-[:{rel}]-(b)",
                "both": f"(a {{id: $id}})-[:{rel}]-(b)",
            }[direction]
            rows = self.query(
                f"MATCH {pattern} RETURN b.id AS id, b.key AS key, b.name AS name LIMIT {int(limit)}",
                {"id": node_id},
            )
            out.extend({**r, "rel": rel} for r in rows)
        return out

    def scan(self, match: str, fields: str, key_expr: str, page: int = 1000, params: dict | None = None) -> list[dict]:
        """Read every matching row, paging by key.

        A single MATCH is capped server-side at 1024 rows, and the `next_cursor`
        it returns is bound to that exact request, so replaying it fails with
        "result cursor does not belong to this query request". Keyset pagination
        avoids the cursor entirely and uses only the supported subset: order by
        a key and ask for everything after the last one seen.

        Silent truncation is the failure this prevents. An entity index missing
        a quarter of its people looks like a working system that just cannot
        find things.
        """
        out: list[dict] = []
        last = ""
        while True:
            rows = self.query(
                f"MATCH {match} WHERE {key_expr} > $__last RETURN {fields} ORDER BY key LIMIT {int(page)}",
                {**(params or {}), "__last": last},
            )
            if not rows:
                break
            out.extend(rows)
            nxt = rows[-1].get("key")
            if not nxt or nxt == last or len(rows) < page:
                break
            last = nxt
        return out

    def scan_label(self, label: str, fields: str = "n.id AS id, n.key AS key, n.name AS name") -> list[dict]:
        return self.scan(f"(n:{label})", fields, "n.key")

    def count(self, label: str) -> int:
        """Count nodes carrying a label. `count(*)` is supported; `count(n)` is not."""
        rows = self.query(f"MATCH (n:{label}) RETURN count(*) AS c")
        return int(rows[0]["c"]) if rows else 0

    def expand(self, node_id: int, rel_type: str, max_len: int = 3, limit: int = 200) -> list[dict]:
        """Bounded variable-length expansion from one node along one type."""
        return self.query(
            f"MATCH p = (a {{id: $id}})-[:{rel_type}*1..{int(max_len)}]->(b) "
            f"RETURN b.id AS id, b.key AS key, b.name AS name LIMIT {int(limit)}",
            {"id": node_id},
        )

    def shortest_paths(
        self,
        source: int,
        target: int,
        rel_types: Sequence[str],
        max_len: int = 4,
        path_count: int = 5,
    ) -> list[dict]:
        """Server-side bounded shortest paths, materialized with provenance.

        Returns normalized {nodes, relationships} dicts — the exact payload the
        UI renders as the traversal trace.
        """
        cypher = (
            "CALL algo.SPpaths({sourceNode: $sourceNode, targetNode: $targetNode, "
            f"relTypes: {list(rel_types)!r}, maxLen: {int(max_len)}, pathCount: {int(path_count)}"
            "}) YIELD path RETURN path"
        )
        rows = self.query(cypher, {"sourceNode": source, "targetNode": target})
        return [r["path"] for r in rows if r.get("path")]

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "HydraClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
