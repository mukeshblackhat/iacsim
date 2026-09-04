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
    """IaC files on disk → RawResources. One per input format.

    `options` come from `parsers.<name>` in iacsim.yaml (e.g. the region a
    CloudFormation template deploys to); parsers that need none ignore them.
    """

    def __init__(self, **options: Any) -> None:
        self.options = options

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
    """Real measurements for `iacsim calibrate`.

    Company-specific by design: which account, region, credentials — or which
    monitoring system at all — is chosen in iacsim.yaml (`calibrate.source` +
    `calibrate.sources.<name>` options) and never in the engine. `cloudwatch`
    is one implementation; a Datadog or X-Ray source is a plugin implementing
    these two methods. `fake` is used in tests and dry runs.

    `kind` is the node subtype (lambda, dynamodb, rds, alb, api_gateway,
    step_functions); `name` is the node's physical name (`Node.label`).
    `measure` returns the profile keys for that kind (see calibrator.py) or
    None when the window holds no data — the calibrator then keeps defaults.
    """

    KINDS = ("lambda", "dynamodb", "rds", "alb", "api_gateway", "step_functions")

    def __init__(self, **options: Any) -> None:
        self.options = options

    def prepare(self, root: Path) -> None:
        """Called once before measuring, with the directory that holds iacsim.yaml —
        resolve relative paths here (mirrors ScenarioSource.load(graph, root))."""

    def describe(self) -> dict[str, Any]:
        """Provenance for the written profile's meta (region, account, fixture path…).
        Never secrets."""
        return {}

    def supports(self, kind: str) -> bool:
        return True

    @abstractmethod
    def measure(self, kind: str, name: str, window: str,
                region: str | None = None) -> dict[str, float] | None: ...


class MetricSourceError(RuntimeError):
    """A metric source cannot be used as configured: missing SDK, missing
    credentials, bad region. `iacsim calibrate` prints the message and exits 3."""


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
