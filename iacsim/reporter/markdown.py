"""Markdown report — the same Brief as the text reporter, as GitHub-flavoured
markdown for PR comments and docs."""

from __future__ import annotations

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import Findings, InfraGraph
from iacsim.diff.models import DiffReport
from iacsim.reporter._brief import Brief, BriefBuilder, Section
from iacsim.reporter._diff_brief import DiffBriefBuilder


@REPORTERS.register("markdown")
class MarkdownReporter(Reporter):
    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        builder = BriefBuilder(graph, top_n=self.options.top_n, all_hops=self.options.all_hops)
        parts = []
        capacity = builder.build_capacity(findings)
        if capacity is not None:
            parts.append(self._brief(capacity))
        parts += [self._brief(builder.build(f)) for f in findings]
        if graph.warnings:
            parts.append("## Graph warnings\n\n" + "\n".join(f"- {w}" for w in graph.warnings) + "\n")
        return "\n".join(parts)

    def render_diff(self, diff: DiffReport, before: InfraGraph, after: InfraGraph) -> str:
        return "\n".join(self._brief(b) for b in DiffBriefBuilder(before, after).build(diff))

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
