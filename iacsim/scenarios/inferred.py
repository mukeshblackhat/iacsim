"""Auto-generate scenarios from entry points when no scenarios.yaml covers them.  [M2]

Entry points: GATEWAY, LB and CDN nodes (and any node with no incoming edges
that is COMPUTE). From each, follow inferred edges depth-first; one scenario
per simple path, named "<entry-label>/<leaf-label>". Declared scenarios take
precedence in the pipeline, so this only fills gaps.
"""

from __future__ import annotations

from pathlib import Path

from iacsim.core.interfaces import SCENARIO_SOURCES, ScenarioSource
from iacsim.core.models import InfraGraph, Scenario


@SCENARIO_SOURCES.register("inferred_from_entrypoints")
class InferredScenarioSource(ScenarioSource):
    def load(self, graph: InfraGraph, root: Path) -> list[Scenario]:
        raise NotImplementedError("M2: infer scenarios from entry points")
