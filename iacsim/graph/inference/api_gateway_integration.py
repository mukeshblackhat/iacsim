"""Inference rule: api_gateway_integration                                  [M1 ✅]

    aws_api_gateway_integration.{rest_api_id, uri}     → REST API → lambda
    aws_apigatewayv2_integration.{api_id, integration_uri} → HTTP API → lambda

The evidence names the method and path when the sibling method / resource
blocks are present. High confidence: this is the actual route.
"""

from __future__ import annotations

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.core.refs import addresses_in
from iacsim.graph.inference._common import first_node, raw_by_address, raws_of_type, short


@INFERENCE_RULES.register("api_gateway_integration")
class ApiGatewayIntegrationRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)
        edges: list[Edge] = []
        for integ in raws_of_type(raw, "aws_api_gateway_integration", "aws_apigatewayv2_integration"):
            api = first_node(graph, integ.attrs.get("rest_api_id") or integ.attrs.get("api_id"), [NodeKind.GATEWAY])
            target = first_node(graph, integ.attrs.get("uri") or integ.attrs.get("integration_uri"), [NodeKind.COMPUTE])
            if not (api and target):
                continue
            edges.append(Edge(
                api, target, EdgeKind.INVOKE, Confidence.HIGH,
                f"{short(integ.address)} routes {_method(integ, raws)} {_path(integ, raws)} to {short(target)}",
            ))
        return edges


def _method(integ, raws) -> str:
    value = integ.attrs.get("http_method")
    if isinstance(value, str) and "${" in value:                 # "${aws_api_gateway_method.x.http_method}"
        for addr in addresses_in(value):
            if addr in raws:
                value = raws[addr].attrs.get("http_method", value)
    return value if isinstance(value, str) else "ANY"


def _path(integ, raws) -> str:
    for addr in addresses_in(integ.attrs.get("resource_id")):
        if addr in raws and isinstance(raws[addr].attrs.get("path_part"), str):
            return "/" + raws[addr].attrs["path_part"]
    return "/"
