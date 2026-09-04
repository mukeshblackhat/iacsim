"""Inference rule: event_source_mapping                                     [M1 ✅]

aws_lambda_event_source_mapping wires SQS / Kinesis / DynamoDB streams to a
Lambda that polls them: queue → compute CONSUME. High confidence.
"""

from __future__ import annotations

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.graph.inference._common import first_node, raws_of_type, short


@INFERENCE_RULES.register("event_source_mapping")
class EventSourceMappingRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        edges: list[Edge] = []
        for esm in raws_of_type(raw, "aws_lambda_event_source_mapping"):
            source = first_node(graph, esm.attrs.get("event_source_arn"), [NodeKind.QUEUE, NodeKind.DATASTORE])
            function = first_node(graph, esm.attrs.get("function_name"), [NodeKind.COMPUTE])
            if source and function:
                edges.append(Edge(
                    source, function, EdgeKind.CONSUME, Confidence.HIGH,
                    f"{short(esm.address)} makes {short(function)} poll {short(source)}",
                ))
        return edges
