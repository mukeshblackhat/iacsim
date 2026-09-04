"""Inference rule: lambda_permission                                        [M1 ✅]

aws_lambda_permission says "principal X may invoke function Y". With
source_arn we know exactly which gateway / bucket / topic X is; without it we
fall back to the only node of that kind, if there is exactly one.

    apigateway.amazonaws.com → gateway → lambda INVOKE
    s3.amazonaws.com         → bucket → lambda INVOKE   (event notification)
    sns.amazonaws.com        → topic → lambda CONSUME
    events.amazonaws.com     → no source node; skipped

High confidence: this is the actual invoke grant.
"""

from __future__ import annotations

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.graph.inference._common import first_node, raws_of_type, short

SOURCES = {
    "apigateway.amazonaws.com": ((NodeKind.GATEWAY,), EdgeKind.INVOKE),
    "s3.amazonaws.com": ((NodeKind.DATASTORE,), EdgeKind.INVOKE),
    "sns.amazonaws.com": ((NodeKind.QUEUE,), EdgeKind.CONSUME),
    "elasticloadbalancing.amazonaws.com": ((NodeKind.LB,), EdgeKind.ROUTE),
}


@INFERENCE_RULES.register("lambda_permission")
class LambdaPermissionRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        edges: list[Edge] = []
        for perm in raws_of_type(raw, "aws_lambda_permission"):
            principal = perm.attrs.get("principal")
            if principal not in SOURCES:
                continue
            kinds, edge_kind = SOURCES[principal]
            function = first_node(graph, perm.attrs.get("function_name"), [NodeKind.COMPUTE]) \
                or first_node(graph, perm.attrs.get("function_arn"), [NodeKind.COMPUTE])
            source = first_node(graph, perm.attrs.get("source_arn"), kinds) or _only_node(graph, kinds)
            if function and source:
                edges.append(Edge(
                    source, function, edge_kind, Confidence.HIGH,
                    f"{short(perm.address)} lets {principal} invoke {short(function)} "
                    f"(source_arn → {short(source)})",
                ))
        return edges


def _only_node(graph: InfraGraph, kinds) -> str | None:
    candidates = [n.id for n in graph.nodes.values() if n.kind in kinds]
    return candidates[0] if len(candidates) == 1 else None
