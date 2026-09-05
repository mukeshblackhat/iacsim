"""Inference rule: step_functions                                           [M1 ✅]

Reads each state machine's Amazon States Language definition and emits
orchestrator → target edges for every Task state, in the order the workflow
runs them. This is the strongest signal we have: for serverless stacks the
definition *is* the call graph.

Definition sources (RawResource.attrs["definition"]):
  - {"__templatefile__": {path, vars}} — read the file, substitute ${var}
    placeholders with the parser's "${address.attr}" strings, json.loads
  - a JSON string
  - an already-structured dict (jsonencode)

Task targets:
  arn:aws:states:::lambda:invoke[.waitForTaskToken]  Parameters.FunctionName → INVOKE
  a bare Lambda ARN reference as Resource                                   → INVOKE
  arn:aws:states:::dynamodb:{get,put,update,delete}Item Parameters.TableName → READ/WRITE
  arn:aws:states:::sqs:sendMessage / sns:publish                            → PUBLISH
  arn:aws:states:::states:startExecution Parameters.StateMachineArn         → INVOKE

The walked structure is stored on the orchestrator node as attrs["workflow"]:
    [{"state": ..., "type": "Task", "target": address, "kind": "invoke"},
     {"state": ..., "type": "Map", "concurrency": 5, "body": [...]},
     {"state": ..., "type": "Parallel", "branches": [[...], [...]]},
     {"state": ..., "type": "Choice", "branches": {"NextState": [...], ...}},
     {"state": ..., "type": "Wait", "seconds": 20}]
so the simulator can replay ordering, parallelism and fan-out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.graph.inference._common import first_node, raw_by_address, short

_TEMPLATE_VAR = re.compile(r"\$\{(\w+)\}")

SERVICE_TASKS = {
    "dynamodb:getitem": (EdgeKind.READ, "TableName", (NodeKind.DATASTORE,)),
    "dynamodb:putitem": (EdgeKind.WRITE, "TableName", (NodeKind.DATASTORE,)),
    "dynamodb:updateitem": (EdgeKind.WRITE, "TableName", (NodeKind.DATASTORE,)),
    "dynamodb:deleteitem": (EdgeKind.WRITE, "TableName", (NodeKind.DATASTORE,)),
    "sqs:sendmessage": (EdgeKind.PUBLISH, "QueueUrl", (NodeKind.QUEUE,)),
    "sns:publish": (EdgeKind.PUBLISH, "TopicArn", (NodeKind.QUEUE,)),
    "states:startexecution": (EdgeKind.INVOKE, "StateMachineArn", (NodeKind.ORCHESTRATOR,)),
    "lambda:invoke": (EdgeKind.INVOKE, "FunctionName", (NodeKind.COMPUTE,)),
}


@INFERENCE_RULES.register("step_functions")
class StepFunctionsRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)
        edges: list[Edge] = []
        for sm in graph.nodes_of_kind(NodeKind.ORCHESTRATOR):
            r = raws.get(sm.id)
            if r is None:
                continue
            definition, source = _load_definition(r.attrs.get("definition"), graph)
            if definition is None:
                graph.warnings.append(f"{sm.id}: state machine definition could not be read ({source})")
                continue
            walker = _Walker(graph, sm.id, source)
            sm.attrs["workflow"] = walker.walk(definition)
            edges.extend(walker.edges)
        return edges


# ------------------------------------------------------------------ definition loading

def _load_definition(value: Any, graph: InfraGraph) -> tuple[dict | None, str]:
    if isinstance(value, dict) and "__templatefile__" in value:
        tf = value["__templatefile__"]
        path = Path(tf["path"])
        try:
            text = path.read_text()
        except OSError as e:
            return None, f"{path}: {e}"
        # Placeholders contain quotes (module.x["k"]...), so escape them for the JSON they land in.
        text = _TEMPLATE_VAR.sub(lambda m: _json_escaped(tf["vars"].get(m.group(1), m.group(0))), text)
        return _parse_json(text, path.name)
    if isinstance(value, dict):
        return value, "inline definition"
    if isinstance(value, str):
        return _parse_json(value, "definition string")
    return None, "no definition attribute"


def _parse_json(text: str, source: str) -> tuple[dict | None, str]:
    try:
        return json.loads(text), source
    except ValueError as e:
        return None, f"{source}: invalid JSON ({e})"


def _json_escaped(value: Any) -> str:
    """A substituted value as it must appear inside JSON source: strings are
    escaped without their surrounding quotes, everything else is dumped as-is."""
    return json.dumps(value)[1:-1] if isinstance(value, str) else json.dumps(value)


# ------------------------------------------------------------------ walking

class _Walker:
    def __init__(self, graph: InfraGraph, machine: str, source: str) -> None:
        self.graph = graph
        self.machine = machine
        self.source = source
        self.edges: list[Edge] = []
        self._seen_targets: set[tuple[str, str]] = set()

    def walk(self, container: dict) -> list[dict]:
        states = container.get("States", {})
        return self._flow(states, container.get("StartAt"), set())

    def _flow(self, states: dict, start: str | None, on_path: set[str]) -> list[dict]:
        out: list[dict] = []
        name = start
        while name and name in states and name not in on_path:
            state = states[name]
            on_path = on_path | {name}
            item = self._visit(name, state, states, on_path)
            if item:
                out.append(item)
            if state.get("Type") == "Choice":
                break                                      # branches handled inside _visit
            name = state.get("Next")
        return out

    def _visit(self, name: str, state: dict, states: dict, on_path: set[str]) -> dict | None:
        kind = state.get("Type")
        if kind == "Task":
            target, edge_kind = self._task_target(name, state)
            return {"state": name, "type": "Task", "target": target, "kind": edge_kind.value if edge_kind else None,
                    "resource": state.get("Resource")}
        if kind == "Map":
            body = state.get("ItemProcessor") or state.get("Iterator") or {}
            return {"state": name, "type": "Map", "concurrency": state.get("MaxConcurrency"),
                    "body": self._flow(body.get("States", {}), body.get("StartAt"), set())}
        if kind == "Parallel":
            return {"state": name, "type": "Parallel",
                    "branches": [self._flow(b.get("States", {}), b.get("StartAt"), set())
                                 for b in state.get("Branches", [])]}
        if kind == "Choice":
            nexts = [c.get("Next") for c in state.get("Choices", []) if c.get("Next")]
            if state.get("Default"):
                nexts.append(state["Default"])
            return {"state": name, "type": "Choice",
                    "branches": {n: self._flow(states, n, on_path) for n in dict.fromkeys(nexts)}}
        if kind == "Wait":
            return {"state": name, "type": "Wait", "seconds": state.get("Seconds")}
        return None                                      # Pass / Succeed / Fail add nothing

    def _task_target(self, name: str, state: dict) -> tuple[str | None, EdgeKind | None]:
        resource = state.get("Resource", "")
        params = state.get("Parameters", {}) if isinstance(state.get("Parameters"), dict) else {}
        edge_kind, param, kinds = None, None, (NodeKind.COMPUTE,)
        if isinstance(resource, str) and resource.startswith("arn:aws:states:::"):
            service = resource.removeprefix("arn:aws:states:::").split(".", 1)[0].lower()
            if service in SERVICE_TASKS:
                edge_kind, param, kinds = SERVICE_TASKS[service]
            candidate = params.get(param) if param else None
            target = first_node(self.graph, candidate, kinds)
        else:
            edge_kind = EdgeKind.INVOKE
            target = first_node(self.graph, resource, kinds)
        if target and edge_kind:
            self._emit(name, state, target, edge_kind)
        return target, edge_kind

    def _emit(self, name: str, state: dict, target: str, kind: EdgeKind) -> None:
        if (self.machine, target) in self._seen_targets:
            return
        self._seen_targets.add((self.machine, target))
        self.edges.append(Edge(
            self.machine, target, kind, Confidence.HIGH,
            f"state '{name}' ({state.get('Type')}, "
            f"{state.get('Resource', '').removeprefix('arn:aws:states:::') or 'direct'}) "
            f"in {self.source} calls {short(target)}",
        ))
