"""Arbiter CLI — ask the graph a question and see exactly how it answered.

    python cli.py ask "who is ENG-4471 assigned to?"
    python cli.py ask "when does Atlas launch?" --as-of 2026-03-01
    python cli.py ask "what is the Atlas budget?"
    python cli.py entities
    python cli.py stats

Every answer prints its provenance: the traversal path, the claims cited with
their sources, and — when sources disagreed — which claim won and why.
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

console = Console()

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


def cmd_ask(args: argparse.Namespace) -> int:
    engine = Engine()
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
