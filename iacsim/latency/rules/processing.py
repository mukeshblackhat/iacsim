"""Cost rule: time spent *inside* the destination node.

Picks the operation from the edge kind (READ → "read", WRITE → "write",
ROUTE → "route", INVOKE → "warm" for lambda / "handle" for servers, ...) and
looks it up via Profile.processing_for(node), which already prefers
per_resource numbers over subtype defaults.
"""

from __future__ import annotations

from iacsim.core.interfaces import COST_RULES, CostRule
from iacsim.core.models import Edge, EdgeKind, InfraGraph, Profile

# edge kind → which key inside the node's processing block to use
OPERATION_FOR_EDGE = {
    EdgeKind.READ: "read",
    EdgeKind.WRITE: "write",
    EdgeKind.ROUTE: "route",
    EdgeKind.PUBLISH: "publish",
    EdgeKind.CONSUME: "consume",
    EdgeKind.INVOKE: None,      # resolved per subtype below
    EdgeKind.PEER: None,
}
INVOKE_KEY_FOR_SUBTYPE = {
    "lambda": "warm", "ec2": "handle", "fargate": "handle",
    "api_gateway": "route", "api_gateway_v2": "route", "alb": "route",
    "step_functions": "transition", "cloudfront": "miss",
}


@COST_RULES.register("processing")
class ProcessingRule(CostRule):
    def cost(self, edge: Edge, graph: InfraGraph, profile: Profile) -> dict[str, float]:
        dst = graph.nodes[edge.dst]
        key = OPERATION_FOR_EDGE.get(edge.kind) or INVOKE_KEY_FOR_SUBTYPE.get(dst.subtype)
        if key is None:
            return {}
        value = profile.processing_for(dst).get(key)
        return {"processing": float(value)} if value is not None else {}
