"""Cost rule: time spent *inside* the destination node.

Picks the operation from the edge kind (READ → "read", WRITE → "write",
ROUTE → "route", INVOKE → "warm" for lambda / "handle" for servers, ...) and
looks it up via Profile.processing_for(node), which already merges defaults ←
by_label ← per_resource.

When the destination has no number for that operation (an ALB ROUTEs into an
EC2 box, whose block only knows `handle`; a synthetic INVOKE lands on a table,
whose block only knows `read`/`write`) the rule falls back to the subtype's
INVOKE key so the hop is never silently free.

A CONSUME hop (queue → poller, from an event source mapping) is the one case
where the number lives on the *source*: `sqs.consume` is the poll delay before
the consumer even starts, so the hop charges that plus the consumer's own
invoke cost (`warm` / `handle`).

`cloudfront.hit` is deliberately unused: CDN hops are priced as a miss — the
worst case — until cache behaviour becomes an input.
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
    "dynamodb": "read", "rds": "read", "s3": "read", "elasticache": "read",
    "sqs": "publish", "sns": "publish", "kinesis": "publish",
}


@COST_RULES.register("processing")
class ProcessingRule(CostRule):
    def cost(self, edge: Edge, graph: InfraGraph, profile: Profile) -> dict[str, float]:
        dst = graph.nodes[edge.dst]
        block = profile.processing_for(dst)
        key = OPERATION_FOR_EDGE.get(edge.kind)
        if edge.kind == EdgeKind.CONSUME:
            src = graph.nodes.get(edge.src)
            poll = float(profile.processing_for(src).get("consume", 0)) if src else 0.0
            own = block.get(INVOKE_KEY_FOR_SUBTYPE.get(dst.subtype, ""))
            return {"processing": poll + float(own or 0)}
        if key is None or key not in block:
            key = INVOKE_KEY_FOR_SUBTYPE.get(dst.subtype)      # fallback: what a call into this node costs
        value = block.get(key) if key else None
        return {"processing": float(value)} if value is not None else {}
