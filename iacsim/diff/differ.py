"""`iacsim diff` — compare two IaC snapshots.

Runs the full pipeline on both targets with the *same* latency profile (taken
from the before-side config, so the numbers differ only because the
infrastructure does), then aligns:

    graph        nodes by id (or label with align_by="label"): added / removed /
                 moved (region, az or vpc changed); edges by (src, dst, kind)
    scenarios    by name; one-sided scenarios are listed, not errors
    categories   per_category additive findings by subject   (A1 / A2 / A3 shift)
    hops         by hop label + occurrence index, so the 2nd call to a table
                 lines up with the 2nd call; |delta| < 0.05 ms counts as unchanged
    nodes        per_node findings by subject
    recs         recommendations by subject: appeared / disappeared / unchanged

Everything here works on Findings + InfraGraph only; rendering goes through
the reporter registry (`Reporter.render_diff`).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from iacsim.core.config import Config
from iacsim.core.models import Finding, Findings, HopResult, InfraGraph
from iacsim.diff.models import (
    CHANGE_THRESHOLD_MS,
    DiffReport,
    GraphDiff,
    HopDelta,
    NodeMove,
    RecommendationDelta,
    ScenarioDiff,
    ValueDelta,
)

PLACEMENT_FIELDS = ("region", "az", "vpc")


# ------------------------------------------------------------------ entry points

def run_diff(before: Path, before_cfg: Config, after: Path, after_cfg: Config,
             scenario: str | None = None, align_by: str = "id") -> tuple[DiffReport, InfraGraph, InfraGraph]:
    """Run both pipelines and diff them. Returns the report plus both graphs
    (reporters need them for display names)."""
    from iacsim.core import pipeline

    after_cfg.set("latency.profiles", before_cfg.get("latency.profiles"))
    b, a = pipeline.run(before, before_cfg), pipeline.run(after, after_cfg)
    b_findings = [f for f in b.findings if scenario is None or f.scenario == scenario]
    a_findings = [f for f in a.findings if scenario is None or f.scenario == scenario]
    report = diff_reports(b_findings, a_findings, b.graph, a.graph, align_by=align_by)
    report.before, report.after = str(before), str(after)
    return report, b.graph, a.graph


def diff_reports(before: list[Findings], after: list[Findings],
                 before_graph: InfraGraph, after_graph: InfraGraph,
                 align_by: str = "id") -> DiffReport:
    """Data-level diff of two pipeline outputs."""
    profile = (after or before or [Findings("", 0, [], [])])[0].profile_sources
    b_by, a_by = {f.scenario: f for f in before}, {f.scenario: f for f in after}
    names = list(dict.fromkeys([*b_by, *a_by]))
    scenarios = [diff_scenario(b_by.get(n), a_by.get(n)) for n in names]
    return DiffReport(before="before", after="after", profile_sources=profile,
                      graph=diff_graphs(before_graph, after_graph, align_by=align_by),
                      scenarios=scenarios)


# ------------------------------------------------------------------ graph level

def diff_graphs(before: InfraGraph, after: InfraGraph, align_by: str = "id") -> GraphDiff:
    """Added / removed / moved nodes and added / removed edges. `align_by="label"`
    matches nodes on their label instead of their id — for comparing the same
    stack expressed in two IaC formats (Terraform address ≠ CloudFormation
    logical id)."""
    key = _node_key(align_by)
    b_nodes = {key(n): n for n in before.nodes.values()}
    a_nodes = {key(n): n for n in after.nodes.values()}

    diff = GraphDiff(
        nodes_added=sorted(a_nodes[k].id for k in a_nodes.keys() - b_nodes.keys()),
        nodes_removed=sorted(b_nodes[k].id for k in b_nodes.keys() - a_nodes.keys()),
    )
    for k in sorted(b_nodes.keys() & a_nodes.keys()):
        for field_name in PLACEMENT_FIELDS:
            b_val, a_val = getattr(b_nodes[k].placement, field_name), getattr(a_nodes[k].placement, field_name)
            if b_val != a_val and _counts_as_move(field_name, align_by):
                diff.nodes_moved.append(NodeMove(a_nodes[k].id, field_name, b_val, a_val))

    # Compare edges by the alignment key, but report them by node id: the dashboard
    # and the tables resolve `src → dst` back to nodes, and a label is not an address.
    b_edges = {_edge_key(e, before, key): _edge_key(e, before, _node_key("id")) for e in before.edges}
    a_edges = {_edge_key(e, after, key): _edge_key(e, after, _node_key("id")) for e in after.edges}
    diff.edges_added = sorted(a_edges[k] for k in a_edges.keys() - b_edges.keys())
    diff.edges_removed = sorted(b_edges[k] for k in b_edges.keys() - a_edges.keys())
    return diff


def _node_key(align_by: str):
    if align_by == "id":
        return lambda n: n.id
    if align_by == "label":
        return lambda n: n.label or n.id
    raise ValueError(f"align_by must be 'id' or 'label', got {align_by!r}")


def _edge_key(edge, graph: InfraGraph, key) -> str:
    src = key(graph.nodes[edge.src]) if edge.src in graph.nodes else edge.src
    dst = key(graph.nodes[edge.dst]) if edge.dst in graph.nodes else edge.dst
    return f"{src} → {dst} ({edge.kind})"


def _counts_as_move(field_name: str, align_by: str) -> bool:
    """When aligning by label the vpc field holds format-specific ids on both
    sides, so a differing vpc *id* is not a move; region and az always are."""
    return not (align_by == "label" and field_name == "vpc")


# ------------------------------------------------------------------ scenario level

def diff_scenario(before: Findings | None, after: Findings | None) -> ScenarioDiff:
    if before is None or after is None:
        present = after or before
        return ScenarioDiff(
            name=present.scenario, status="only_after" if before is None else "only_before",
            before_ms=before.total_ms if before else None,
            after_ms=after.total_ms if after else None,
            description=present.description,
            shape_before=before.shape if before else {}, shape_after=after.shape if after else {},
        )
    b_groups, a_groups = before.by_analyzer(), after.by_analyzer()
    return ScenarioDiff(
        name=after.scenario, status="both",
        before_ms=before.total_ms, after_ms=after.total_ms,
        description=after.description or before.description,
        categories=diff_values(b_groups.get("per_category", []), a_groups.get("per_category", []),
                               additive_only=True),
        hops=diff_hops(before.hops, after.hops),
        nodes=diff_values(b_groups.get("per_node", []), a_groups.get("per_node", [])),
        recommendations=diff_recommendations(b_groups.get("recommendations", []),
                                             a_groups.get("recommendations", [])),
        shape_before=before.shape, shape_after=after.shape,
    )


def diff_values(before: list[Finding], after: list[Finding], additive_only: bool = False) -> list[ValueDelta]:
    """Align findings by subject. Order: after-side order, then before-only subjects."""
    if additive_only:
        before, after = [f for f in before if f.additive], [f for f in after if f.additive]
    b_by, a_by = {f.subject: f for f in before}, {f.subject: f for f in after}
    out = []
    for subject in list(dict.fromkeys([*a_by, *b_by])):
        b, a = b_by.get(subject), a_by.get(subject)
        out.append(ValueDelta(
            subject=subject,
            before_ms=b.latency_ms if b else None, after_ms=a.latency_ms if a else None,
            before_share=b.share if b else None, after_share=a.share if a else None,
            layer=(a or b).layer, detail=(a or b).detail,
        ))
    return out


def diff_hops(before: list[HopResult], after: list[HopResult]) -> list[HopDelta]:
    """Align by (label, occurrence). Output order: after-side path order, then
    removed hops in before-side path order."""
    b_keys, a_keys = _occurrence_keys(before), _occurrence_keys(after)
    b_index = {k: (i, h) for i, (k, h) in enumerate(zip(b_keys, before, strict=True), 1)}
    a_index = {k: (i, h) for i, (k, h) in enumerate(zip(a_keys, after, strict=True), 1)}

    def make(k) -> HopDelta:
        b, a = b_index.get(k), a_index.get(k)
        return HopDelta(
            label=k[0], occurrence=k[1],
            index_before=b[0] if b else None, index_after=a[0] if a else None,
            before_ms=b[1].latency_ms if b else None, after_ms=a[1].latency_ms if a else None,
            breakdown_before=dict(b[1].breakdown) if b else {},
            breakdown_after=dict(a[1].breakdown) if a else {},
        )

    ordered = list(dict.fromkeys([*a_keys, *b_keys]))
    return [make(k) for k in ordered]


def _occurrence_keys(hops: list[HopResult]) -> list[tuple[str, int]]:
    seen: Counter[str] = Counter()
    keys = []
    for h in hops:
        seen[h.label] += 1
        keys.append((h.label, seen[h.label]))
    return keys


def diff_recommendations(before: list[Finding], after: list[Finding]) -> list[RecommendationDelta]:
    b_by, a_by = {f.subject: f for f in before}, {f.subject: f for f in after}
    out = []
    for subject, f in a_by.items():
        status = "unchanged" if subject in b_by else "appeared"
        out.append(RecommendationDelta(subject, status, f.latency_ms, f.detail))
    for subject, f in b_by.items():
        if subject not in a_by:
            out.append(RecommendationDelta(subject, "disappeared", f.latency_ms, f.detail))
    return out


# ------------------------------------------------------------------ CI threshold

_THRESHOLD = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|%)\s*$")


def parse_threshold(text: str) -> tuple[str, float]:
    """'50ms' → ("ms", 50.0); '10%' → ("percent", 10.0)."""
    m = _THRESHOLD.match(text)
    if not m:
        raise ValueError(f"--fail-on-regression expects e.g. '50ms' or '10%', got {text!r}")
    value, unit = float(m.group(1)), m.group(2)
    return ("ms" if unit == "ms" else "percent", value)


def summarise(diff: DiffReport) -> str:
    """One line for the CLI / CI log."""
    changed = [s for s in diff.scenarios if s.status == "both" and abs(s.delta_ms) >= CHANGE_THRESHOLD_MS]
    if diff.is_empty:
        return "no latency change"
    parts = [f"{s.name}: {s.before_ms:,.1f} → {s.after_ms:,.1f} ms ({s.delta_ms:+,.1f})" for s in changed]
    return "; ".join(parts) if parts else "graph changed, scenario totals unchanged"
