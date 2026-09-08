"""Inference rule: vpc_peering                                              [M1 ✅]

    aws_vpc_peering_connection.{vpc_id, peer_vpc_id}      (+ peer_region)
    google_compute_network_peering.{network, peer_network}

→ vpc ↔ vpc PEER edge. Not a request hop by itself, but it tells the distance
rule that traffic between those VPCs crosses a peering link (and, with
peer_region, a region boundary). One rule for both clouds (G15): the code is
provider-neutral, only the type and attribute names differ, so they live in a
table. A GCP peering is declared once per side, so both directions get an edge.
"""

from __future__ import annotations

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.graph.inference._common import first_node, raws_of_type, short

# resource type → (this side, the other side, the attribute naming the peer's region or None)
PEERINGS: dict[str, tuple[str, str, str | None]] = {
    "aws_vpc_peering_connection": ("vpc_id", "peer_vpc_id", "peer_region"),
    "google_compute_network_peering": ("network", "peer_network", None),
}


@INFERENCE_RULES.register("vpc_peering")
class VpcPeeringRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        edges: list[Edge] = []
        for rtype, (this_side, other_side, region_attr) in PEERINGS.items():
            for pcx in raws_of_type(raw, rtype):
                a = first_node(graph, pcx.attrs.get(this_side), [NodeKind.NETWORK])
                b = first_node(graph, pcx.attrs.get(other_side), [NodeKind.NETWORK])
                if a and b:
                    region = pcx.attrs.get(region_attr) if region_attr else None
                    where = f" (peer region {region})" if isinstance(region, str) else ""
                    edges.append(Edge(a, b, EdgeKind.PEER, Confidence.HIGH,
                                      f"{short(pcx.address)} peers {short(a)} with {short(b)}{where}"))
        return edges
