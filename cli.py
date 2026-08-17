"""Arbiter CLI. Ask the graph a question and see exactly how it answered.

    python cli.py init ./mydata            derive an ontology from a folder
    python cli.py ingest                   build the graph from it
    python cli.py ask "who owns Atlas?"    query it
    python cli.py entities                 canonical entities + merge evidence
    python cli.py stats                    graph node counts

Every answer prints its provenance: the traversal path, the claims cited with
their sources, and, when sources disagreed, which claim won and why.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from answer.engine import Answer, Engine, Evidence
from graph.hydra import HydraClient
from graph.models import load_schema

console = Console()

STATE = Path(__file__).resolve().parent / ".arbiter" / "state.json"


def read_state() -> dict:
    """What was last ingested, so `ask` uses the schema the graph was built with."""
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(**values) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({**read_state(), **values}, indent=2), encoding="utf-8")


def active_schema():
    path = read_state().get("schema")
    return load_schema(Path(path)) if path and Path(path).exists() else load_schema()

TOOL_STYLE = {
    "jira": "blue", "linear": "blue", "github": "magenta", "hubspot": "cyan",
    "confluence": "green", "drive": "green", "fireflies": "yellow",
    "gmail": "yellow", "slack": "bright_black",
}


def render_answer(ans: Answer, show_graph: str = "") -> None:
    if ans.abstained:
        console.print(Panel(
            Text(ans.text, style="bold yellow"),
            title=f"[yellow]ABSTAINED[/] · gate: {ans.gate}",
            border_style="yellow", padding=(1, 2),
        ))
    else:
        console.print(Panel(
            Text(ans.text, style="bold white"),
            title="[green]ANSWER[/]" + ("" if ans.grounded_by_model else " [dim](deterministic rendering)[/]"),
            border_style="green", padding=(1, 2),
        ))

    if ans.path:
        parts = []
        for src, pred, dst in ans.path:
            parts += [f"[bold]{src}[/]", f"[dim]--{pred}-->[/]", f"[bold]{dst}[/]"]
        console.print("  [dim]path[/]  " + " ".join(parts))

    if ans.evidence:
        table = Table(show_header=True, header_style="dim", box=None, padding=(0, 2))
        for col in ("claim", "source", "asserted", "score", "statement"):
            table.add_column(col, overflow="fold")
        for ev in ans.evidence[:8]:
            c = ev.claim
            table.add_row(
                f"[dim]{c.key.split(':')[1][:8]}[/]",
                f"[{TOOL_STYLE.get(c.source_tool, 'white')}]{c.source_tool}[/] {c.source_artifact_key.split(':')[-1][:22]}",
                c.asserted_at[:10],
                f"{c.score:.2f}",
                (c.evidence_span or f"{c.predicate}")[:70],
            )
        console.print("\n  [dim]evidence[/]")
        console.print(table)

    if ans.superseded or ans.contested:
        def value(claim) -> str:
            return ans.labels.get(claim.object_key) or claim.object_literal or claim.object_key.split(":")[-1]

        rows = []
        winner = ans.evidence[0].claim if ans.evidence else None
        for ev in (ans.superseded + ans.contested)[:5]:
            c = ev.claim
            state = "superseded" if c.status == "superseded" else "contested"
            rows.append(
                f"  [red]{state}[/] {c.source_tool} ({c.asserted_at[:10]}, authority {c.authority:.2f}, "
                f"score {c.score:.2f}) → {value(c)}"
            )
        body = "\n".join(rows)
        if winner:
            body = (
                f"  [green]current[/]    {winner.source_tool} ({winner.asserted_at[:10]}, "
                f"authority {winner.authority:.2f}, score {winner.score:.2f}) → "
                f"{value(winner)}\n" + body
            )
        console.print(Panel(body, title="[red]CONFLICT ARBITRATED[/]", border_style="red", padding=(0, 1)))

    meta = f"  [dim]{ans.latency_ms} ms · {len(ans.evidence)} claim(s)"
    if ans.as_of:
        meta += f" · as of {ans.as_of[:10]}"
    if ans.entities:
        meta += " · matched " + ", ".join(f"{e.name} ({e.matched_via})" for e in ans.entities[:2])
    console.print(meta + "[/]\n")

    if show_graph:
        write_graph(ans, Path(show_graph))
        console.print(f"  [dim]subgraph written to {show_graph}[/]\n")


def write_graph(ans: Answer, path: Path) -> None:
    """Standalone HTML subgraph (mermaid inlined, no network access needed)."""
    def node_id(name: str) -> str:
        return "n" + str(abs(hash(name)) % 10**8)

    lines = ["graph LR"]
    for src, pred, dst in ans.path:
        lines.append(f'  {node_id(src)}["{src}"] -->|{pred}| {node_id(dst)}["{dst}"]')
    for ev in ans.evidence[:6]:
        c = ev.claim
        art = c.source_artifact_key.split(":", 1)[-1]
        lines.append(f'  {node_id(art)}[("{art}")] -.->|SOURCE| {node_id(c.key)}["{c.predicate}"]')
    for ev in ans.superseded[:4]:
        c = ev.claim
        lines.append(f'  {node_id(c.key)}["{c.predicate} (superseded)"]:::superseded')
    lines.append("  classDef superseded stroke-dasharray: 4 4,color:#999;")

    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Arbiter subgraph</title>"
        "<style>body{font-family:system-ui;padding:2rem;background:#0f1115;color:#e6e6e6}"
        "pre.mermaid{background:#161922;padding:1rem;border-radius:8px}</style>"
        f"<h2>{ans.question}</h2><p>{ans.text}</p>"
        f"<pre class='mermaid'>\n{chr(10).join(lines)}\n</pre>",
        encoding="utf-8",
    )


def cmd_init(args: argparse.Namespace) -> int:
    """Point Arbiter at a folder and let it derive the ontology."""
    from ingest.induce import run as induce

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        console.print(f"[red]{data_dir} is not a directory[/]")
        return 1

    out = Path(args.out).resolve() if args.out else Path(__file__).resolve().parent / "ontology" / "generated.yaml"
    schema = induce(data_dir, out, vocab_docs=args.vocab_docs)
    write_state(schema=str(out), data_dir=str(data_dir))

    console.print(Panel(
        f"  sources    {len(schema['sources'])}\n"
        f"  predicates {len(schema['predicates'])}\n"
        f"  rules      {sum(len(b['rules']) for b in schema['sources'].values())}\n\n"
        f"  schema written to {out}\n"
        f"  review it, then run: [bold]python cli.py ingest[/]",
        title="[green]ONTOLOGY INDUCED[/]", border_style="green", padding=(1, 2),
    ))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from ingest.load import run as load

    state = read_state()
    schema_path = args.schema or state.get("schema")
    data_dir = args.data_dir or state.get("data_dir")
    if not schema_path or not data_dir:
        console.print("[red]nothing to ingest. Run: python cli.py init <folder>[/]")
        return 1

    load(tier_b=args.tier_b, limit=args.limit, schema_path=schema_path, data_dir=data_dir)
    write_state(schema=str(schema_path), data_dir=str(data_dir))
    console.print("\n[green]ready.[/] ask a question: [bold]python cli.py ask \"...\"[/]")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    engine = Engine(schema=active_schema())
    ans = engine.ask(args.question, as_of=args.as_of, max_hops=args.hops, use_model=not args.no_model)
    if args.json:
        print(json.dumps({
            "question": ans.question, "status": ans.status, "gate": ans.gate, "answer": ans.text,
            "as_of": ans.as_of, "latency_ms": ans.latency_ms,
            "citations": [{
                "claim": e.claim.key, "predicate": e.claim.predicate,
                "subject": e.claim.subject_key, "object": e.claim.object_key or e.claim.object_literal,
                "source_tool": e.claim.source_tool, "source": e.claim.source_artifact_key,
                "asserted_at": e.claim.asserted_at, "score": e.claim.score, "status": e.claim.status,
            } for e in ans.evidence],
            "superseded": [e.claim.key for e in ans.superseded],
            "path": [list(p) for p in ans.path],
        }, indent=2))
    else:
        render_answer(ans, args.graph)
    return 0 if not ans.abstained else 2


def cmd_entities(_: argparse.Namespace) -> int:
    client = HydraClient()
    rows = client.query(
        "MATCH (p:Person) RETURN p.key AS key, p.name AS name, p.email AS email, "
        "p.alias_count AS aliases, p.mention_count AS mentions, p.tools AS tools, p.merge_evidence AS evidence"
    )
    def as_int(value: object) -> int:
        try:
            return int(value)  # absent properties can come back as "" or null
        except (TypeError, ValueError):
            return 0

    table = Table(title="canonical entities", header_style="dim", box=None, padding=(0, 2))
    for col in ("name", "email", "aliases", "mentions", "tools"):
        table.add_column(col, overflow="fold")
    for r in sorted(rows, key=lambda r: -as_int(r.get("mentions"))):
        table.add_row(
            f"[bold]{r.get('name')}[/]", r.get("email") or "[dim]—[/]",
            str(as_int(r.get("aliases"))), str(as_int(r.get("mentions"))),
            f"[dim]{r.get('tools') or ''}[/]",
        )
    console.print(table)
    for r in rows:
        if r.get("evidence"):
            console.print(f"\n  [bold]{r.get('name')}[/]")
            for line in str(r["evidence"]).split(" | "):
                console.print(f"    [dim]{line}[/]")
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    client = HydraClient()
    labels = ["Person", "Alias", "Artifact", "Claim", "Project", "Account", "Topic", "Group"]
    table = Table(title="graph", header_style="dim", box=None, padding=(0, 3))
    table.add_column("label")
    table.add_column("count", justify="right")
    for label in labels:
        table.add_row(label, str(client.count(label)))
    console.print(table)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="arbiter", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="point Arbiter at a folder and derive an ontology from it")
    init.add_argument("data_dir", help="folder of exports (jsonl, json, csv, md)")
    init.add_argument("--out", default="", help="where to write the induced schema")
    init.add_argument("--vocab-docs", type=int, default=40, help="documents sampled for vocabulary discovery")
    init.set_defaults(func=cmd_init)

    ingest = sub.add_parser("ingest", help="build the graph using the induced ontology")
    ingest.add_argument("--schema", default="", help="schema file (defaults to the last induced one)")
    ingest.add_argument("--data-dir", default="", help="corpus folder (defaults to the last used one)")
    ingest.add_argument("--tier-b", action="store_true", help="also extract claims from free text with an LLM")
    ingest.add_argument("--limit", type=int, default=None)
    ingest.set_defaults(func=cmd_ingest)

    ask = sub.add_parser("ask", help="ask a question")
    ask.add_argument("question")
    ask.add_argument("--as-of", default="", help="answer as the graph stood on this date (YYYY-MM-DD)")
    ask.add_argument("--hops", type=int, default=3)
    ask.add_argument("--json", action="store_true")
    ask.add_argument("--no-model", action="store_true", help="skip LLM rendering; graph output only")
    ask.add_argument("--graph", default="", help="write a standalone HTML subgraph to this path")
    ask.set_defaults(func=cmd_ask)

    sub.add_parser("entities", help="show canonical entities and merge evidence").set_defaults(func=cmd_entities)
    sub.add_parser("stats", help="graph node counts").set_defaults(func=cmd_stats)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
