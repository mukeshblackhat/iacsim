"""The diff report structure shared by the text and markdown reporters.

A diff renders as one "what changed" Brief for the graph, then one Brief per
scenario:

    header          before / after paths, profile
    graph           nodes moved / added / removed, edges added / removed
    --- per scenario ---
    meta            before, after, delta (+ms / +%), shape if it changed
    categories      A1 / A2 / A3 shift: before → after ms and share
    hops            changed / added / removed hops with breakdown deltas;
                    unchanged hops collapsed into the intro line
    bottlenecks     per_node deltas, largest |delta| first
    recommendations appeared / disappeared / unchanged

Same `Brief` / `Section` / `Row` types as `_brief.py`, so the text and
markdown reporters need no new drawing code.
"""

from __future__ import annotations

from iacsim.core.models import DiffReport, HopDelta, InfraGraph, ScenarioDiff, ValueDelta
from iacsim.reporter._brief import Brief, Row, Section

TOP_NODES = 5


class DiffBriefBuilder:
    def __init__(self, before: InfraGraph, after: InfraGraph) -> None:
        self.before, self.after = before, after

    # ------------------------------------------------------------ top level

    def build(self, diff: DiffReport) -> list[Brief]:
        return [self._graph_brief(diff), *(self._scenario_brief(s) for s in diff.scenarios)]

    def _graph_brief(self, diff: DiffReport) -> Brief:
        g = diff.graph
        s = Section("What changed in the infrastructure", ["change", "what", "before", "after"])
        for m in g.nodes_moved:
            s.rows.append(Row(["moved", f"{self.name(m.node_id)} ({m.field})", m.before or "—", m.after or "—"]))
        for n in g.nodes_added:
            s.rows.append(Row(["added", self.name(n), "—", self.kind(n, self.after)]))
        for n in g.nodes_removed:
            s.rows.append(Row(["removed", self.name(n), self.kind(n, self.before), "—"]))
        for e in g.edges_added:
            s.rows.append(Row(["edge added", self.edge_name(e), "—", "✓"]))
        for e in g.edges_removed:
            s.rows.append(Row(["edge removed", self.edge_name(e), "✓", "—"]))
        if not s.rows:
            s.rows.append(Row(["none", "graphs are identical", "", ""]))
        return Brief(title="what changed", subtitle=None,
                     meta=[("before", diff.before), ("after", diff.after),
                           ("profile", " → ".join(diff.profile_sources))],
                     sections=[s])

    # ------------------------------------------------------------ per scenario

    def _scenario_brief(self, s: ScenarioDiff) -> Brief:
        if s.status != "both":
            side = "after" if s.status == "only_after" else "before"
            total = s.after_ms if s.status == "only_after" else s.before_ms
            return Brief(title=s.name, subtitle=s.description,
                         meta=[("status", f"only in {side} ({total:,.1f} ms) — nothing to compare")],
                         sections=[])
        meta = [("before", f"{s.before_ms:,.1f} ms"), ("after", f"{s.after_ms:,.1f} ms"),
                ("delta", self.delta_text(s.delta_ms, s.delta_pct))]
        if s.shape_before != s.shape_after:
            meta.append(("shape", f"{shape_text(s.shape_before)} → {shape_text(s.shape_after)}"))
        sections = [self._categories(s.categories), self._hops(s.hops),
                    self._nodes(s.nodes), self._recommendations(s)]
        return Brief(title=s.name, subtitle=s.description, meta=meta,
                     sections=[x for x in sections if x.rows])

    def _categories(self, cats: list[ValueDelta]) -> Section:
        s = Section("Where the time goes — shift",
                    ["layer", "category", "before ms", "after ms", "delta", "share", "what would change it"])
        for c in sorted(cats, key=lambda c: -abs(c.delta_ms)):
            s.rows.append(Row([c.layer or "", c.subject, ms(c.before_ms), ms(c.after_ms),
                               signed(c.delta_ms), share_shift(c.before_share, c.after_share), c.detail]))
        return s

    def _hops(self, hops: list[HopDelta]) -> Section:
        shown = [h for h in hops if h.status != "unchanged"]
        unchanged = len(hops) - len(shown)
        intro = (f"{len(shown)} hop(s) changed, {unchanged} unchanged" if shown
                 else f"all {len(hops)} hops unchanged")
        s = Section("Hops that changed", ["status", "#", "hop", "before ms", "after ms", "delta", "breakdown"],
                    intro=intro)
        for h in shown:
            index = f"{h.index_before or '—'}→{h.index_after or '—'}"
            s.rows.append(Row([h.status, index, self.hop_name(h.label), ms(h.before_ms), ms(h.after_ms),
                               signed(h.delta_ms), breakdown_shift(h)]))
        return s

    def _nodes(self, nodes: list[ValueDelta]) -> Section:
        s = Section("Bottleneck shift", ["node", "before ms", "after ms", "delta", "why (after)"])
        moved = [n for n in nodes if n.status != "unchanged"]
        for n in sorted(moved, key=lambda n: -abs(n.delta_ms))[:TOP_NODES]:
            s.rows.append(Row([self.name(n.subject), ms(n.before_ms), ms(n.after_ms), signed(n.delta_ms), n.detail]))
        return s

    def _recommendations(self, sd: ScenarioDiff) -> Section:
        s = Section("Recommendations", ["status", "suggestion", "saves ~ms"])
        for r in sd.recommendations:
            if r.status == "unchanged":
                continue
            s.rows.append(Row([r.status, r.subject, f"{r.saving_ms:,.0f}"]))
        unchanged = sum(1 for r in sd.recommendations if r.status == "unchanged")
        if unchanged:
            s.intro = f"{unchanged} recommendation(s) unchanged"
        return s

    # ------------------------------------------------------------ helpers

    def name(self, node_id: str) -> str:
        graph = self.after if node_id in self.after.nodes else self.before
        return graph.display_name(node_id)

    def hop_name(self, label: str) -> str:
        a, _, b = label.partition(" → ")
        return f"{self.name(a)} → {self.name(b)}" if b else label

    def edge_name(self, key: str) -> str:
        """'src → dst (kind)' with display names."""
        body, _, kind = key.rpartition(" (")
        return f"{self.hop_name(body)} ({kind}" if kind else key

    @staticmethod
    def kind(node_id: str, graph: InfraGraph) -> str:
        n = graph.nodes.get(node_id)
        return f"{n.kind}/{n.subtype}" if n else ""

    @staticmethod
    def delta_text(delta: float, pct: float | None) -> str:
        return f"{delta:+,.1f} ms" + (f" ({pct:+.0%})" if pct is not None else "")


def ms(value: float | None) -> str:
    return "—" if value is None else f"{value:,.1f}"


def signed(delta: float) -> str:
    return "0.0" if abs(delta) < 0.05 else f"{delta:+,.1f}"


def share_shift(before: float | None, after: float | None) -> str:
    b = "—" if before is None else f"{before:.0%}"
    a = "—" if after is None else f"{after:.0%}"
    return f"{b} → {a}"


def breakdown_shift(h: HopDelta) -> str:
    parts = []
    for cat, (b, a) in h.breakdown_deltas().items():
        parts.append(f"{cat} {b:g}→{a:g}" if abs(a - b) >= 0.05 else f"{cat} {a:g}")
    return " ".join(parts)


def shape_text(shape: dict[str, float]) -> str:
    if not shape:
        return "—"
    parts = [f"{int(shape.get('hop_count', 0))} hops"]
    if shape.get("parallel_groups"):
        parts.append(f"parallel saves {shape.get('parallel_savings_ms', 0):,.0f} ms")
    if shape.get("wait_ms"):
        parts.append(f"{shape['wait_ms']:,.0f} ms waits")
    return ", ".join(parts)
