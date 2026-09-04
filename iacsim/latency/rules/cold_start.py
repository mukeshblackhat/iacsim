"""Cost rule: Lambda cold start, blended by probability for the deterministic
walker: expected = cold_prob × cold. The Monte-Carlo walker reads cold_prob
and cold from the same profile block and samples instead.
"""

from __future__ import annotations

from iacsim.core.interfaces import COST_RULES, CostRule
from iacsim.core.models import Edge, InfraGraph, Profile


@COST_RULES.register("cold_start")
class ColdStartRule(CostRule):
    def cost(self, edge: Edge, graph: InfraGraph, profile: Profile) -> dict[str, float]:
        dst = graph.nodes[edge.dst]
        if dst.subtype != "lambda":
            return {}
        p = profile.processing_for(dst)
        cold, prob = p.get("cold"), p.get("cold_prob")
        if cold is None or prob is None:
            return {}
        return {"cold_start": float(cold) * float(prob)}
