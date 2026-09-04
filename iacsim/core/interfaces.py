"""Abstract base classes — one per extension point — and their registries.

To add an implementation: subclass the ABC, decorate with the matching
registry's `.register("name")`, and reference "name" in iacsim.yaml.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from iacsim.core.models import (
    DiffReport,
    Edge,
    Findings,
    InfraGraph,
    Profile,
    RawResources,
    Result,
    Scenario,
)
from iacsim.core.registry import Registry


class Parser(ABC):
    """IaC files on disk → RawResources. One per input format."""

    @classmethod
    @abstractmethod
    def detect(cls, path: Path) -> bool:
        """Return True if this parser recognises the files at `path`."""

    @abstractmethod
    def parse(self, path: Path) -> RawResources: ...


class Normaliser(ABC):
    """Provider-specific RawResources → provider-neutral nodes. One per cloud."""

    @abstractmethod
    def normalise(self, raw: RawResources) -> InfraGraph: ...


class InferenceRule(ABC):
    """Looks at the graph (and raw resources) and proposes edges with evidence."""

    @abstractmethod
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]: ...


class ScenarioSource(ABC):
    """Produces request paths — from a YAML file, or inferred from entry points."""

    @abstractmethod
    def load(self, graph: InfraGraph, root: Path) -> list[Scenario]: ...


class ProfileSource(ABC):
    """Produces one layer of latency numbers to merge into the Profile."""

    @abstractmethod
    def load(self, spec: str) -> dict[str, Any]: ...


class CostRule(ABC):
    """Adds one component of an edge's latency (distance, processing, cold start...).
    Returns partial breakdown {name: ms}; the latency model sums them."""

    @abstractmethod
    def cost(self, edge: Edge, graph: InfraGraph, profile: Profile) -> dict[str, float]: ...


class Walker(ABC):
    """Runs one scenario across a costed graph."""

    @abstractmethod
    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result: ...


class Analyzer(ABC):
    """Turns a Result into ranked findings."""

    @abstractmethod
    def analyse(self, result: Result, graph: InfraGraph) -> list: ...


class Reporter(ABC):
    """Renders Findings (or a diff) to a string / file."""

    @abstractmethod
    def render(self, findings: list[Findings], graph: InfraGraph) -> str: ...

    def render_diff(self, diff: DiffReport, before: InfraGraph, after: InfraGraph) -> str:
        """Optional: render an `iacsim diff`. Built-ins implement it; a
        third-party reporter that doesn't is simply not usable for diffs."""
        raise NotImplementedError(f"{type(self).__name__} does not render diffs")


class MetricSource(ABC):
    """Real measurements for calibration (M7). `fake` is used in tests."""

    @abstractmethod
    def lambda_durations(self, function_name: str, window: str) -> dict[str, float]: ...

    @abstractmethod
    def table_latency(self, table_name: str, window: str) -> dict[str, float]: ...


# ------------------------------------------------------------------ registries

PARSERS: Registry[Parser] = Registry("parser")
NORMALISERS: Registry[Normaliser] = Registry("normaliser")
INFERENCE_RULES: Registry[InferenceRule] = Registry("inference rule")
SCENARIO_SOURCES: Registry[ScenarioSource] = Registry("scenario source")
PROFILE_SOURCES: Registry[ProfileSource] = Registry("profile source")
COST_RULES: Registry[CostRule] = Registry("cost rule")
WALKERS: Registry[Walker] = Registry("walker")
ANALYZERS: Registry[Analyzer] = Registry("analyzer")
REPORTERS: Registry[Reporter] = Registry("reporter")
METRIC_SOURCES: Registry[MetricSource] = Registry("metric source")
