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

SCHEMA_VERSION = "1"


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


class Confidence(StrEnum):
    DECLARED = "declared"  # from scenarios.yaml — always trusted
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


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
    rule: str | None = None              # registry name of the inference rule
    latency: Latency | None = None       # filled by latency rules


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
              costed once, the count is recorded in Result.shape
    wait_ms:  a deliberate pause (Step Functions Wait state); no hop, pure cost
    """
    node: str | None = None
    parallel: list[list[Step]] | None = None
    fanout: tuple[str, int] | None = None
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

    def processing_for(self, node: Node) -> dict[str, Any]:
        """per_resource[node.id] → defaults[subtype] → {}"""
        section = self.processing.get(node.subtype, {})
        per_resource = section.get("per_resource", {}).get(node.id)
        return per_resource or section.get("defaults", {})


# ------------------------------------------------------------------ simulation output

@dataclass
class HopResult:
    src: str
    dst: str
    latency_ms: float
    breakdown: dict[str, float]
    evidence: str
    on_critical_path: bool = True


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


@dataclass
class Finding:
    """One line of the bottleneck report."""
    analyzer: str
    subject: str                         # hop "a → b", node id, or category name
    latency_ms: float
    share: float                         # 0..1 of scenario total
    detail: str


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
    schema_version: str = SCHEMA_VERSION


@dataclass
class DiffResult:
    scenario: str
    before_ms: float
    after_ms: float
    hop_deltas: list[tuple[str, float, float]]   # (hop label, before, after)

    @property
    def delta_ms(self) -> float:
        return self.after_ms - self.before_ms
