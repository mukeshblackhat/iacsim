"""Inference rule: env_var                                                  [M1 ✅]

A compute node whose configuration *names* another resource almost certainly
talks to it:

    Lambda          environment.variables            e.g. TABLE_NAME = aws_dynamodb_table.x.name
    EC2             user_data / templatefile() vars  e.g. db_host = module.database.address
    ECS             the service's task definition → container_definitions[].environment / secrets
    Cloud Run       template.containers[].env[] (v2), template.spec.containers[].env[] (v1),
                    template.template.containers[].env[] (a v2 job); template.volumes[].cloud_sql_instance
    Cloud Functions environment_variables (v1), service_config.environment_variables (v2)
    GCE             metadata_startup_script / metadata.startup-script; a managed instance
                    group reads them from its instance template

Edge kind by target: datastore → READ, queue → PUBLISH, orchestrator/compute →
INVOKE. Medium confidence — the code knows the name, we assume it uses it.

The rule is provider-neutral (G14): it walks placeholders and looks nodes up by
address. It couples to a cloud only through `CONFIG_ATTRS`, keyed by resource
type prefix, and through the two container walkers (`_container_env` for ECS,
`_gcp_container_env` for Cloud Run) that flatten a container spec to {NAME: value}.
"""

from __future__ import annotations

import json
from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import (
    DEFAULT_KIND_FOR_TARGET,
    Confidence,
    Edge,
    InfraGraph,
    NodeKind,
    RawResource,
    RawResources,
)
from iacsim.core.refs import references_in
from iacsim.graph.inference._common import (
    block,
    blocks,
    instance_template_of,
    raw_by_address,
    short,
    task_definition_of,
)

KIND_FOR_TARGET = DEFAULT_KIND_FOR_TARGET      # one table, shared with synthetic hops in the traversal

# Scalar / map attributes read straight off the resource, per provider (by type prefix).
CONFIG_ATTRS: dict[str, tuple[str, ...]] = {
    "aws_": ("environment", "user_data", "user_data_base64", "container_definitions"),
    "google_": ("environment_variables", "metadata_startup_script", "metadata"),
}


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
            for attr, value in _sources(raws, r):
                for var_name, ref in _named_references(value):
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


def _config_attrs(rtype: str) -> tuple[str, ...]:
    return next((attrs for prefix, attrs in CONFIG_ATTRS.items() if rtype.startswith(prefix)), ())


def _sources(raws: dict[str, RawResource], r: RawResource) -> list[tuple[str, Any]]:
    """(where, value) pairs to scan for a compute resource: its own config
    attributes, then whatever glue resource carries its container / VM spec."""
    sources: list[tuple[str, Any]] = [(attr, r.attrs.get(attr)) for attr in _config_attrs(r.type)]
    if (td := task_definition_of(raws, r)) is not None:
        sources.append((f"{short(td.address)} container_definitions",
                        _container_env(td.attrs.get("container_definitions"))))
    if r.type.startswith("google_"):
        sources.extend(_gcp_sources(raws, r))
    return sources


def _gcp_sources(raws: dict[str, RawResource], r: RawResource) -> list[tuple[str, Any]]:
    sources: list[tuple[str, Any]] = []
    if "template" in r.attrs:                                            # Cloud Run service / job / worker pool
        sources.append(("template.containers env", _gcp_container_env(r.attrs["template"])))
        sources.append(("template.volumes cloud_sql_instance",
                        [block(v.get("cloud_sql_instance")).get("instances") for v in _volumes(r.attrs["template"])]))
    if "service_config" in r.attrs:                                      # Cloud Functions v2
        service = block(r.attrs["service_config"])
        sources.append(("service_config.environment_variables", service.get("environment_variables")))
        sources.append(("service_config.secret_environment_variables", service.get("secret_environment_variables")))
    if (template := instance_template_of(raws, r)) is not None:          # a MIG's VMs
        for attr in ("metadata_startup_script", "metadata"):
            sources.append((f"{short(template.address)} {attr}", template.attrs.get(attr)))
    return sources


def _container_env(defs: Any) -> dict[str, Any]:
    """ECS container_definitions (structured via jsonencode, or a JSON string)
    → {ENV_NAME: value} over every container's `environment` and `secrets`."""
    if isinstance(defs, str):
        try:
            defs = json.loads(defs)
        except ValueError:
            return {}
    out: dict[str, Any] = {}
    for container in defs if isinstance(defs, list) else []:
        if not isinstance(container, dict):
            continue
        for entry in list(container.get("environment") or []) + list(container.get("secrets") or []):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                out[entry["name"]] = entry.get("value", entry.get("valueFrom"))
    return out


def _gcp_container_env(template: Any) -> dict[str, Any]:
    """A Cloud Run template (v2 `template.containers[]`, v1 `template.spec.containers[]`,
    job `template.template.containers[]`) → {ENV_NAME: value} over every
    container's `env { name value }` blocks, wherever `containers` sits."""
    out: dict[str, Any] = {}
    for container in _find_blocks(template, "containers"):
        for entry in blocks(container.get("env")):
            if isinstance(entry.get("name"), str):
                out[entry["name"]] = entry.get("value", entry.get("value_source"))
    return out


def _volumes(template: Any) -> list[dict[str, Any]]:
    holders = [block(template), block(block(template).get("template"))]       # a job nests template.template
    return [v for holder in holders for v in blocks(holder.get("volumes"))]


def _find_blocks(value: Any, key: str) -> list[dict[str, Any]]:
    """Every block stored under `key` anywhere inside a nested attribute value."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key:
                found.extend(blocks(v))
            else:
                found.extend(_find_blocks(v, key))
    elif isinstance(value, list):
        for v in value:
            found.extend(_find_blocks(v, key))
    return found


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
