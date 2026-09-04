"""Resources, their capacity, and the queueing math.                       [M8]

A *resource* is what a request occupies while a hop is served. Most nodes are
their own resource; Lambdas without reserved concurrency share one virtual
resource — the account's unreserved pool — because that is how AWS allocates
them. Every resource has `slots` (how many requests it serves at once):

    lambda, reserved     concurrency                        (attrs.concurrency)
    lambda, unreserved   pool: account_concurrency − Σ reserved   (profile capacity.lambda)
    ec2 / fargate        instances × rps_per_class × S      (rps-capped → slots = rps × service time)
    rds                  max_connections[instance_class]
    dynamodb             on-demand: ondemand_rps × S; provisioned: (read+write capacity) × S
    alb / api_gateway / s3 / sqs / sns / elasticache / step_functions   rps × S

so utilisation is uniformly ρ = offered_erlangs / slots, where offered erlangs
a = Σ arrivals/s × hold time (s). For an rps-capped resource that collapses to
ρ = λ / rps, as it should.

Queueing: M/M/c (Erlang C) — Poisson arrivals, exponential service, `slots`
servers. Mean queue wait W_q = C(c, a) · S / (c − a); the 99th percentile of
the queue wait is −ln(0.01 / C) · S / (c − a) when C > 0.01, else 0. ρ ≥ 1 is
"saturated": waits are unbounded and reported as None. Very large c (> 20 000)
is treated as M/M/∞: no wait below saturation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from iacsim.core.models import InfraGraph, Node, NodeKind, Profile

UNRESERVED_POOL = "lambda:unreserved-pool"
INFINITE_SERVERS = 20_000
RPS_CAPPED = {"ec2", "fargate", "dynamodb", "alb", "api_gateway", "api_gateway_v2", "s3", "sqs", "sns",
              "elasticache", "cloudfront", "kinesis", "step_functions"}


@dataclass
class Resource:
    key: str                              # node id, or UNRESERVED_POOL
    label: str
    subtype: str
    slots: float | None = None            # concurrency-limited resources
    rps: float | None = None              # throughput-limited resources
    members: list[str] = field(default_factory=list)   # node ids that share this resource
    source: str = ""                      # which IaC attribute / profile key set the capacity
    # filled by the walker:
    erlangs: float = 0.0                  # offered load Σ λ·hold (s)
    arrivals_per_s: float = 0.0
    by_scenario: dict[str, float] = field(default_factory=dict)   # erlangs contributed per scenario

    @property
    def mean_hold_s(self) -> float:
        return self.erlangs / self.arrivals_per_s if self.arrivals_per_s else 0.0

    def servers(self) -> float:
        """Effective c for M/M/c."""
        if self.slots is not None:
            return float(self.slots)
        return max(1.0, (self.rps or 0.0) * self.mean_hold_s)

    def utilisation(self) -> float:
        c = self.servers()
        return self.erlangs / c if c else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "subtype": self.subtype, "slots": self.slots,
                "rps": self.rps, "members": self.members, "source": self.source}


# ------------------------------------------------------------------ resources from the graph

def resources_for(graph: InfraGraph, profile: Profile) -> dict[str, Resource]:
    """One Resource per capacity-bearing node (plus the shared Lambda pool),
    keyed so `resource_of(node)` finds it."""
    cap = profile.capacity or {}
    out: dict[str, Resource] = {}
    reserved_total = 0
    unreserved: list[Node] = []

    for node in graph.nodes.values():
        if node.kind in (NodeKind.NETWORK, NodeKind.EXTERNAL):
            continue
        r = _resource(node, cap)
        if r is None:
            unreserved.append(node)
            continue
        if node.subtype == "lambda":
            reserved_total += int(r.slots or 0)
        out[node.id] = r

    if unreserved:
        account = float((cap.get("lambda") or {}).get("account_concurrency", 1000))
        pool = max(1.0, account - reserved_total)
        out[UNRESERVED_POOL] = Resource(
            key=UNRESERVED_POOL, label="Lambda unreserved pool", subtype="lambda", slots=pool,
            members=[n.id for n in unreserved],
            source=f"capacity.lambda.account_concurrency {account:g} − {reserved_total} reserved = {pool:g} slots "
                   f"shared by {len(unreserved)} function(s)")
    return out


def resource_of(node_id: str, resources: dict[str, Resource], graph: InfraGraph) -> Resource | None:
    if node_id in resources:
        return resources[node_id]
    node = graph.nodes.get(node_id)
    if node is not None and node.subtype == "lambda":
        return resources.get(UNRESERVED_POOL)
    return None


def _resource(node: Node, cap: dict[str, Any]) -> Resource | None:
    a, st = node.attrs, node.subtype
    label = node.label or node.id
    section = cap.get(st) or {}
    if st == "lambda":
        if "concurrency" in a:
            return Resource(node.id, label, st, slots=float(a["concurrency"]),
                            source=f"reserved_concurrent_executions={a['concurrency']} on {node.id}")
        return None                                    # → the shared pool
    if st == "ec2":
        family = str(a.get("instance_type", "")).split(".")[0]
        per = section.get("rps_per_instance") or {}
        rps = float(per.get(family, per.get("default", 300))) * float(a.get("instances", 1))
        return Resource(node.id, label, st, rps=rps,
                        source=f"{a.get('instances', 1)} × {a.get('instance_type', '?')} "
                               f"({per.get(family, per.get('default', 300))} rps each, capacity.ec2)")
    if st == "fargate":
        n = float(a.get("instances", 1))
        rps = n * float(section.get("rps_per_task", 300))
        return Resource(node.id, label, st, rps=rps, source=f"desired_count={n:g} × capacity.fargate.rps_per_task")
    if st == "rds":
        table = section.get("max_connections") or {}
        klass = str(a.get("instance_class", ""))
        conns = float(table.get(klass, table.get("default", 400)))
        return Resource(node.id, label, st, slots=conns,
                        source=f"instance_class={klass or '?'} → max_connections {conns:g} (capacity.rds)")
    if st == "dynamodb":
        if a.get("read_capacity") or a.get("write_capacity"):
            rps = float(a.get("read_capacity") or 0) + float(a.get("write_capacity") or 0)
            return Resource(node.id, label, st, rps=rps,
                            source=f"provisioned read_capacity+write_capacity={rps:g} on {node.id}")
        rps = float(section.get("ondemand_rps", 40000))
        return Resource(node.id, label, st, rps=rps, source=f"PAY_PER_REQUEST → capacity.dynamodb.ondemand_rps {rps:g}")
    if st == "step_functions":
        rps = float(section.get("start_rps", 1000))
        return Resource(node.id, label, st, rps=rps, source="capacity.step_functions.start_rps")
    if st in RPS_CAPPED:
        rps = float(section.get("rps", 10000))
        return Resource(node.id, label, st, rps=rps, source=f"capacity.{st}.rps {rps:g}")
    return None


# ------------------------------------------------------------------ queueing math

def erlang_c(c: float, a: float) -> float:
    """P(wait > 0) for M/M/c with c servers and offered load a erlangs.
    Stable recursion — no factorials. 1.0 at or past saturation."""
    if a <= 0:
        return 0.0
    c_int = math.ceil(c)
    if a >= c_int:
        return 1.0
    total, term = 0.0, 1.0                     # Σ_{k<c} a^k/k!   and   a^k/k! running
    for k in range(c_int):
        total += term
        term *= a / (k + 1)
    last = term * c_int / (c_int - a)          # a^c/c! · c/(c−a)
    return last / (total + last)


@dataclass
class Wait:
    mean_ms: float | None                      # None = saturated
    p99_ms: float | None
    utilisation: float
    saturated: bool


def queue_wait(resource: Resource) -> Wait:
    rho = resource.utilisation()
    c, a, s = resource.servers(), resource.erlangs, resource.mean_hold_s
    if rho >= 1.0:
        return Wait(None, None, rho, True)
    if c > INFINITE_SERVERS or a <= 0:
        return Wait(0.0, 0.0, rho, False)
    pc = erlang_c(c, a)
    rate = (c - a) / s if s else float("inf")          # (cμ − λ) in 1/s
    mean = pc / rate if rate else 0.0
    p99 = max(0.0, -math.log(0.01 / pc) / rate) if pc > 0.01 and rate else 0.0
    return Wait(round(mean * 1000, 3), round(p99 * 1000, 3), rho, False)
