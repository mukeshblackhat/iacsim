"""The intermediate representation (IR) — the fixed contract between stages.

Everything a parser produces, a latency model annotates, a walker consumes and a
reporter prints goes through these dataclasses. They are deliberately plain so
they dump to JSON with no surprises. `SCHEMA_VERSION` is written into every
dump so old files stay readable after upgrades.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "2"


# ------------------------------------------------------------------ parser output

@dataclass
class RawResource:
    """One resource exactly as the IaC file declared it, before normalisation.

    `type` is provider-specific ("aws_lambda_function", "AWS::Lambda::Function").
    `references` are other resource addresses this one mentions anywhere in its
    body — the raw material for edge inference. Inside `attrs`, mentions of
    other resources are placeholder strings "${address.attr}" (see core/refs.py);
    `jsonencode(...)` values are kept structured; `templatefile(...)` becomes
    {"__templatefile__": {"path": ..., "vars": {...}}}.
    """
    address: str                        # "module.compute.aws_instance.this[\"us-east-1a\"]"
    type: str
    attrs: dict[str, Any]
    references: list[str] = field(default_factory=list)
    region: str | None = None           # from the provider (or alias) this resource uses
    source_file: str | None = None
    source_line: int | None = None


@dataclass
class RawResources:
    resources: list[RawResource]
    format: str                         # "terraform" | "cloudformation"
    root_path: str
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ graph

class NodeKind(StrEnum):
    """Provider-neutral role of a node. Latency rules key off this + subtype."""
    COMPUTE = "compute"        # lambda, ec2, fargate
    DATASTORE = "datastore"    # dynamodb, rds, elasticache, s3
    LB = "lb"                  # alb, nlb
    GATEWAY = "gateway"        # api_gateway
    QUEUE = "queue"            # sqs, sns, kinesis
    ORCHESTRATOR = "orchestrator"  # step_functions
    CDN = "cdn"                # cloudfront
    NETWORK = "network"        # vpc, subnet, peering, nat  — placement, not hops
    EXTERNAL = "external"      # the internet / a user


class EdgeKind(StrEnum):
    INVOKE = "invoke"      # sync call: gateway → lambda, lambda → lambda
    READ = "read"          # compute → datastore
    WRITE = "write"
    ROUTE = "route"        # lb → target
    PUBLISH = "publish"    # compute → queue
    CONSUME = "consume"    # queue → compute
    PEER = "peer"          # vpc ↔ vpc


# What kind of call a hop into a node most likely is when nothing in the IaC says
# otherwise — used by the env_var rule and for synthetic (estimated) hops.
DEFAULT_KIND_FOR_TARGET: dict[NodeKind, EdgeKind] = {
    NodeKind.DATASTORE: EdgeKind.READ,
    NodeKind.QUEUE: EdgeKind.PUBLISH,
    NodeKind.ORCHESTRATOR: EdgeKind.INVOKE,
    NodeKind.COMPUTE: EdgeKind.INVOKE,
}


class Confidence(StrEnum):
    DECLARED = "declared"  # from scenarios.yaml — always trusted
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Most-trusted first, so `max(confidences, key=CONFIDENCE_ORDER.index)` reads naturally.
CONFIDENCE_ORDER = [Confidence.DECLARED, Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW]

# When two rules find evidence for the same (src, dst) pair with different
# operations, the edge is priced as the first of these that any rule found —
# a request path *reads* by default; a scenario step says `op: write` when it
# doesn't. Every operation found is kept in `Edge.ops`.
KIND_PRIORITY = [EdgeKind.INVOKE, EdgeKind.ROUTE, EdgeKind.CONSUME, EdgeKind.PUBLISH,
                 EdgeKind.READ, EdgeKind.WRITE, EdgeKind.PEER]


@dataclass
class Placement:
    region: str | None = None
    az: str | None = None
    vpc: str | None = None
    subnet: str | None = None


@dataclass
class Latency:
    """Cost of one hop, in milliseconds. `expected` feeds the deterministic walker;
    the rest feed Monte-Carlo. `breakdown` says where the number came from, e.g.
    {"distance": 65.0, "processing": 4.0, "cold_start": 20.0}."""
    expected: float
    p50: float | None = None
    p99: float | None = None
    distribution: str | None = None       # "lognormal", "bimodal", ...
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class Node:
    id: str                              # same as RawResource.address
    kind: NodeKind
    subtype: str                         # "lambda", "dynamodb", "alb", ...
    placement: Placement = field(default_factory=Placement)
    attrs: dict[str, Any] = field(default_factory=dict)   # only what latency rules need
    label: str | None = None


@dataclass
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    confidence: Confidence
    evidence: str                        # human sentence: why we believe this hop exists
    rule: str | None = None              # registry name(s) of the inference rule(s), "a+b" when merged
    latency: Latency | None = None       # filled by latency rules
    ops: list[EdgeKind] = field(default_factory=list)   # every operation a rule found evidence for
                                                        # (KIND_PRIORITY order); `kind` is the one priced
                                                        # unless the scenario step says `op:`


@dataclass
class InfraGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    source_format: str | None = None
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)

    def find_edge(self, src: str, dst: str) -> Edge | None:
        return next((e for e in self.edges if e.src == src and e.dst == dst), None)

    def nodes_of_kind(self, kind: NodeKind) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def display_name(self, node_id: str) -> str:
        """A node's label when no other node shares it, else a shortened id:
        `module.compute.aws_instance.this["a"]` → `compute.instance["a"]`."""
        node = self.nodes.get(node_id)
        if node and node.label and sum(1 for n in self.nodes.values() if n.label == node.label) == 1:
            return node.label
        short = node_id.replace("module.", "").replace(".aws_", ".")
        return short[:-5] if short.endswith(".this") else short.replace(".this[", "[")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ scenarios

@dataclass
class Step:
    """One step in a request path. Exactly one of `node`, `parallel`, `fanout`, `wait_ms` is set.

    node:     visit this node (sequential). `op` overrides the edge's operation
              ("read" | "write" | "publish" …) so a scenario can say "this step
              writes" on an edge inference tagged as a read.
    parallel: run each branch (a list of Steps) concurrently; cost = max(branches)
    fanout:   visit `node` `count` times concurrently (Step Functions Map, SQS batch);
              `(node, count)` or `(node, count, concurrency)` — costed once per wave
              of `concurrency` copies (the enclosing Map's MaxConcurrency when the
              third element is absent); the count is recorded in Result.shape
    wait_ms:  a deliberate pause (Step Functions Wait state); no hop, pure cost
    """
    node: str | None = None
    parallel: list[list[Step]] | None = None
    fanout: tuple[str, int] | tuple[str, int, int | None] | None = None
    wait_ms: float | None = None
    op: str | None = None
    note: str | None = None


@dataclass
class Scenario:
    name: str
    entry: str
    steps: list[Step]
    description: str | None = None
    source: str = "declared"             # "declared" | "inferred"


# ------------------------------------------------------------------ latency profile

@dataclass
class Profile:
    """Merged latency numbers. Shape mirrors latency/defaults.yaml exactly so a
    hand-written override and a CloudWatch-calibrated file are interchangeable."""
    meta: dict[str, Any]
    distance: dict[str, Any]
    processing: dict[str, Any]
    sources: list[str] = field(default_factory=list)   # profile files merged, in order
    # Spread around the expected values, used only by the Monte-Carlo walker:
    # {"distance_sigma": 0.2, "processing_sigma": 0.3} (σ of the lognormal, in log space).
    # A per-subtype `sigma` inside processing.<subtype>.defaults / per_resource wins.
    variance: dict[str, Any] = field(default_factory=dict)
    # Capacity limits per subtype (latency/defaults.yaml `capacity:`), read only by
    # the `load` walker: account concurrency, rps per instance class, max connections…
    capacity: dict[str, Any] = field(default_factory=dict)

    def processing_for(self, node: Node) -> dict[str, Any]:
        """defaults[subtype] ← by_label[node.label] ← per_resource[node.id]  (later wins per key).

        Merging (not replacing) means a calibrated or hand-written override can
        set just `warm` and still inherit `cold` / `cold_prob`. `by_label` is
        keyed by the physical AWS name, so one calibrated file serves both the
        Terraform and the CloudFormation graph of the same stack."""
        section = self.processing.get(node.subtype, {})
        block = dict(section.get("defaults", {}))
        if node.label:
            block.update(section.get("by_label", {}).get(node.label, {}))
        block.update(section.get("per_resource", {}).get(node.id, {}))
        return block


# ------------------------------------------------------------------ simulation output

@dataclass
class HopResult:
    src: str
    dst: str
    latency_ms: float
    breakdown: dict[str, float]
    evidence: str
    on_critical_path: bool = True
    group: str | None = None             # "parallel1/branch2" when inside a parallel group, else None
    percentiles: dict[str, float] = field(default_factory=dict)   # {"p50", "p99"} from monte_carlo; empty otherwise

    @property
    def label(self) -> str:
        return f"{self.src} → {self.dst}"


@dataclass
class Result:
    scenario: str
    total_ms: float
    hops: list[HopResult]
    walker: str
    percentiles: dict[str, float] = field(default_factory=dict)   # {"p50": .., "p99": ..} from monte_carlo
    samples: int | None = None
    # Layer A3 — the *shape* of the path, independent of any latency number:
    # {"hop_count", "sequential_hops", "parallel_groups", "parallel_savings_ms", "fanout_copies", "wait_ms"}
    shape: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Filled only by the `load` walker (M8): the users sweep for this scenario —
    # {"users": [...], "rps": {U: λ}, "latency": {U: {"expected_ms", "p99_ms", "saturated"}},
    #  "utilisation": {U: {resource: ρ}}, "resources": {resource: {...}}, "thresholds": {...}}
    load: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    """One line of the bottleneck report.

    `latency_ms` is the time attributed to `subject` — except for the
    `recommendations` analyzer, where it is the *estimated saving*.
    `refs` names the hops ("a → b") or nodes the number was derived from, so
    every claim in the report can be traced back to the hop table.
    `layer` is A1 (distance) / A2 (service) / A3 (shape) where it applies.
    `additive` is False for informational lines that must not be summed
    toward the total (parallel savings, hop counts).
    """
    analyzer: str
    subject: str                         # hop "a → b", node id, category name, or a recommendation title
    latency_ms: float
    share: float                         # 0..1 of scenario total (0 for non-additive lines)
    detail: str
    refs: list[str] = field(default_factory=list)
    layer: str | None = None
    additive: bool = True


@dataclass
class Findings:
    scenario: str
    total_ms: float
    findings: list[Finding]
    profile_sources: list[str]
    description: str | None = None
    source: str = "declared"             # "declared" | "inferred"
    hops: list[HopResult] = field(default_factory=list)   # in path order, for the hop table
    shape: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    percentiles: dict[str, float] = field(default_factory=dict)   # {"p50", "p90", "p95", "p99"} when sampled
    samples: int | None = None
    load: dict[str, Any] = field(default_factory=dict)   # the `load` walker's sweep (see Result.load)
    schema_version: str = SCHEMA_VERSION

    def by_analyzer(self) -> dict[str, list[Finding]]:
        """Findings grouped by analyzer name, in first-seen order."""
        grouped: dict[str, list[Finding]] = {}
        for f in self.findings:
            grouped.setdefault(f.analyzer, []).append(f)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        """The report.json shape for one scenario (see reporter/json_.py)."""
        return {
            "name": self.scenario,
            "description": self.description,
            "source": self.source,
            "total_ms": self.total_ms,
            "percentiles": self.percentiles,
            "samples": self.samples,
            "shape": self.shape,
            "profile": {"sources": self.profile_sources},
            "hops": [asdict(h) | {"label": h.label} for h in self.hops],
            "findings": {name: [asdict(f) for f in items] for name, items in self.by_analyzer().items()},
            "warnings": self.warnings,
            "load": self.load,
        }


# ------------------------------------------------------------------ diff (M4)

CHANGE_THRESHOLD_MS = 0.05               # below this a before/after pair counts as unchanged

@dataclass
class ValueDelta:
    """One aligned line — a category, a node, or a total — before vs after.
    `None` on a side means the subject only exists on the other side."""
    subject: str
    before_ms: float | None
    after_ms: float | None
    before_share: float | None = None
    after_share: float | None = None
    layer: str | None = None
    detail: str = ""                     # the after-side detail (or before's if gone)

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
class HopDelta:
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
        for s, sd in zip(self.scenarios, d["scenarios"]):
            sd["delta_ms"] = s.delta_ms
            sd["delta_pct"] = s.delta_pct
            for h, hd in zip(s.hops, sd["hops"]):
                hd["status"], hd["delta_ms"] = h.status, h.delta_ms
            for v, vd in zip([*s.categories, *s.nodes], [*sd["categories"], *sd["nodes"]]):
                vd["status"], vd["delta_ms"] = v.status, v.delta_ms
        return d

