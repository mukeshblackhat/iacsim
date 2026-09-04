"""scenarios.yaml → Scenario objects.

    checkout:
      description: "..."
      entry: aws_api_gateway_rest_api.main
      steps:
        - aws_lambda_function.create_order
        - parallel:
            - aws_dynamodb_table.orders
            - aws_sqs_queue.notifications
        - fanout: { node: aws_lambda_function.render, count: 20 }
        - aws_lambda_function.confirm

Every node mentioned must exist in the graph; otherwise a clear error naming
the scenario, the step and the closest matching node id.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import yaml

from iacsim.core.interfaces import SCENARIO_SOURCES, ScenarioSource
from iacsim.core.models import InfraGraph, Scenario, Step


class UnknownNodeInScenario(ValueError):
    pass


@SCENARIO_SOURCES.register("yaml_file")
class YamlScenarioSource(ScenarioSource):
    filename = "scenarios.yaml"

    def load(self, graph: InfraGraph, root: Path) -> list[Scenario]:
        path = root / self.filename
        if not path.is_file():
            return []
        doc = yaml.safe_load(path.read_text()) or {}
        return [self._scenario(name, body, graph) for name, body in doc.items()]

    def _scenario(self, name: str, body: dict[str, Any], graph: InfraGraph) -> Scenario:
        self._check(body["entry"], graph, name)
        steps = [self._step(s, graph, name) for s in body.get("steps", [])]
        return Scenario(name=name, entry=body["entry"], steps=steps,
                        description=body.get("description"), source="declared")

    def _step(self, item: Any, graph: InfraGraph, scenario: str) -> Step:
        if isinstance(item, str):
            self._check(item, graph, scenario)
            return Step(node=item)
        if "parallel" in item:
            branches = [[self._step(x, graph, scenario)] if not isinstance(x, list)
                        else [self._step(y, graph, scenario) for y in x]
                        for x in item["parallel"]]
            return Step(parallel=branches)
        if "fanout" in item:
            self._check(item["fanout"]["node"], graph, scenario)
            return Step(fanout=(item["fanout"]["node"], int(item["fanout"]["count"])))
        raise ValueError(f"scenario '{scenario}': unrecognised step {item!r}")

    @staticmethod
    def _check(node_id: str, graph: InfraGraph, scenario: str) -> None:
        if node_id in graph.nodes:
            return
        hint = difflib.get_close_matches(node_id, graph.nodes, n=1)
        suffix = f" — did you mean '{hint[0]}'?" if hint else ""
        raise UnknownNodeInScenario(f"scenario '{scenario}' references unknown node '{node_id}'{suffix}")
