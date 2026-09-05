"""The report structure shared by the text and markdown reporters.

A "brief" is one scenario rendered as ordered sections:

    header          scenario, description, source, total (+ p50/p95/p99 when sampled), profile rungs
    where           "Where the time goes" — A1 / A2 / A3 shares with a bar
    bottlenecks     top-N per_node lines
    recommendations
    hops            the hop table — top-N by ms (path index kept), or every hop
                    in path order with --all-hops
    critical_path   only when the scenario has a parallel group
    tail_risk       only when the walker sampled — which hops drive p99 − p50
    warnings

With the `load` walker (M8) there is one extra brief *before* the scenarios,
`build_capacity(findings)`: utilisation per resource across the users sweep,
p99 per scenario across the sweep, and the saturation findings (what breaks
first, at how many users, what raises the ceiling). Each scenario brief then
carries a "load" meta line with its p99 at each user count.

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
            self._tail_risk(groups.get("tail_risk", [])),
            self._warnings(f.warnings),
        ]
        return Brief(
            title=f.scenario,
            subtitle=f.description,
            meta=[("source", f.source), ("total", f"{f.total_ms:,.1f} ms"),
                  ("tail", self._percentiles(f)), ("samples", f"{f.samples:,}" if f.samples else ""),
                  ("profile", " → ".join(f.profile_sources)), ("shape", self._shape(f.shape)),
                  ("load", self._load_line(f.load))],
            sections=[s for s in sections if s.rows],
        )

    # ------------------------------------------------------------ capacity (load walker)

    def build_capacity(self, findings: list[Findings]) -> Brief | None:
        """The users-sweep brief, from the first scenario that carries a load sweep."""
        first = next((f for f in findings if f.load and f.load.get("resources")), None)
        if first is None:
            return None
        load = first.load
        users = list(load["users"])
        threshold = float(load.get("thresholds", {}).get("utilisation", 0.8))
        sections = [self._utilisation(load, users, threshold), self._p99_sweep(findings, users),
                    self._saturation(first.by_analyzer().get("saturation", []), users)]
        return Brief(
            title="users until it breaks",
            subtitle="analytic M/M/c per resource; utilisation is linear in users, so the break point is exact",
            meta=[("users", ", ".join(f"{u:,}" for u in users)),
                  ("thresholds", f"p99 {load.get('thresholds', {}).get('p99_ms', 0):,.0f} ms · utilisation {threshold:.0%}"),
                  ("tail factor", f"{load.get('tail_factor', 1.3):g} × no-contention expected (simulation.tail_factor)"),
                  ("assumes", "; ".join(load.get("assumptions", [])))],
            sections=[s for s in sections if s.rows],
        )

    def _utilisation(self, load: dict, users: list[int], threshold: float) -> Section:
        s = Section("Utilisation by users", ["resource", *[f"{u:,}" for u in users], "capacity"],
                    intro="▲ past the utilisation threshold · SAT = saturated (queue grows without bound)")
        resources = load["resources"]
        last = load["utilisation"][users[-1]]
        for key, _ in sorted(last.items(), key=lambda kv: -(kv[1] if kv[1] is not None else float("inf")))[:8]:
            r = resources[key]
            cells = [r["label"]]
            for u in users:
                rho = load["utilisation"][u].get(key, 0.0)
                if rho is None:                          # no servers at all
                    cells.append("SAT")
                    continue
                cells.append(f"{rho:.0%}" + (" SAT" if rho >= 1 else " ▲" if rho >= threshold else ""))
            cells.append(f"{r['slots']:,.0f} slots" if r.get("slots") is not None else f"{r['rps']:,.0f} rps")
            s.rows.append(Row(cells, note=clip(r["source"], 90), note_col=0))
        return s

    @staticmethod
    def _p99_sweep(findings: list[Findings], users: list[int]) -> Section:
        s = Section("p99 by users", ["scenario", *[f"{u:,}" for u in users]])
        for f in findings:
            lat = f.load.get("latency") if f.load else None
            if not lat or not f.load.get("traffic", True):
                continue
            cells = [f.scenario]
            for u in users:
                row = lat.get(u) or {}
                cells.append("SAT" if row.get("saturated") else
                             f"{row['p99_ms']:,.0f} ms" if row.get("p99_ms") is not None else "")
            s.rows.append(Row(cells))
        return s

    @staticmethod
    def _saturation(lines: list[Finding], users: list[int]) -> Section:
        s = Section("What breaks first", ["", "users", "finding"])
        for f in lines:
            if f.subject == "scenario_p99":
                continue
            kind = ("first to break" if f.subject == "first_to_break" else
                    "ceiling" if f.subject == "ceiling" else f.subject.replace("resource:", ""))
            s.rows.append(Row([kind, f"{f.latency_ms:,.0f}", f.detail]))
        return s

    @staticmethod
    def _load_line(load: dict) -> str:
        lat = load.get("latency") if load else None
        if not lat or not load.get("traffic", True):
            return ""
        parts = []
        for u, row in lat.items():
            parts.append(f"@{u:,} SAT" if row.get("saturated") else
                         f"@{u:,} p99 {row['p99_ms']:,.0f} ms" if row.get("p99_ms") is not None else f"@{u:,} —")
        return " · ".join(parts)

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
        sampled = any(h.percentiles for h in hops)
        columns = ["#", "", "hop", "ms", *(["p99"] if sampled else []), "breakdown"]
        s = Section("Hops", columns, intro=intro)
        for i, h in shown:
            marker = "" if h.on_critical_path else "·"
            name = f"{self.name(h.src)}  (wait)" if h.src == h.dst else f"{self.name(h.src)} → {self.name(h.dst)}"
            breakdown = " ".join(f"{k}={v:g}" for k, v in h.breakdown.items())
            p99 = [f"{h.percentiles['p99']:,.1f}" if h.percentiles else ""] if sampled else []
            s.rows.append(Row([str(i), marker, name, f"{h.latency_ms:,.1f}", *p99, breakdown],
                              note=clip(h.evidence), note_col=2))
        return s

    def _critical_path(self, branches: list[Finding]) -> Section:
        s = Section("Critical path", ["branch", "ms", "verdict"])
        for b in branches:
            s.rows.append(Row([b.subject, f"{b.latency_ms:,.1f}", b.detail]))
        return s

    def _tail_risk(self, lines: list[Finding]) -> Section:
        s = Section("Tail risk (p99 − p50)", ["hop", "p99 − p50", "share of spread", "why"])
        for t in lines:
            s.rows.append(Row([self.name(t.subject) if " → " in t.subject else t.subject,
                               f"{t.latency_ms:,.1f}", f"{t.share:.0%}", t.detail]))
        return s

    @staticmethod
    def _percentiles(f: Findings) -> str:
        p = f.percentiles
        if not p:
            return ""
        return " · ".join(f"{k} {p[k]:,.1f}" for k in ("p50", "p95", "p99") if k in p) + " ms"

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
