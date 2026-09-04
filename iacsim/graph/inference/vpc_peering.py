"""Inference rule: vpc_peering                                              [M1 ✅]

aws_vpc_peering_connection.{vpc_id, peer_vpc_id} → vpc ↔ vpc PEER edge. Not
a request hop by itself, but it tells the distance rule that traffic between
those VPCs crosses a peering link (and, with peer_region, a region boundary).
"""

from __future__ import annotations

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.graph.inference._common import first_node, raws_of_type, short


@INFERENCE_RULES.register("vpc_peering")
class VpcPeeringRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        edges: list[Edge] = []
        for pcx in raws_of_type(raw, "aws_vpc_peering_connection"):
            a = first_node(graph, pcx.attrs.get("vpc_id"), [NodeKind.NETWORK])
            b = first_node(graph, pcx.attrs.get("peer_vpc_id"), [NodeKind.NETWORK])
            if a and b:
                region = pcx.attrs.get("peer_region")
                where = f" (peer region {region})" if isinstance(region, str) else ""
                edges.append(Edge(a, b, EdgeKind.PEER, Confidence.HIGH,
                                  f"{short(pcx.address)} peers {short(a)} with {short(b)}{where}"))
        return edges
