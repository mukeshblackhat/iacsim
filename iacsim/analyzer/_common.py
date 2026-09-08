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


# Human names for the subtypes that pay a cold start (Normaliser.COLD_START). The
# report used to say "Lambda" for every cold start; a Cloud Run one now says so.
_COLD_START_SERVICE = {
    "lambda": "Lambda", "cloud_run": "Cloud Run", "cloud_run_job": "Cloud Run",
    "cloud_functions": "Cloud Functions", "cloud_functions_v2": "Cloud Functions", "app_engine": "App Engine",
}


def cold_start_service(graph: InfraGraph, node_ids) -> str:
    """'Lambda', 'Cloud Run', or 'Cloud Run / Lambda' for a mixed set."""
    names = {_COLD_START_SERVICE.get(graph.nodes[n].subtype, graph.nodes[n].subtype)
             for n in node_ids if n in graph.nodes}
    return " / ".join(sorted(names)) or "Lambda"


def cold_start_fix(service: str) -> tuple[str, str]:
    """(finding title stem, the remedy sentence) — Lambda's provisioned concurrency
    has a different name on every other platform."""
    if service == "Lambda":
        return "provisioned concurrency", "provisioned concurrency (or a smaller package / SnapStart) removes it"
    return "a minimum instance count", "a minimum instance count (min_instances) keeps a warm instance and removes it"
