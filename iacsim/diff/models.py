"""Dataclasses for `iacsim diff` (M4). JSON-dumpable; `DiffReport.to_dict()` is
the diff.json contract documented in reporter/json_.py."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from iacsim.core.models import SCHEMA_VERSION

CHANGE_THRESHOLD_MS = 0.05               # below this a before/after pair counts as unchanged


class _DeltaMixin:
    """Shared by every before/after pair: `before_ms` / `after_ms` may be None
    when the subject only exists on one side."""
    before_ms: float | None
    after_ms: float | None

    @property
    def delta_ms(self) -> float:
        return (self.after_ms or 0.0) - (self.before_ms or 0.0)

    @property
    def status(self) -> str:
        if self.before_ms is None:
            return "added"
        if self.after_ms is None:
            return "removed"
        return "changed" if abs(self.delta_ms) >= CHANGE_THRESHOLD_MS else "unchanged"


@dataclass
class ValueDelta(_DeltaMixin):
    """One aligned line — a category, a node, or a total — before vs after."""
    subject: str
    before_ms: float | None
    after_ms: float | None
    before_share: float | None = None
    after_share: float | None = None
    layer: str | None = None
    detail: str = ""                     # the after-side detail (or before's if gone)


@dataclass
class HopDelta(_DeltaMixin):
    """One aligned hop. Alignment key = hop label + occurrence index, so the
    second call to the same table lines up with the second call, not the first."""
    label: str
    occurrence: int
    index_before: int | None
    index_after: int | None
    before_ms: float | None
    after_ms: float | None
    breakdown_before: dict[str, float] = field(default_factory=dict)
    breakdown_after: dict[str, float] = field(default_factory=dict)

    def breakdown_deltas(self) -> dict[str, tuple[float, float]]:
        """{category: (before, after)} for every category present on either side."""
        keys = list(dict.fromkeys([*self.breakdown_before, *self.breakdown_after]))
        return {k: (self.breakdown_before.get(k, 0.0), self.breakdown_after.get(k, 0.0)) for k in keys}


@dataclass
class RecommendationDelta:
    subject: str
    status: str                          # "appeared" | "disappeared" | "unchanged"
    saving_ms: float
    detail: str


@dataclass
class ScenarioDiff:
    name: str
    status: str                          # "both" | "only_before" | "only_after"
    before_ms: float | None
    after_ms: float | None
    description: str | None = None
    categories: list[ValueDelta] = field(default_factory=list)
    hops: list[HopDelta] = field(default_factory=list)
    nodes: list[ValueDelta] = field(default_factory=list)
    recommendations: list[RecommendationDelta] = field(default_factory=list)
    shape_before: dict[str, float] = field(default_factory=dict)
    shape_after: dict[str, float] = field(default_factory=dict)

    @property
    def delta_ms(self) -> float:
        return (self.after_ms or 0.0) - (self.before_ms or 0.0)

    @property
    def delta_pct(self) -> float | None:
        if not self.before_ms:
            return None
        return self.delta_ms / self.before_ms

    def hops_with(self, status: str) -> list[HopDelta]:
        return [h for h in self.hops if h.status == status]


@dataclass
class NodeMove:
    node_id: str
    field: str                           # "region" | "az" | "vpc"
    before: str | None
    after: str | None


@dataclass
class GraphDiff:
    """Scenario-independent changes: what was added, removed, or moved."""
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    nodes_moved: list[NodeMove] = field(default_factory=list)
    edges_added: list[str] = field(default_factory=list)     # "src → dst (kind)"
    edges_removed: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.nodes_added or self.nodes_removed or self.nodes_moved
                    or self.edges_added or self.edges_removed)


@dataclass
class DiffReport:
    before: str                          # path or label of the before snapshot
    after: str
    profile_sources: list[str]
    graph: GraphDiff
    scenarios: list[ScenarioDiff]
    schema_version: str = SCHEMA_VERSION

    @property
    def is_empty(self) -> bool:
        return self.graph.is_empty and all(
            s.status == "both" and abs(s.delta_ms) < CHANGE_THRESHOLD_MS for s in self.scenarios
        )

    def regressions(self, threshold: tuple[str, float]) -> list[ScenarioDiff]:
        """Scenarios whose total grew by more than `threshold` = ("ms", 50) or ("percent", 10)."""
        unit, limit = threshold
        out = []
        for s in self.scenarios:
            if s.status != "both":
                continue
            grew = s.delta_ms if unit == "ms" else (s.delta_pct or 0.0) * 100
            if grew > limit:
                out.append(s)
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for s, sd in zip(self.scenarios, d["scenarios"], strict=True):
            sd["delta_ms"] = s.delta_ms
            sd["delta_pct"] = s.delta_pct
            for h, hd in zip(s.hops, sd["hops"], strict=True):
                hd["status"], hd["delta_ms"] = h.status, h.delta_ms
            for v, vd in zip([*s.categories, *s.nodes], [*sd["categories"], *sd["nodes"]], strict=True):
                vd["status"], vd["delta_ms"] = v.status, v.delta_ms
        return d
