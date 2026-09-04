"""Terminal report — the Brief drawn with `rich` tables.

Renders to a string so the CLI can print it or a test can assert on it.
`color=True` emits ANSI (the CLI passes it when stdout is a TTY); otherwise
rich degrades to plain text with the same layout.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.table import Table
from rich.text import Text

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import DiffReport, Findings, InfraGraph
from iacsim.reporter._brief import Brief, BriefBuilder, Section
from iacsim.reporter._diff_brief import DiffBriefBuilder

WIDTH = 120


@REPORTERS.register("text")
class TextReporter(Reporter):
    def __init__(self, color: bool = False, top_n: int = 10, all_hops: bool = False) -> None:
        self.color, self.top_n, self.all_hops = color, top_n, all_hops

    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=self.color, width=WIDTH, no_color=not self.color,
                          highlight=False)
        builder = BriefBuilder(graph, top_n=self.top_n, all_hops=self.all_hops)
        capacity = builder.build_capacity(findings)
        if capacity is not None:
            self._draw(console, capacity, rule="capacity")
        for f in findings:
            self._draw(console, builder.build(f))
        if graph.warnings:
            console.print(Text("graph warnings", style="bold yellow"))
            for w in graph.warnings:
                console.print(f"  - {w}")
        return buf.getvalue()

    def render_diff(self, diff: DiffReport, before: InfraGraph, after: InfraGraph) -> str:
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=self.color, width=WIDTH, no_color=not self.color,
                          highlight=False)
        for i, brief in enumerate(DiffBriefBuilder(before, after).build(diff)):
            self._draw(console, brief, rule="diff" if i == 0 else "scenario")
        return buf.getvalue()

    def _draw(self, console: Console, brief: Brief, rule: str = "scenario") -> None:
        console.rule(Text(f"{rule}: {brief.title}", style="bold"))
        if brief.subtitle:
            console.print(Text(brief.subtitle, style="italic"))
        for key, value in brief.meta:
            if value:
                console.print(Text(f"{key:8} ", style="bold"), end="")
                console.print(Text(value, style=self._meta_style(key, value)))
        console.print()
        for section in brief.sections:
            self._section(console, section)

    @staticmethod
    def _meta_style(key: str, value: str) -> str:
        """Deltas are red when latency grew, green when it shrank."""
        if key != "delta":
            return ""
        return "red" if value.startswith("+") else "green" if value.startswith("-") else ""

    def _section(self, console: Console, section: Section) -> None:
        table = Table(title=section.title, title_justify="left", caption=section.intro,
                      caption_justify="left", show_lines=False, pad_edge=False, expand=False)
        for col in section.columns:
            justify = ("right" if col in ("ms", "p99", "p99 − p50", "share", "share of spread", "saves ~ms",
                                          "of total", "#", "users") or col.replace(",", "").isdigit() else "left")
            table.add_column(col, justify=justify, overflow="fold", no_wrap=(col in ("bar", "layer")))
        for row in section.rows:
            table.add_row(*row.cells)
            if row.note:
                cells = [""] * len(row.cells)
                cells[row.note_col] = Text(row.note, style="dim")
                table.add_row(*cells)
        console.print(table)
        console.print()
