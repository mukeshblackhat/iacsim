"""Inference rule: iam_policy                                               [M1 ✅]

    aws_iam_role_policy.role → a role
    compute.role / task_role_arn / orchestrator.role_arn → the same role
    policy.Statement[].Action + Resource → what that principal may touch

Actions map to operations: dynamodb:Get*/Query/Scan, s3:Get* → READ;
dynamodb:Put*/Update*/Delete*, s3:Put*/Delete* → WRITE; sqs:SendMessage,
sns:Publish → PUBLISH; lambda:InvokeFunction, states:StartExecution → INVOKE.
A statement granting both read and write yields one edge with `ops` =
[READ, WRITE] and `kind` = READ (KIND_PRIORITY — a path reads by default; a
scenario step says `op: write`). Medium confidence: permission is not proof
of a call. `jsonencode()` policies arrive structured; JSON strings are parsed.
An ECS service's roles are read from its task definition.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import (
    KIND_PRIORITY,
    Confidence,
    Edge,
    EdgeKind,
    InfraGraph,
    NodeKind,
    RawResources,
)
from iacsim.core.refs import addresses_in
from iacsim.graph.inference._common import raw_by_address, raws_of_type, short, task_definition_of

PRINCIPAL_ROLE_ATTRS = ("role", "task_role_arn", "execution_role_arn", "role_arn")
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
            holders = [x for x in (r, task_definition_of(raws, r)) if x is not None]
            for holder in holders:
                for attr in PRINCIPAL_ROLE_ATTRS:
                    for role in addresses_in(holder.attrs.get(attr)):
                        principals_for_role[role].append(node.id)

        edges: list[Edge] = []
        for policy in raws_of_type(raw, "aws_iam_role_policy"):
            roles = addresses_in(policy.attrs.get("role"))
            principals = [p for role in roles for p in principals_for_role.get(role, [])]
            if not principals:
                continue
            for statement in _statements(policy.attrs.get("policy")):
                actions = _as_list(statement.get("Action"))
                ops = _edge_kinds(actions)
                if not ops:
                    continue
                for target in addresses_in(statement.get("Resource")):
                    if target not in graph.nodes:
                        continue
                    if graph.nodes[target].kind == NodeKind.QUEUE:
                        # sqs:ReceiveMessage / DeleteMessage describe a *consumer* — that edge
                        # (queue → function) comes from the event source mapping, not from here
                        if EdgeKind.PUBLISH not in ops:
                            continue
                        ops = [EdgeKind.PUBLISH]
                    for principal in principals:
                        if principal == target:
                            continue
                        edges.append(Edge(
                            principal, target, ops[0], Confidence.MEDIUM,
                            f"IAM policy {short(policy.address)} grants {_summarise(actions)} on "
                            f"{short(target)} to the role of {short(principal)}",
                            ops=list(ops),
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


def _edge_kinds(actions: list[str]) -> list[EdgeKind]:
    """Every operation the statement allows, in KIND_PRIORITY order (first is priced)."""
    lowered = [a.lower() for a in actions if isinstance(a, str)]
    found: set[EdgeKind] = set()
    if any(a in INVOKE_ACTIONS or a.endswith(":*") and a.startswith(("lambda", "states")) for a in lowered):
        found.add(EdgeKind.INVOKE)
    if any(a in PUBLISH_ACTIONS for a in lowered):
        found.add(EdgeKind.PUBLISH)
    verbs = [a.split(":", 1)[1] if ":" in a else a for a in lowered]
    if any(v == "*" or v.startswith(WRITE_VERBS) for v in verbs):
        found.add(EdgeKind.WRITE)
    if any(v == "*" or v.startswith(READ_VERBS) for v in verbs):
        found.add(EdgeKind.READ)
    return sorted(found, key=KIND_PRIORITY.index)


def _summarise(actions: list[str]) -> str:
    shown = [a for a in actions if isinstance(a, str)][:3]
    return ", ".join(shown) + (", …" if len(actions) > 3 else "")
