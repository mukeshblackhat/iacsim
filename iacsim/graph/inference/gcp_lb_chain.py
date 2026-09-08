"""Inference rule: gcp_lb_chain                                             [M11]

    google_compute_(global_)forwarding_rule.target              → target_*_proxy
    google_compute_(global_)forwarding_rule.backend_service     → backend_service (internal / passthrough LB)
    google_compute_(region_)target_{http,https}_proxy.url_map   → url_map
    google_compute_(region_)target_{tcp,ssl}_proxy.backend_service → backend_service
    google_compute_(region_)url_map.default_service, path_matcher[].default_service,
        path_matcher[].path_rule[].service, route_rules[].service,
        *.weighted_backend_services[].backend_service           → backend_service | backend_bucket
    google_compute_(region_)backend_service.backend[].group     → NEG | instance group (manager)
    google_compute_backend_bucket.bucket_name                   → the gcs bucket
    NEG.cloud_run.service / cloud_function.function / app_engine.service → the serverless node
    google_compute_network_endpoint.{network_endpoint_group, instance} → a VM behind a zonal NEG
    google_compute_instance_group.instances                     → its VMs

emits one ROUTE edge per link, HIGH confidence — this is the actual routing
configuration. Global and regional twins share one code path because the
normaliser gave them one subtype. Every link is a node (G3), so most hops are a
direct attribute follow; the NEG → VM join goes through `google_compute_network_endpoint`
glue with `target_group`'s two-dict shape (G17). Joins are on the resolved
*address* — `.id`, `.self_link` and `.name` all name the same node (02-TYPE-MAP.md,
"Reference-style trap").

Deliberately left: a `default_url_redirect` url_map is a terminal, not a broken
chain (no edge, no warning); an `app_engine {}` NEG whose service is a literal
name does not resolve (App Engine versions carry `service`, not a URL, and the
subtype is unverified); a MIG is the compute node, so its VMs are not expanded.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, Node, NodeKind, RawResources
from iacsim.core.refs import addresses_in
from iacsim.graph.inference._common import blocks, raw_by_address, raws_of_type, short

# subtype of the source node → the attribute paths it routes through (dotted; list
# blocks are walked at every level) → the subtypes a resolved address may have.
LINKS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "forwarding_rule": (("target", ("target_proxy",)), ("backend_service", ("backend_service",))),
    "target_proxy": (("url_map", ("url_map",)), ("backend_service", ("backend_service",))),
    "url_map": (
        ("default_service", ("backend_service", "backend_bucket")),
        ("path_matcher.default_service", ("backend_service", "backend_bucket")),
        ("path_matcher.path_rule.service", ("backend_service", "backend_bucket")),
        ("path_matcher.route_rules.service", ("backend_service", "backend_bucket")),
        ("path_matcher.route_rules.route_action.weighted_backend_services.backend_service",
         ("backend_service", "backend_bucket")),
        ("default_route_action.weighted_backend_services.backend_service", ("backend_service", "backend_bucket")),
        ("path_matcher.default_route_action.weighted_backend_services.backend_service",
         ("backend_service", "backend_bucket")),
    ),
    "backend_service": (("backend.group", ("neg", "gce")),),
    "backend_bucket": (("bucket_name", ("gcs",)),),
    "neg": (
        ("cloud_run.service", ("cloud_run",)),
        ("cloud_function.function", ("cloud_functions", "cloud_functions_v2")),
        ("app_engine.service", ("app_engine",)),
    ),
}
NEG_ENDPOINT_TYPES = ("google_compute_network_endpoint", "google_compute_region_network_endpoint",
                      "google_compute_global_network_endpoint")


@INFERENCE_RULES.register("gcp_lb_chain")
class GcpLbChainRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)
        edges: list[Edge] = []
        for node in graph.nodes.values():
            if node.subtype not in LINKS or (r := raws.get(node.id)) is None:
                continue
            for path, subtypes in LINKS[node.subtype]:
                for dst in _addresses_at(r.attrs, path.split(".")):
                    target = graph.nodes.get(dst)
                    if target is not None and target.subtype in subtypes and target.id != node.id:
                        edges.append(_route(node, target, path))
        edges.extend(self._zonal_neg_members(graph, raw))
        edges.extend(self._instance_group_members(graph, raw))
        return edges

    @staticmethod
    def _zonal_neg_members(graph: InfraGraph, raw: RawResources) -> list[Edge]:
        """NEG → VM through the endpoint glue: {neg → [(vm, endpoint)]} (G17)."""
        members: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for endpoint in raws_of_type(raw, *NEG_ENDPOINT_TYPES):
            neg = next(iter(addresses_in(endpoint.attrs.get("network_endpoint_group"))), None)
            vm = next((a for a in addresses_in(endpoint.attrs.get("instance"))
                       if a in graph.nodes and graph.nodes[a].kind == NodeKind.COMPUTE), None)
            if neg in graph.nodes and vm:
                members[neg].append((vm, endpoint.address))
        return [
            Edge(neg, vm, EdgeKind.ROUTE, Confidence.HIGH,
                 f"{short(endpoint)} registers {short(vm)} in {short(neg)}")
            for neg, vms in members.items() for vm, endpoint in vms
        ]

    @staticmethod
    def _instance_group_members(graph: InfraGraph, raw: RawResources) -> list[Edge]:
        """A plain (unmanaged) instance group lists its VMs; a MIG is itself the node."""
        edges: list[Edge] = []
        for group in raws_of_type(raw, "google_compute_instance_group"):
            if group.address not in graph.nodes:
                continue
            for vm in addresses_in(group.attrs.get("instances")):
                if vm in graph.nodes and graph.nodes[vm].kind == NodeKind.COMPUTE and vm != group.address:
                    edges.append(Edge(group.address, vm, EdgeKind.ROUTE, Confidence.HIGH,
                                      f"{short(group.address)} instances lists {short(vm)}"))
        return edges


def _route(src: Node, dst: Node, path: str) -> Edge:
    return Edge(src.id, dst.id, EdgeKind.ROUTE, Confidence.HIGH,
                f"{src.subtype} {short(src.id)} {path} routes to {dst.subtype} {short(dst.id)}")


def _addresses_at(value: Any, path: list[str]) -> Iterator[str]:
    """Addresses referenced at a dotted attribute path; a repeated block at any
    level is a list and every repetition is walked."""
    if not path:
        yield from addresses_in(value)
        return
    head, rest = path[0], path[1:]
    for holder in blocks(value) if isinstance(value, list) else [value]:
        if isinstance(holder, dict) and head in holder:
            yield from _addresses_at(holder[head], rest)
