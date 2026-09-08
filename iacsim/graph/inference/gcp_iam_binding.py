"""Inference rule: gcp_iam_binding                                          [M11]

    google_project_iam_{member,binding}.{role, member(s)}                 → project-wide grant
    google_<resource>_iam_{member,binding}.{role, member(s)} + <resource> → resource-scoped grant
    member = "serviceAccount:<email>"      → the google_service_account (placeholder, or a literal
                                             <account_id>@<project>.iam.gserviceaccount.com)
    member = "serviceAccount:<pool>.svc.id.goog[ns/ksa]" → the GKE cluster with that workload pool
    compute / orchestrator *.service_account* (any depth: template.service_account,
        template.spec.service_account_name, service_config.service_account_email,
        service_account { email }, a MIG's instance template) → the principals using it
    role                                   → what the principal may do to the target

GCP IAM is a binding, not a policy document (G16): `role` names the verb set and
`member` names the principal; there is no action list and no resource list. So
the role is classified by its last segment —

    *.invoker                       → INVOKE   (compute / orchestrator targets only)
    pubsub.publisher                → PUBLISH  (queue targets only)
    pubsub.subscriber               → CONSUME, drawn *from* the queue to the principal
    *viewer / *reader / *.client    → READ
    *creator / *writer              → WRITE
    *user / *editor / *admin / *owner → READ + WRITE

— and a project-wide grant targets every node whose subtype the role's service
implies (`roles/datastore.user` → firestore, `roles/storage.objectViewer` → gcs);
a resource-scoped grant targets the resource it is attached to. Medium
confidence: permission is not proof of a call. Ops are joined per (principal,
target) so `objectViewer` + `objectCreator` is one edge with ops [READ, WRITE],
kind READ (KIND_PRIORITY).

Deliberately left: `*_iam_policy` (a `policy_data` document from a data source,
opaque here); primitive `roles/{viewer,editor,owner}` (too broad to be evidence
of a call); control-plane roles (`run.admin`, `iam.serviceAccountUser`, logging,
monitoring — no data-plane meaning → no edge); `allUsers` / `allAuthenticatedUsers`
(a public entry point is the normaliser's decision, G13); Google-managed service
agents (`service-<n>@gcp-sa-*`) that match no service account in the stack.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import (
    KIND_PRIORITY,
    Confidence,
    Edge,
    EdgeKind,
    InfraGraph,
    NodeKind,
    RawResource,
    RawResources,
)
from iacsim.core.refs import addresses_in
from iacsim.graph.inference._common import block, instance_template_of, raw_by_address, raws_of_type, short

GRANT = re.compile(r"^google_(?P<family>.+)_iam_(member|binding)$")
SA_EMAIL = re.compile(r"^(?P<account>[a-z][a-z0-9-]*)@(?P<project>[a-z0-9-]+)\.iam\.gserviceaccount\.com$")
WORKLOAD_IDENTITY = re.compile(r"^(?P<pool>[a-z0-9-]+\.svc\.id\.goog)\[(?P<ksa>[^\]]+)\]$")
GRANT_META_ATTRS = ("role", "member", "members", "condition", "project", "location", "region", "zone")

# role service → node subtypes a project-wide grant reaches
SERVICE_SUBTYPES: dict[str, tuple[str, ...]] = {
    "pubsub": ("pubsub", "pubsub_subscription"),
    "storage": ("gcs",),
    "datastore": ("firestore",), "firestore": ("firestore",),
    "cloudsql": ("cloud_sql",),
    "spanner": ("spanner",),
    "bigquery": ("bigquery",),
    "bigtable": ("bigtable",),
    "redis": ("memorystore",), "memorystore": ("memorystore",),
    "alloydb": ("alloydb",),
    "file": ("filestore",),
    "run": ("cloud_run", "cloud_run_job", "cloud_run_worker_pool", "cloud_functions_v2"),
    "cloudfunctions": ("cloud_functions", "cloud_functions_v2"),
    "workflows": ("workflows",),
    "cloudtasks": ("cloud_tasks",),
    "aiplatform": ("vertex_index", "vertex_index_endpoint"),
    "documentai": ("document_ai",),
}
READ_WRITE_NAMES = ("user", "editor", "admin", "owner")
WRITE_NAMES = ("creator", "writer")
READ_NAMES = ("viewer", "reader", "client")


@INFERENCE_RULES.register("gcp_iam_binding")
class GcpIamBindingRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)
        accounts = _ServiceAccounts(graph, raw, raws)
        found: dict[tuple[str, str], _Found] = {}
        for grant in raw.resources:
            if not GRANT.match(grant.type):
                continue
            role = grant.attrs.get("role")
            if not isinstance(role, str):
                continue
            targets = _targets(graph, raws, grant, role)
            if not targets:
                continue
            for member in _members(grant.attrs):
                for principal, who in accounts.principals_for(member):
                    for target in targets:
                        if principal == target:
                            continue
                        for src, dst, ops in _edges_for(graph, principal, target, role):
                            item = found.setdefault((src, dst), _Found())
                            item.ops.update(ops)
                            item.evidence.append(
                                f"{short(grant.address)} grants {role} to {member.split(':', 1)[-1]} "
                                f"({who}) on {short(target)}")
        edges: list[Edge] = []
        for (src, dst), item in found.items():
            ops = sorted(item.ops, key=KIND_PRIORITY.index)
            edges.append(Edge(src, dst, ops[0], Confidence.MEDIUM, "; ".join(dict.fromkeys(item.evidence)), ops=ops))
        return edges


class _Found:
    def __init__(self) -> None:
        self.ops: set[EdgeKind] = set()
        self.evidence: list[str] = []


# ------------------------------------------------------------------ members → principals

class _ServiceAccounts:
    """member string → the compute / orchestrator nodes that run as that account."""

    def __init__(self, graph: InfraGraph, raw: RawResources, raws: dict[str, RawResource]) -> None:
        self.graph = graph
        self.by_account_id: dict[str, str] = {}
        for sa in raws_of_type(raw, "google_service_account"):
            if isinstance(sa.attrs.get("account_id"), str):
                self.by_account_id[sa.attrs["account_id"]] = sa.address
        self.users: dict[str, list[str]] = defaultdict(list)          # sa address | literal email → node ids
        for node in graph.nodes.values():
            if node.kind not in (NodeKind.COMPUTE, NodeKind.ORCHESTRATOR):
                continue
            holders = [h for h in (raws.get(node.id), instance_template_of(raws, raws.get(node.id))) if h]
            for holder in holders:
                for value in _service_account_values(holder.attrs):
                    for address in addresses_in(value):
                        self.users[address].append(node.id)
                    if isinstance(value, str) and "${" not in value:
                        self.users[value].append(node.id)
        self.clusters = [(n.id, block(raws[n.id].attrs.get("workload_identity_config")).get("workload_pool"))
                         for n in graph.nodes_of_kind(NodeKind.COMPUTE) if n.subtype == "gke_cluster" and n.id in raws]

    def principals_for(self, member: str) -> list[tuple[str, str]]:
        """[(node id, how we know)] for one `member` string."""
        kind, _, ident = member.partition(":")
        if kind != "serviceAccount" or not ident:
            return []
        if m := WORKLOAD_IDENTITY.match(ident):
            return self._workload_identity(m.group("pool"))
        candidates: list[str] = list(addresses_in(ident))
        if not candidates and (m := SA_EMAIL.match(ident)) and m.group("account") in self.by_account_id:
            candidates.append(self.by_account_id[m.group("account")])
        out: list[tuple[str, str]] = []
        for sa in dict.fromkeys(candidates):
            out += [(node, f"the service account of {short(node)}") for node in self.users.get(sa, [])]
        out += [(node, f"the service account of {short(node)}") for node in self.users.get(ident, [])]
        return list(dict.fromkeys(out))

    def _workload_identity(self, pool: str) -> list[tuple[str, str]]:
        matched = [cid for cid, cluster_pool in self.clusters
                   if isinstance(cluster_pool, str) and cluster_pool == pool]
        if not matched and len(self.clusters) == 1:
            matched = [self.clusters[0][0]]
        return [(cid, f"workload identity on {short(cid)}") for cid in matched]


def _service_account_values(attrs: dict[str, Any], path: tuple[str, ...] = ()) -> Iterator[Any]:
    """Every leaf under a key path that mentions `service_account`, at any depth
    (`service_account`, `template.service_account`, `service_account { email }`)."""
    for key, value in attrs.items():
        here = (*path, key)
        if isinstance(value, dict):
            yield from _service_account_values(value, here)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _service_account_values(item, here)
                elif any("service_account" in seg for seg in here):
                    yield item
        elif any("service_account" in seg for seg in here):
            yield value


def _members(attrs: dict[str, Any]) -> list[str]:
    raw = attrs.get("members") if "members" in attrs else attrs.get("member")
    values = raw if isinstance(raw, list) else [raw]
    return [m for m in values if isinstance(m, str)]


# ------------------------------------------------------------------ grants → targets

def _targets(graph: InfraGraph, raws: dict[str, RawResource], grant: RawResource, role: str) -> list[str]:
    family = GRANT.match(grant.type).group("family")
    if family == "project":
        return _project_targets(graph, role)
    for key, value in grant.attrs.items():                       # the resource the grant is attached to
        if key in GRANT_META_ATTRS:
            continue
        for address in addresses_in(value):
            if address in graph.nodes:
                return [address]
    literal = next((v for k, v in grant.attrs.items() if k not in GRANT_META_ATTRS and isinstance(v, str)), None)
    if literal:                                                  # a literal name: match nodes of the family
        return [n.id for n in graph.nodes.values()
                if n.label == literal and n.id in raws and raws[n.id].type == f"google_{family}"]
    return []


def _project_targets(graph: InfraGraph, role: str) -> list[str]:
    service = role.removeprefix("roles/").split(".", 1)
    if len(service) < 2:
        return []                                                # a primitive role: too broad to be evidence
    subtypes = SERVICE_SUBTYPES.get(service[0], ())
    return [n.id for n in graph.nodes.values() if n.subtype in subtypes]


# ------------------------------------------------------------------ role → operations

def _edges_for(graph: InfraGraph, principal: str, target: str, role: str) -> list[tuple[str, str, list[EdgeKind]]]:
    """(src, dst, ops) for one grant on one target — the subscriber carve-out
    reverses the edge: consuming is queue → principal, like an event source mapping."""
    name = role.rsplit(".", 1)[-1].lower() if "." in role else ""
    kind = graph.nodes[target].kind
    if kind == NodeKind.QUEUE:
        if name == "subscriber":
            return [(target, principal, [EdgeKind.CONSUME])]
        if name == "publisher" or name.endswith(READ_WRITE_NAMES):
            return [(principal, target, [EdgeKind.PUBLISH])]
        return []
    if kind in (NodeKind.COMPUTE, NodeKind.ORCHESTRATOR):
        return [(principal, target, [EdgeKind.INVOKE])] if name.endswith("invoker") else []
    if kind == NodeKind.DATASTORE:
        if name.endswith(READ_WRITE_NAMES):
            return [(principal, target, [EdgeKind.READ, EdgeKind.WRITE])]
        if name.endswith(WRITE_NAMES):
            return [(principal, target, [EdgeKind.WRITE])]
        if name.endswith(READ_NAMES):
            return [(principal, target, [EdgeKind.READ])]
    return []
