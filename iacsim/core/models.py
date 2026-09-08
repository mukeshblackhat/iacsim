"""The intermediate representation (IR) — the fixed contract between stages.

Everything a parser produces, a latency model annotates, a walker consumes and a
reporter prints goes through these dataclasses. They are deliberately plain so
they dump to JSON with no surprises. `SCHEMA_VERSION` is written into every
dump so old files stay readable after upgrades.
"""

from __future__ import annotations

import re
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


@dataclass
class RawResources:
    resources: list[RawResource]
    format: str                         # "terraform" | "cloudformation"
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

# Attributes that hold a resource's physical (AWS) name, in lookup order. The
# normaliser reads them for `Node.label`; the CloudFormation adapter uses the
# same list to turn literal names in env vars back into references.
PHYSICAL_NAME_ATTRS = ("function_name", "name", "identifier", "bucket", "rest_api_name")


@dataclass
class Placement:
    region: str | None = None
    az: str | None = None
    vpc: str | None = None
    subnet: str | None = None


@dataclass
class Latency:
    """Cost of one hop, in milliseconds. `expected` feeds the deterministic walker;
    the Monte-Carlo walker draws around it using the profile's variance. `breakdown`
    says where the number came from, e.g. {"distance": 65.0, "processing": 4.0}."""
    expected: float
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
class WorkflowStep:
    """One state of an orchestrator's workflow (Step Functions ASL today), as the
    step_functions rule records it on the orchestrator node — `attrs["workflow"]`
    holds `to_dict()` of each top-level step, so graph.json keeps its shape.

    type      Task | Map | Parallel | Choice | Wait
    Task      target (node id or None), kind (edge kind value), resource (raw ARN)
    Map       concurrency (MaxConcurrency or None), body (steps)
    Parallel  branches (list of step lists)
    Choice    choices (next-state name → step list)
    Wait      seconds
    """
    type: str
    state: str
    target: str | None = None
    kind: str | None = None
    resource: str | None = None
    concurrency: int | None = None
    seconds: float | None = None
    body: list[WorkflowStep] = field(default_factory=list)
    branches: list[list[WorkflowStep]] = field(default_factory=list)
    choices: dict[str, list[WorkflowStep]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"state": self.state, "type": self.type}
        if self.type == "Task":
            d.update(target=self.target, kind=self.kind, resource=self.resource)
        elif self.type == "Map":
            d.update(concurrency=self.concurrency, body=[s.to_dict() for s in self.body])
        elif self.type == "Parallel":
            d["branches"] = [[s.to_dict() for s in b] for b in self.branches]
        elif self.type == "Choice":
            d["branches"] = {name: [s.to_dict() for s in b] for name, b in self.choices.items()}
        elif self.type == "Wait":
            d["seconds"] = self.seconds
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowStep:
        kind = d.get("type", "")
        raw_branches = d.get("branches") or ([] if kind == "Parallel" else {})
        return cls(
            type=kind, state=d.get("state", ""), target=d.get("target"), kind=d.get("kind"),
            resource=d.get("resource"), concurrency=d.get("concurrency"), seconds=d.get("seconds"),
            body=cls.from_dicts(d.get("body") or []),
            branches=[cls.from_dicts(b) for b in raw_branches] if isinstance(raw_branches, list) else [],
            choices={n: cls.from_dicts(b) for n, b in raw_branches.items()} if isinstance(raw_branches, dict) else {},
        )

    @classmethod
    def from_dicts(cls, items: list[dict[str, Any]]) -> list[WorkflowStep]:
        return [cls.from_dict(d) for d in items]

    def sub_flows(self) -> list[list[WorkflowStep]]:
        """Every nested step list (Map body, Parallel branches, Choice branches)."""
        return [self.body, *self.branches, *self.choices.values()]


@dataclass
class InfraGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    source_format: str | None = None
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    # Lookup indexes, built lazily and kept out of the dataclass fields so they
    # never reach graph.json (`asdict`) or equality. `add_node` / `add_edge` are
    # the only mutation points and keep them in step.
    def __post_init__(self) -> None:
        self._edge_index: dict[tuple[str, str], Edge] | None = None
        self._label_counts: dict[str, int] | None = None

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node
        self._label_counts = None

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)
        if self._edge_index is not None:
            self._edge_index.setdefault((edge.src, edge.dst), edge)

    def find_edge(self, src: str, dst: str) -> Edge | None:
        """The first edge src → dst, O(1) after the first call."""
        if self._edge_index is None or len(self._edge_index) > len(self.edges):
            index: dict[tuple[str, str], Edge] = {}
            for e in self.edges:
                index.setdefault((e.src, e.dst), e)
            self._edge_index = index
        return self._edge_index.get((src, dst))

    def nodes_of_kind(self, kind: NodeKind) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def display_name(self, node_id: str) -> str:
        """A node's label when no other node shares it, else a shortened id:
        `module.compute.aws_instance.this["a"]` → `compute.instance["a"]`,
        `module.svc.google_cloud_run_v2_service.this` → `svc.cloud_run_v2_service`."""
        node = self.nodes.get(node_id)
        if self._label_counts is None or len(self._label_counts) == 0 and self.nodes:
            counts: dict[str, int] = {}
            for n in self.nodes.values():
                if n.label:
                    counts[n.label] = counts.get(n.label, 0) + 1
            self._label_counts = counts
        if node and node.label and self._label_counts.get(node.label) == 1:
            return node.label
        short = _TYPE_PREFIX.sub(".", node_id.replace("module.", ""))
        return short[:-5] if short.endswith(".this") else short.replace(".this[", "[")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# The provider prefix of a Terraform address's resource-type segment, whichever
# cloud it is: `.aws_db_instance.this` → `.db_instance.this`,
# `.google_cloud_run_v2_service.this` → `.cloud_run_v2_service.this`. Only the
# first `_`-delimited word goes, and only from the segment directly before the
# resource name — a module called `my_mod` or an index key like `["a_b"]` is
# never touched. It must follow a dot: a root-level `aws_x.this` keeps its
# prefix, exactly as before. A pattern rather than the normalisers' PREFIXES
# because models.py sits below the registry and cannot import it.
_TYPE_PREFIX = re.compile(r"\.[a-z][a-z0-9]*_(?=[a-z0-9_]+\.[^.\[]+(?:\[.*\])?$)")


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
class LoadSummary:
    """The `load` walker's users sweep for one scenario (M8). `to_dict()` is the
    report.json `load` block; `resources` / `utilisation` / `thresholds` /
    `assumptions` / `tail_factor` are the same on every scenario of a run."""
    users: list[int]
    traffic: bool = True                                   # does load.yaml send this scenario any traffic?
    rps: dict[int, float] = field(default_factory=dict)    # users → arrivals per second
    latency: dict[int, dict[str, Any]] = field(default_factory=dict)   # users → {expected_ms, p99_ms, saturated, …}
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)  # key → capacity.Resource.to_dict() + extras
    utilisation: dict[int, dict[str, float | None]] = field(default_factory=dict)  # users → key → ρ (None = SAT)
    thresholds: dict[str, float] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    tail_factor: float = 1.3

    def to_dict(self) -> dict[str, Any]:
        return {"users": self.users, "rps": self.rps, "latency": self.latency, "traffic": self.traffic,
                "resources": self.resources, "utilisation": self.utilisation, "thresholds": self.thresholds,
                "assumptions": self.assumptions, "tail_factor": self.tail_factor}


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
    load: LoadSummary | None = None      # filled only by the `load` walker (M8)


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
    load: LoadSummary | None = None      # the `load` walker's sweep (see Result.load)
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
            "load": self.load.to_dict() if self.load else {},
        }
