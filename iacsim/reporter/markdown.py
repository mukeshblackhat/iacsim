"""Markdown report — the same Brief as the text reporter, as GitHub-flavoured
markdown for PR comments and docs."""

from __future__ import annotations

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import Findings, InfraGraph
from iacsim.reporter._brief import Brief, BriefBuilder, Section


@REPORTERS.register("markdown")
class MarkdownReporter(Reporter):
    def __init__(self, top_n: int = 10, all_hops: bool = False, **_ignored) -> None:
        self.top_n, self.all_hops = top_n, all_hops

    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        builder = BriefBuilder(graph, top_n=self.top_n, all_hops=self.all_hops)
        parts = [self._brief(builder.build(f)) for f in findings]
        if graph.warnings:
            parts.append("## Graph warnings\n\n" + "\n".join(f"- {w}" for w in graph.warnings) + "\n")
        return "\n".join(parts)

    def _brief(self, brief: Brief) -> str:
        lines = [f"# {brief.title}", ""]
        if brief.subtitle:
            lines += [f"_{brief.subtitle}_", ""]
        lines += [f"- **{k}**: {v}" for k, v in brief.meta if v] + [""]
        for section in brief.sections:
            lines += self._section(section)
        return "\n".join(lines)

    @staticmethod
    def _section(section: Section) -> list[str]:
        lines = [f"## {section.title}", ""]
        if section.intro:
            lines += [f"_{section.intro}_", ""]
        lines.append("| " + " | ".join(section.columns) + " |")
        lines.append("|" + "|".join("---" for _ in section.columns) + "|")
        for row in section.rows:
            cells = [c.replace("|", "\\|") for c in row.cells]
            if row.note:
                note = row.note.replace("|", "\\|")
                cells[row.note_col] = f"{cells[row.note_col]}<br><sub>{note}</sub>"
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        return lines
