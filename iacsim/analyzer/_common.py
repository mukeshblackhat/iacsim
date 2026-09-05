"""Helpers shared by the analyzers: which hops count toward the total, how a
hop's network cost is classified (A1), and layer labels for breakdown keys."""

from __future__ import annotations

from iacsim.core.models import HopResult, InfraGraph, Result

# breakdown key → latency layer (SPEC §1a). Anything else is reported under "other".
LAYER_OF = {
    "distance": "A1",
    "processing": "A2",
    "cold_start": "A2",
    "transition": "A2",
    "wait": "A3",
}
LAYER_NAMES = {"A1": "distance", "A2": "service", "A3": "shape"}


def counted_hops(result: Result) -> list[HopResult]:
    """Hops that add up to `total_ms` — everything on the critical path."""
    return [h for h in result.hops if h.on_critical_path]


def is_wait(hop: HopResult) -> bool:
    return hop.src == hop.dst and "wait" in hop.breakdown


def distance_class(hop: HopResult, graph: InfraGraph) -> str:
    """'internet' | 'cross_region' | 'cross_az' | 'same_az' | 'same_region' (AZ unknown on a side,
    e.g. an ALB that spans AZs) | 'unknown' for a hop."""
    src, dst = graph.nodes.get(hop.src), graph.nodes.get(hop.dst)
    if src is None or dst is None:
        return "unknown"
    if src.kind == "external":
        return "internet"
    a, b = src.placement, dst.placement
    if a.region and b.region and a.region != b.region:
        return "cross_region"
    if a.az and b.az:
        return "cross_az" if a.az != b.az else "same_az"
    return "same_region"


def region_of(graph: InfraGraph, node_id: str) -> str | None:
    node = graph.nodes.get(node_id)
    return node.placement.region if node else None
