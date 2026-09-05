"""Inference rule: env_var                                                  [M1 ✅]

A compute node whose configuration *names* another resource almost certainly
talks to it:

    Lambda   environment.variables            e.g. TABLE_NAME = aws_dynamodb_table.x.name
    EC2      user_data / templatefile() vars  e.g. db_host = module.database.address
    ECS      container_definitions environment

Edge kind by target: datastore → READ, queue → PUBLISH, orchestrator/compute →
INVOKE. Medium confidence — the code knows the name, we assume it uses it.
"""

from __future__ import annotations

from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import (
    DEFAULT_KIND_FOR_TARGET,
    Confidence,
    Edge,
    InfraGraph,
    NodeKind,
    RawResources,
)
from iacsim.core.refs import references_in
from iacsim.graph.inference._common import raw_by_address, short

KIND_FOR_TARGET = DEFAULT_KIND_FOR_TARGET      # one table, shared with synthetic hops in the traversal

CONFIG_ATTRS = ("environment", "user_data", "user_data_base64", "container_definitions")


@INFERENCE_RULES.register("env_var")
class EnvVarRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)
        edges: list[Edge] = []
        for node in graph.nodes_of_kind(NodeKind.COMPUTE):
            r = raws.get(node.id)
            if r is None:
                continue
            seen: set[str] = set()
            for attr in CONFIG_ATTRS:
                for var_name, ref in _named_references(r.attrs.get(attr)):
                    target = graph.nodes.get(ref.address)
                    if target is None or target.kind not in KIND_FOR_TARGET or ref.address in seen:
                        continue
                    if ref.address == node.id:
                        continue
                    seen.add(ref.address)
                    where = f"{attr} {var_name}" if var_name else attr
                    edges.append(Edge(
                        node.id, ref.address, KIND_FOR_TARGET[target.kind], Confidence.MEDIUM,
                        f"{short(node.id)} {where} references {short(ref.address)}.{ref.attr or 'id'}",
                    ))
        return edges


def _named_references(value: Any, name: str | None = None):
    """Yield (variable name, ResourceRef) for every placeholder, remembering the nearest key."""
    if isinstance(value, dict):
        if "__templatefile__" in value:
            yield from _named_references(value["__templatefile__"].get("vars"), name)
            return
        for k, v in value.items():
            yield from _named_references(v, k if isinstance(k, str) else name)
    elif isinstance(value, list):
        for v in value:
            yield from _named_references(v, name)
    elif isinstance(value, str):
        for ref in references_in(value):
            yield name, ref
