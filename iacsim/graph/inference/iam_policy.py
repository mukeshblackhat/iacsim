"""Inference rule: iam_policy                                               [M1 ✅]

    aws_iam_role_policy.role → a role
    compute.role / task_role_arn / orchestrator.role_arn → the same role
    policy.Statement[].Action + Resource → what that principal may touch

Actions map to edge kinds: dynamodb:Get*/Query/Scan, s3:Get* → READ;
dynamodb:Put*/Update*/Delete*, s3:Put*/Delete* → WRITE (WRITE wins if both);
sqs:SendMessage, sns:Publish → PUBLISH; lambda:InvokeFunction,
states:StartExecution → INVOKE. Medium confidence: permission is not proof of
a call. `jsonencode()` policies arrive structured; JSON strings are parsed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.core.refs import addresses_in
from iacsim.graph.inference._common import raw_by_address, raws_of_type, short

PRINCIPAL_ROLE_ATTRS = ("role", "task_role_arn", "role_arn")
WRITE_VERBS = ("put", "update", "delete", "batchwrite", "write")
READ_VERBS = ("get", "query", "scan", "batchget", "list", "describe", "read")
PUBLISH_ACTIONS = ("sqs:sendmessage", "sns:publish", "kinesis:putrecord")
INVOKE_ACTIONS = ("lambda:invokefunction", "lambda:invoke", "states:startexecution", "states:startsyncexecution")


@INFERENCE_RULES.register("iam_policy")
class IamPolicyRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)

        principals_for_role: dict[str, list[str]] = defaultdict(list)
        for node in graph.nodes.values():
            if node.kind not in (NodeKind.COMPUTE, NodeKind.ORCHESTRATOR):
                continue
            r = raws.get(node.id)
            for attr in PRINCIPAL_ROLE_ATTRS:
                for role in addresses_in(r.attrs.get(attr)) if r else []:
                    principals_for_role[role].append(node.id)

        edges: list[Edge] = []
        for policy in raws_of_type(raw, "aws_iam_role_policy"):
            roles = addresses_in(policy.attrs.get("role"))
            principals = [p for role in roles for p in principals_for_role.get(role, [])]
            if not principals:
                continue
            for statement in _statements(policy.attrs.get("policy")):
                actions = _as_list(statement.get("Action"))
                kind = _edge_kind(actions)
                if kind is None:
                    continue
                for target in addresses_in(statement.get("Resource")):
                    if target not in graph.nodes:
                        continue
                    for principal in principals:
                        if principal == target:
                            continue
                        edges.append(Edge(
                            principal, target, kind, Confidence.MEDIUM,
                            f"IAM policy {short(policy.address)} grants {_summarise(actions)} on "
                            f"{short(target)} to the role of {short(principal)}",
                        ))
        return edges


def _statements(policy: Any) -> list[dict]:
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except ValueError:
            return []
    if not isinstance(policy, dict):
        return []
    return [s for s in _as_list(policy.get("Statement")) if isinstance(s, dict) and s.get("Effect", "Allow") == "Allow"]


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _edge_kind(actions: list[str]) -> EdgeKind | None:
    lowered = [a.lower() for a in actions if isinstance(a, str)]
    if any(a in INVOKE_ACTIONS or a.endswith(":*") and a.startswith(("lambda", "states")) for a in lowered):
        return EdgeKind.INVOKE
    if any(a in PUBLISH_ACTIONS for a in lowered):
        return EdgeKind.PUBLISH
    verbs = [a.split(":", 1)[1] if ":" in a else a for a in lowered]
    if any(v == "*" or v.startswith(WRITE_VERBS) for v in verbs):
        return EdgeKind.WRITE
    if any(v.startswith(READ_VERBS) for v in verbs):
        return EdgeKind.READ
    return None


def _summarise(actions: list[str]) -> str:
    shown = [a for a in actions if isinstance(a, str)][:3]
    return ", ".join(shown) + (", …" if len(actions) > 3 else "")
