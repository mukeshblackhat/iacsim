"""Inference rule: target_group                                             [M1 ✅]

    aws_lb_listener.load_balancer_arn  → the LB
    aws_lb_listener.default_action.target_group_arn (or forward.target_group) → target group
    aws_lb_target_group_attachment.{target_group_arn, target_id} → instance
    aws_ecs_service.load_balancer.target_group_arn → service
    aws_autoscaling_group.target_group_arns / aws_autoscaling_attachment → ASG
    aws_elb.instances (classic ELB) → instances directly

emits lb → compute ROUTE edges. High confidence: this is the actual routing
configuration.
"""

from __future__ import annotations

from collections import defaultdict

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.core.refs import addresses_in
from iacsim.graph.inference._common import first_node, raws_of_type, short


@INFERENCE_RULES.register("target_group")
class TargetGroupRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        # target group address → list of (lb address, listener address)
        lbs_for_tg: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for listener in raws_of_type(raw, "aws_lb_listener", "aws_alb_listener"):
            lb = first_node(graph, listener.attrs.get("load_balancer_arn"), [NodeKind.LB])
            if not lb:
                continue
            for tg in addresses_in(listener.attrs.get("default_action")):
                lbs_for_tg[tg].append((lb, listener.address))

        # target group address → list of (compute address, evidence source)
        targets_for_tg: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for att in raws_of_type(raw, "aws_lb_target_group_attachment", "aws_alb_target_group_attachment"):
            tg = next(iter(addresses_in(att.attrs.get("target_group_arn"))), None)
            target = first_node(graph, att.attrs.get("target_id"), [NodeKind.COMPUTE])
            if tg and target:
                targets_for_tg[tg].append((target, att.address))
        for svc in raws_of_type(raw, "aws_ecs_service"):
            if svc.address in graph.nodes:
                for tg in addresses_in(svc.attrs.get("load_balancer")):
                    targets_for_tg[tg].append((svc.address, svc.address))
        for asg in raws_of_type(raw, "aws_autoscaling_group"):
            if asg.address in graph.nodes:
                for tg in addresses_in(asg.attrs.get("target_group_arns")):
                    targets_for_tg[tg].append((asg.address, asg.address))
        for att in raws_of_type(raw, "aws_autoscaling_attachment"):
            tg = next(iter(addresses_in(att.attrs.get("lb_target_group_arn"))
                           or addresses_in(att.attrs.get("alb_target_group_arn"))), None)
            asg = first_node(graph, att.attrs.get("autoscaling_group_name"), [NodeKind.COMPUTE])
            if tg and asg:
                targets_for_tg[tg].append((asg, att.address))

        edges = []
        for elb in raws_of_type(raw, "aws_elb"):                 # classic ELB routes straight to instances
            if elb.address not in graph.nodes:
                continue
            for target in addresses_in(elb.attrs.get("instances")):
                if target in graph.nodes and graph.nodes[target].kind == NodeKind.COMPUTE:
                    edges.append(Edge(src=elb.address, dst=target, kind=EdgeKind.ROUTE, confidence=Confidence.HIGH,
                                      evidence=f"{short(elb.address)} instances lists {short(target)}"))
        for tg, lbs in lbs_for_tg.items():
            for lb, listener in lbs:
                for target, via in targets_for_tg.get(tg, []):
                    edges.append(Edge(
                        lb, target, EdgeKind.ROUTE, Confidence.HIGH,
                        f"listener {short(listener)} forwards to target group {short(tg)}; "
                        f"{short(via)} registers {short(target)}",
                    ))
        return edges
