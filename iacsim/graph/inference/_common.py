"""Small helpers shared by the inference rules (same stage, so importing is fine)."""

from __future__ import annotations

import re
import textwrap
from collections.abc import Iterable
from typing import Any

from iacsim.core.models import Confidence, InfraGraph, NodeKind, RawResource, RawResources
from iacsim.core.refs import addresses_in, split_address


def raws_of_type(raw: RawResources, *types: str) -> list[RawResource]:
    return [r for r in raw.resources if r.type in types]


def raw_by_address(raw: RawResources) -> dict[str, RawResource]:
    return {r.address: r for r in raw.resources}


def first_node(graph: InfraGraph, value: Any, kinds: Iterable[NodeKind] | None = None) -> str | None:
    """First address mentioned in `value` that is a node in the graph (optionally of given kinds)."""
    wanted = set(kinds) if kinds else None
    for address in addresses_in(value):
        node = graph.nodes.get(address)
        if node and (wanted is None or node.kind in wanted):
            return address
    return None


def task_definition_of(raws: dict[str, RawResource], r: RawResource | None) -> RawResource | None:
    """An ECS service's container config lives on its task definition (glue,
    not a node): follow `task_definition` so env vars and roles can be read
    as if they were on the service."""
    if r is None or r.type not in ("aws_ecs_service",):
        return None
    for address in addresses_in(r.attrs.get("task_definition")):
        td = raws.get(address)
        if td is not None and td.type == "aws_ecs_task_definition":
            return td
    return None


def short(address: str) -> str:
    """Readable form for evidence text.

    module.table["workflows"].aws_dynamodb_table.this → table["workflows"]
    module.compute.aws_instance.this["us-east-1a"]    → compute.aws_instance["us-east-1a"]
    aws_sfn_state_machine.workflow                     → aws_sfn_state_machine.workflow
    """
    ref = split_address(address)
    segments = _segments(ref.address)
    modules = [segments[i + 1] for i in range(0, len(segments) - 2, 2) if segments[i] == "module"]
    rtype, rname = segments[-2], segments[-1]
    if modules and rname.split("[", 1)[0] in ("this", "main"):
        suffix = rname[rname.index("["):] if "[" in rname else ""
        return modules[-1] + (f".{rtype}{suffix}" if suffix else "")
    return f"{rtype}.{rname}"


def _segments(address: str) -> list[str]:
    out, buf, depth = [], [], 0
    for ch in address:
        depth += (ch == "[") - (ch == "]")
        if ch == "." and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


# ------------------------------------------------------------------ GCP helpers (WP5)
# Additive: nothing above changed. The GCP rules share three needs the AWS ones
# never had — HCL blocks arrive as one-element lists, a Terraform heredoc keeps
# its `<<EOF … EOF` wrapper, and a target is often named by a literal URL or
# name rather than a placeholder.

def block(value: Any) -> dict[str, Any]:
    """A nested HCL block as one dict: `template { }` is emitted as a one-element
    list; a plain map is returned as-is; anything else is an empty dict."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return value if isinstance(value, dict) else {}


def blocks(value: Any) -> list[dict[str, Any]]:
    """Every repetition of a block (`backend { } backend { }`) as a list of dicts."""
    if isinstance(value, dict):
        return [value]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


_HEREDOC = re.compile(r'^\s*"?<<-?\s*(?P<marker>[A-Za-z_][A-Za-z0-9_]*)\r?\n(?P<body>.*)\n\s*(?P=marker)"?\s*$',
                      re.DOTALL)


def heredoc_body(text: str) -> str:
    """The body of a Terraform heredoc as the parser hands it over (`<<-EOT … EOT`,
    sometimes still wrapped in quotes), dedented, with Terraform's `$${` escape
    turned back into the `${` the embedded language meant. A plain string is
    returned unchanged."""
    m = _HEREDOC.match(text)
    body = m.group("body") if m else text
    return textwrap.dedent(body).replace("$${", "${")


def label_index(graph: InfraGraph, kinds: Iterable[NodeKind] | None = None,
                subtypes: Iterable[str] | None = None) -> dict[str, list[str]]:
    """{label → [node id, …]} over the nodes of the given kinds / subtypes, for
    matching a literal name (`"orders"`) the way a placeholder would match."""
    wanted_kinds = set(kinds) if kinds else None
    wanted_subtypes = set(subtypes) if subtypes else None
    out: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if not node.label:
            continue
        if wanted_kinds is not None and node.kind not in wanted_kinds:
            continue
        if wanted_subtypes is not None and node.subtype not in wanted_subtypes:
            continue
        out.setdefault(node.label, []).append(node.id)
    return out


# Literal service URLs: `https://api-abc123-uc.a.run.app`, `https://api-123456.us-central1.run.app`
# (Cloud Run and Cloud Functions v2), `https://us-central1-proj.cloudfunctions.net/fn` (v1) and the
# Workflows execution REST endpoint. The host's first label is `<service name>-<hash|project number>`.
_RUN_APP = re.compile(r"https?://([a-z0-9-]+)(?:\.[a-z0-9-]+)*\.run\.app", re.IGNORECASE)
_CLOUDFUNCTIONS = re.compile(r"https?://[^/\s]+\.cloudfunctions\.net/([A-Za-z0-9_-]+)")
_WORKFLOW_EXEC = re.compile(
    r"workflowexecutions\.googleapis\.com/v\d\w*/projects/[^/]+/locations/[^/]+/workflows/([\w-]+)")
URL_TARGET_KINDS = (NodeKind.COMPUTE, NodeKind.ORCHESTRATOR, NodeKind.GATEWAY)


def node_for_url(graph: InfraGraph, url: Any) -> tuple[str, Confidence, str] | None:
    """The node a URL points at, with how sure we are and why.

    A placeholder (`${google_cloud_run_v2_service.api.uri}/path`) is HIGH. A
    literal `*.run.app` host is matched on the service-name prefix of its first
    label, a `cloudfunctions.net/<fn>` path on the function name and a Workflows
    execution URL on the workflow name — all LOW, and the note says so."""
    if not isinstance(url, str):
        return None
    target = first_node(graph, url, URL_TARGET_KINDS)
    if target:
        return target, Confidence.HIGH, "a placeholder"
    if m := _RUN_APP.search(url):
        host = m.group(1).lower()
        best = None
        for label, ids in label_index(graph, subtypes=("cloud_run", "cloud_functions_v2")).items():
            named = host == label.lower() or host.startswith(label.lower() + "-")
            if named and (best is None or len(label) > len(best[0])):
                best = (label, ids[0])
        if best:
            return best[1], Confidence.LOW, f"a literal run.app URL, matched on service name '{best[0]}'"
    if m := _CLOUDFUNCTIONS.search(url):
        ids = label_index(graph, subtypes=("cloud_functions", "cloud_functions_v2")).get(m.group(1))
        if ids:
            return ids[0], Confidence.LOW, f"a literal cloudfunctions.net URL, matched on function name '{m.group(1)}'"
    if m := _WORKFLOW_EXEC.search(url):
        ids = label_index(graph, subtypes=("workflows",)).get(m.group(1))
        if ids:
            return ids[0], Confidence.LOW, f"a literal Workflows execution URL, matched on workflow name '{m.group(1)}'"
    return None


def instance_template_of(raws: dict[str, RawResource], r: RawResource | None) -> RawResource | None:
    """A managed instance group's VMs are configured on its instance template
    (glue, not a node): follow `version[].instance_template` so startup scripts
    and service accounts can be read as if they were on the group — the GCP twin
    of `task_definition_of`."""
    if r is None or not r.type.endswith("instance_group_manager"):
        return None
    for address in addresses_in(r.attrs.get("version")):
        template = raws.get(address)
        if template is not None and template.type.endswith("instance_template"):
            return template
    return None
