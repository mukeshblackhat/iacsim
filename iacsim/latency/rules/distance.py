"""Cost rule: network distance between src and dst placements.

same AZ → same_az; same region, different AZ → cross_az; same region but one
side has no AZ (an ALB spans AZs, a regional service like DynamoDB has none) →
same_region_unknown_az; different region → cross_region[a/b] (or
cross_region_default). A hop from EXTERNAL uses internet_to_edge. Result is
one-way; a request/response pair is 2× — the walker applies that, not this rule.
"""

from __future__ import annotations

from iacsim.core.interfaces import COST_RULES, CostRule
from iacsim.core.models import Edge, InfraGraph, NodeKind, Profile


@COST_RULES.register("distance")
class DistanceRule(CostRule):
    def cost(self, edge: Edge, graph: InfraGraph, profile: Profile) -> dict[str, float]:
        src, dst = graph.nodes[edge.src], graph.nodes[edge.dst]
        d = profile.distance

        if src.kind == NodeKind.EXTERNAL:
            return {"distance": float(d.get("internet_to_edge", 20))}

        a, b = src.placement, dst.placement
        if a.region and b.region and a.region != b.region:
            key = "/".join(sorted((a.region, b.region)))
            return {"distance": float(d.get("cross_region", {}).get(key, d.get("cross_region_default", 120)))}
        if not a.az or not b.az:
            return {"distance": float(d.get("same_region_unknown_az", 0.5))}
        if a.az != b.az:
            return {"distance": float(d.get("cross_az", 1.0))}
        return {"distance": float(d.get("same_az", 0.3))}
