"""Small helpers shared by the inference rules (same stage, so importing is fine)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from iacsim.core.models import InfraGraph, NodeKind, RawResource, RawResources
from iacsim.core.refs import addresses_in, split_address


def raws_of_type(raw: RawResources, *types: str) -> list[RawResource]:
    return [r for r in raw.resources if r.type in types]


def raw_by_address(raw: RawResources) -> dict[str, RawResource]:
    return {r.address: r for r in raw.resources}


def first_node(graph: InfraGraph, value: Any, kinds: Iterable[NodeKind] | None = None) -> str | None:
    """First address mentioned in `value` that is a node in the graph (optionally of given kinds)."""
    wanted = set(kinds) if kinds else None
    for address in addresses_in(value):
        node = graph.nodes.get(address)
        if node and (wanted is None or node.kind in wanted):
            return address
    return None


def task_definition_of(raws: dict[str, RawResource], r: RawResource | None) -> RawResource | None:
    """An ECS service's container config lives on its task definition (glue,
    not a node): follow `task_definition` so env vars and roles can be read
    as if they were on the service."""
    if r is None or r.type not in ("aws_ecs_service",):
        return None
    for address in addresses_in(r.attrs.get("task_definition")):
        td = raws.get(address)
        if td is not None and td.type == "aws_ecs_task_definition":
            return td
    return None


def short(address: str) -> str:
    """Readable form for evidence text.

    module.table["workflows"].aws_dynamodb_table.this → table["workflows"]
    module.compute.aws_instance.this["us-east-1a"]    → compute.aws_instance["us-east-1a"]
    aws_sfn_state_machine.workflow                     → aws_sfn_state_machine.workflow
    """
    ref = split_address(address)
    segments = _segments(ref.address)
    modules = [segments[i + 1] for i in range(0, len(segments) - 2, 2) if segments[i] == "module"]
    rtype, rname = segments[-2], segments[-1]
    if modules and rname.split("[", 1)[0] in ("this", "main"):
        suffix = rname[rname.index("["):] if "[" in rname else ""
        return modules[-1] + (f".{rtype}{suffix}" if suffix else "")
    return f"{rtype}.{rname}"


def _segments(address: str) -> list[str]:
    out, buf, depth = [], [], 0
    for ch in address:
        depth += (ch == "[") - (ch == "]")
        if ch == "." and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out
