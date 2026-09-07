"""Abstract base classes — one per extension point — and their registries.

To add an implementation: subclass the ABC, decorate with the matching
registry's `.register("name")`, and reference "name" in iacsim.yaml.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from iacsim.core.models import (
    Edge,
    Findings,
    InfraGraph,
    Profile,
    RawResources,
    Result,
    Scenario,
)
from iacsim.core.registry import Registry
from iacsim.diff.models import DiffReport


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
    """Provider-specific RawResources → provider-neutral nodes. One per cloud.

    A normaliser also declares how its own subtypes *behave*, in the two class
    tables below. The cost rules read the tables (through `behaviour_tables()`)
    instead of naming clouds, so adding a provider never edits a rule. Both
    default to empty: a normaliser that declares nothing keeps working, its
    nodes simply price as they did before the tables existed.

    `INVOKE_KEYS` maps a subtype to the key inside its `processing` block that a
    call *into* such a node is charged — `lambda` → `warm`, `s3` → `read`. It is
    used for INVOKE hops and as the fallback for any edge kind the node's block
    does not price. **A subtype missing from it makes every hop into that node
    cost nothing, silently** (`latency/rules/processing.py`), so every subtype
    the type map can produce belongs here, and the key must exist in the
    profile block — `tests/test_latency_rules.py` asserts both.

    `COLD_START` lists the subtypes that pay a cold start (`latency/rules/
    cold_start.py`); their profile blocks need `cold` and `cold_prob`.

    `CHAIN` lists subtypes that are one physical device drawn as several nodes
    (GCP's forwarding rule → proxy → URL map → backend service). A hop whose
    *both* ends are chain subtypes costs no distance (`latency/rules/
    distance.py`); the hop into the chain and the hop out of it are priced
    normally.
    """

    INVOKE_KEYS: ClassVar[dict[str, str]] = {}          # subtype → profile key charged on a call in
    COLD_START: ClassVar[frozenset[str]] = frozenset()  # subtypes that cold-start
    CHAIN: ClassVar[frozenset[str]] = frozenset()       # subtypes with no network between them

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


@dataclass
class ReporterOptions:
    """What the CLI lets a user change about rendering; every reporter gets the
    same object and reads what it needs."""
    top_n: int = 10          # hops / bottlenecks shown per section
    all_hops: bool = False   # show every hop in path order
    color: bool = False      # ANSI colour (text reporter, TTY only)


class Reporter(ABC):
    """Renders Findings (or a diff) to a string / file."""

    def __init__(self, options: ReporterOptions | None = None) -> None:
        self.options = options or ReporterOptions()

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

    def prepare(self, root: Path) -> None:  # noqa: B027 — optional hook, not every source needs it
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


# ------------------------------------------------- provider-owned behaviour tables

@dataclass(frozen=True)
class BehaviourTables:
    """Every registered Normaliser's subtype tables, merged into one lookup.

    The seam exists because `CostRule.cost(edge, graph, profile)` receives no
    config and `Node` carries no provider field, so a rule cannot ask *which*
    cloud a node came from. It does not need to: subtype names are unique across
    providers by contract (CONTRIBUTING.md, "A cloud provider"), so the union of
    every normaliser's tables answers the question unambiguously.
    """

    invoke_keys: dict[str, str]
    cold_start: frozenset[str]
    chain: frozenset[str]


_TABLES: tuple[tuple[str, ...], BehaviourTables] | None = None


def behaviour_tables() -> BehaviourTables:
    """The merged tables, built once and rebuilt only if a plugin registers a
    normaliser later in the process (the registry's name list is the cache key)."""
    global _TABLES
    names = tuple(NORMALISERS.names())
    if _TABLES is None or _TABLES[0] != names:
        invoke_keys: dict[str, str] = {}
        cold_start: set[str] = set()
        chain: set[str] = set()
        for name in names:                      # sorted, so a merge is deterministic
            normaliser = NORMALISERS.get(name)
            invoke_keys.update(normaliser.INVOKE_KEYS)
            cold_start.update(normaliser.COLD_START)
            chain.update(normaliser.CHAIN)
        _TABLES = (names, BehaviourTables(invoke_keys, frozenset(cold_start), frozenset(chain)))
    return _TABLES[1]
