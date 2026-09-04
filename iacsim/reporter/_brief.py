"""The report structure shared by the text and markdown reporters.

A "brief" is one scenario rendered as ordered sections:

    header          scenario, description, source, total, profile rungs
    where           "Where the time goes" — A1 / A2 / A3 shares with a bar
    bottlenecks     top-N per_node lines
    recommendations
    hops            the hop table — top-N by ms (path index kept), or every hop
                    in path order with --all-hops
    critical_path   only when the scenario has a parallel group
    warnings

Both reporters build the same `Section` list from a Findings and differ only
in how they draw it, so the terminal and a PR comment always agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from iacsim.core.models import Finding, Findings, HopResult, InfraGraph

BAR_WIDTH = 24
EVIDENCE_WIDTH = 70


@dataclass
class Row:
    cells: list[str]
    note: str | None = None          # a second, dimmed line under `note_col` (evidence, sources)
    note_col: int = -1


@dataclass
class Section:
    title: str
    columns: list[str]
    rows: list[Row] = field(default_factory=list)
    intro: str | None = None


@dataclass
class Brief:
    title: str
    subtitle: str | None
    meta: list[tuple[str, str]]
    sections: list[Section]


class BriefBuilder:
    def __init__(self, graph: InfraGraph, top_n: int = 10, all_hops: bool = False) -> None:
        self.graph, self.top_n, self.all_hops = graph, top_n, all_hops

    def build(self, f: Findings) -> Brief:
        groups = f.by_analyzer()
        sections = [
            self._where(groups.get("per_category", []), f),
            self._bottlenecks(groups.get("per_node", [])),
            self._recommendations(groups.get("per_hop", []), groups.get("recommendations", [])),
            self._hops(f.hops),
            self._critical_path(groups.get("critical_path", [])),
            self._warnings(f.warnings),
        ]
        return Brief(
            title=f.scenario,
            subtitle=f.description,
            meta=[("source", f.source), ("total", f"{f.total_ms:,.1f} ms"),
                  ("profile", " → ".join(f.profile_sources)), ("shape", self._shape(f.shape))],
            sections=[s for s in sections if s.rows],
        )

    # ------------------------------------------------------------ sections

    def _where(self, cats: list[Finding], f: Findings) -> Section:
        s = Section("Where the time goes", ["layer", "category", "ms", "share", "bar", "what would change it"])
        additive = [c for c in cats if c.additive]
        for c in sorted(additive, key=lambda c: -c.latency_ms):
            s.rows.append(Row([c.layer or "", c.subject, f"{c.latency_ms:,.1f}", f"{c.share:.0%}",
                               bar(c.share), c.detail]))
        for c in cats:
            if c.additive or c.subject == "service":
                continue
            value = f"{c.latency_ms:,.1f} ms" if c.subject == "parallel_savings" else f"{c.latency_ms:g}"
            s.rows.append(Row([c.layer or "", c.subject, value, "—", "", c.detail]))
        return s

    def _bottlenecks(self, nodes: list[Finding]) -> Section:
        s = Section("Top bottlenecks", ["#", "node", "ms", "share", "why"])
        for i, n in enumerate(nodes[:5], 1):
            s.rows.append(Row([str(i), self.name(n.subject), f"{n.latency_ms:,.1f}", f"{n.share:.0%}", n.detail]))
        return s

    def _recommendations(self, _hops: list[Finding], recs: list[Finding]) -> Section:
        s = Section("Recommendations", ["#", "suggestion", "saves ~ms", "of total", "reasoning"])
        for i, r in enumerate(recs, 1):
            why, _, based_on = r.detail.partition(". Based on: ")
            s.rows.append(Row([str(i), r.subject, f"{r.latency_ms:,.0f}", f"{r.share:.0%}", why],
                              note=f"based on: {based_on}" if based_on else None))
        return s

    def _hops(self, hops: list[HopResult]) -> Section:
        indexed = list(enumerate(hops, 1))
        if self.all_hops:
            shown, intro = indexed, f"all {len(hops)} hops in path order"
        else:
            shown = sorted(indexed, key=lambda ih: -ih[1].latency_ms)[:self.top_n]
            shown.sort(key=lambda ih: ih[0])
            intro = (f"top {len(shown)} of {len(hops)} hops by ms, in path order — --all-hops for every hop"
                     if len(hops) > len(shown) else f"all {len(hops)} hops in path order")
        s = Section("Hops", ["#", "", "hop", "ms", "breakdown"], intro=intro)
        for i, h in shown:
            marker = "" if h.on_critical_path else "·"
            name = f"{self.name(h.src)}  (wait)" if h.src == h.dst else f"{self.name(h.src)} → {self.name(h.dst)}"
            breakdown = " ".join(f"{k}={v:g}" for k, v in h.breakdown.items())
            s.rows.append(Row([str(i), marker, name, f"{h.latency_ms:,.1f}", breakdown],
                              note=clip(h.evidence), note_col=2))
        return s

    def _critical_path(self, branches: list[Finding]) -> Section:
        s = Section("Critical path", ["branch", "ms", "verdict"])
        for b in branches:
            s.rows.append(Row([b.subject, f"{b.latency_ms:,.1f}", b.detail]))
        return s

    @staticmethod
    def _warnings(warnings: list[str]) -> Section:
        return Section("Warnings", ["warning"], [Row([w]) for w in warnings])

    # ------------------------------------------------------------ helpers

    def name(self, node_id: str) -> str:
        if " → " in node_id:
            a, b = node_id.split(" → ", 1)
            return f"{self.name(a)} → {self.name(b)}"
        return self.graph.display_name(node_id)

    @staticmethod
    def _shape(shape: dict[str, float]) -> str:
        if not shape:
            return ""
        parts = [f"{int(shape.get('hop_count', 0))} hops"]
        if shape.get("parallel_groups"):
            parts.append(f"{int(shape['parallel_groups'])} parallel group(s) saving {shape['parallel_savings_ms']:,.1f} ms")
        if shape.get("fanout_copies"):
            parts.append(f"fan-out ×{int(shape['fanout_copies'])} costed once")
        if shape.get("wait_ms"):
            parts.append(f"{shape['wait_ms']:,.0f} ms of waits")
        return "; ".join(parts)


def bar(share: float, width: int = BAR_WIDTH) -> str:
    filled = round(max(0.0, min(1.0, share)) * width)
    return "█" * filled + "░" * (width - filled)


def clip(text: str, width: int = EVIDENCE_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"
