"""Auto-generate scenarios from entry points.                                [M2 ✅]

Only fills gaps: the pipeline drops any inferred scenario whose name a
declared one already uses. Meant to give a useful first answer on a repo with
no `scenarios.yaml`, not to enumerate every path.

Entry points  — every node the `internet` node has an edge into (gateways,
                load balancers, CDNs).
Walk          — depth-first over inferred edges from the entry point.
                * DATASTORE / QUEUE nodes end a path (a request does not
                  continue *through* a table).
                * An ORCHESTRATOR node is not followed edge-by-edge; its
                  `attrs["workflow"]` (recorded by the step_functions rule) is
                  replayed instead: Task → step, Map → its body as one
                  parallel branch (all items identical, costed once), Parallel
                  → parallel, Choice → the branch with the most Task states
                  (the representative "does real work" path; a Choice whose
                  branches are only Waits or empty takes the empty one — the
                  common no-delay case), noted in the description, Wait →
                  wait_ms.
                * depth ≤ MAX_DEPTH.
Selection     — one path per (entry point, leaf subtype), so an API Lambda
                that can reach ten DynamoDB tables yields one table path, one
                S3 path and one Step Functions path — never all ten. Paths
                through an orchestrator rank first, then longer paths, capped
                at MAX_PER_ENTRY.
Names         — "<entry label>/<leaf label>", `source="inferred"`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iacsim.core.interfaces import SCENARIO_SOURCES, ScenarioSource
from iacsim.core.models import InfraGraph, NodeKind, Scenario, Step

MAX_DEPTH = 8
MAX_PER_ENTRY = 5
TERMINAL_KINDS = {NodeKind.DATASTORE, NodeKind.QUEUE}
NOT_A_HOP = {NodeKind.NETWORK, NodeKind.EXTERNAL}


@dataclass
class _Path:
    nodes: list[str]                       # visited node ids, entry excluded
    steps: list[Step]
    notes: list[str] = field(default_factory=list)
    via_orchestrator: bool = False

    @property
    def leaf(self) -> str:
        return self.nodes[-1] if self.nodes else ""


@SCENARIO_SOURCES.register("inferred_from_entrypoints")
class InferredScenarioSource(ScenarioSource):
    def load(self, graph: InfraGraph, root: Path) -> list[Scenario]:
        self.graph = graph
        scenarios: list[Scenario] = []
        for entry in self._entry_points():
            for path in self._select(self._paths_from(entry)):
                scenarios.append(self._scenario(entry, path))
        return scenarios

    # ------------------------------------------------------------------ discovery

    def _entry_points(self) -> list[str]:
        return [e.dst for e in self.graph.edges if e.src == "internet" and e.dst in self.graph.nodes]

    def _successors(self, node_id: str) -> list[str]:
        return [e.dst for e in self.graph.edges
                if e.src == node_id and e.dst in self.graph.nodes
                and self.graph.nodes[e.dst].kind not in NOT_A_HOP]

    def _paths_from(self, entry: str) -> list[_Path]:
        found: list[_Path] = []
        self._dfs(entry, _Path(nodes=[], steps=[]), {entry}, found)
        return found

    def _dfs(self, current: str, path: _Path, seen: set[str], found: list[_Path]) -> None:
        if len(path.nodes) >= MAX_DEPTH:
            found.append(path)
            return
        nexts = [n for n in self._successors(current) if n not in seen]
        if not nexts:
            if path.nodes:
                found.append(path)
            return
        for nxt in nexts:
            extended = self._extend(path, nxt)
            kind = self.graph.nodes[nxt].kind
            if kind in TERMINAL_KINDS or kind == NodeKind.ORCHESTRATOR:
                found.append(extended)          # tables end a path; orchestrators were replayed
            else:
                self._dfs(nxt, extended, seen | {nxt}, found)

    def _extend(self, path: _Path, node_id: str) -> _Path:
        node = self.graph.nodes[node_id]
        new = _Path(nodes=path.nodes + [node_id], steps=path.steps + [Step(node=node_id)],
                    notes=list(path.notes), via_orchestrator=path.via_orchestrator)
        if node.kind == NodeKind.ORCHESTRATOR and node.attrs.get("workflow"):
            new.steps += self._replay(node.attrs["workflow"], new.notes)
            new.via_orchestrator = True
        return new

    # ------------------------------------------------------------------ workflow replay

    def _replay(self, items: list[dict[str, Any]], notes: list[str]) -> list[Step]:
        steps: list[Step] = []
        for item in items:
            kind = item.get("type")
            if kind == "Task" and item.get("target") in self.graph.nodes:
                steps.append(Step(node=item["target"]))
            elif kind == "Map":
                body = self._replay(item.get("body", []), notes)
                if body:
                    n = item.get("concurrency") or 1
                    notes.append(f"Map {item['state']}: ×{n} concurrent items, one item costed")
                    steps.append(Step(parallel=[body], note=f"Map {item['state']} ×{n}"))
            elif kind == "Parallel":
                branches = [self._replay(b, notes) for b in item.get("branches", [])]
                branches = [b for b in branches if b]
                if branches:
                    steps.append(Step(parallel=branches))
            elif kind == "Choice":
                steps += self._choose_branch(item, notes)
            elif kind == "Wait" and item.get("seconds"):
                steps.append(Step(wait_ms=float(item["seconds"]) * 1000, note=f"Wait {item['state']}"))
        return steps

    def _choose_branch(self, choice: dict[str, Any], notes: list[str]) -> list[Step]:
        branches = choice.get("branches", {})
        if not branches:
            return []
        best = max(branches, key=lambda name: _task_count(branches[name]))
        if _task_count(branches[best]) == 0:
            return []                                   # only waits / nothing: take the no-delay path
        notes.append(f"Choice {choice['state']}: took branch {best} ({_task_count(branches[best])} tasks)")
        return self._replay(branches[best], notes)

    # ------------------------------------------------------------------ selection

    def _select(self, paths: list[_Path]) -> list[_Path]:
        ranked = sorted(paths, key=lambda p: (not p.via_orchestrator, -len(p.nodes)))
        chosen: list[_Path] = []
        seen_leaf_subtypes: set[str] = set()
        for path in ranked:
            subtype = self.graph.nodes[path.leaf].subtype if path.leaf in self.graph.nodes else ""
            if subtype in seen_leaf_subtypes:
                continue
            seen_leaf_subtypes.add(subtype)
            chosen.append(path)
            if len(chosen) >= MAX_PER_ENTRY:
                break
        return chosen

    def _scenario(self, entry: str, path: _Path) -> Scenario:
        name = f"{self._label(entry)}/{self._label(path.leaf)}"
        description = "inferred: " + " → ".join(self._label(n) for n in [entry] + path.nodes)
        if path.notes:
            description += " (" + "; ".join(dict.fromkeys(path.notes)) + ")"
        return Scenario(name=name, entry=entry, steps=path.steps, description=description, source="inferred")

    def _label(self, node_id: str) -> str:
        return self.graph.display_name(node_id)


def _task_count(items: list[dict[str, Any]]) -> int:
    """Number of Task states in a workflow fragment, recursing into Map / Parallel / Choice."""
    n = 0
    for item in items:
        kind = item.get("type")
        if kind == "Task":
            n += 1
        elif kind == "Map":
            n += _task_count(item.get("body", []))
        elif kind == "Parallel":
            n += sum(_task_count(b) for b in item.get("branches", []))
        elif kind == "Choice":
            n += max((_task_count(b) for b in item.get("branches", {}).values()), default=0)
    return n
