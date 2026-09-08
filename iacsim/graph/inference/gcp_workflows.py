"""Inference rule: gcp_workflows                                            [M11]

Reads each Workflows definition (`google_workflows_workflow.source_contents`, a
YAML string — usually a heredoc) and emits orchestrator → target edges for every
step that calls a service, in the order the workflow runs them. As with Step
Functions, for a serverless stack the definition *is* the call graph. Also wires
Cloud Scheduler jobs, the other GCP orchestrator, to what they fire.

Definition sources (RawResource.attrs["source_contents"]):
  - a YAML string, heredoc wrapper and `$${` escapes removed (`heredoc_body`)
  - {"__templatefile__": ...} or a `${file()}` placeholder → skipped with a warning

Call targets (step `call:` + `args:`):
  http.get / post / put / patch / delete / request   args.url → INVOKE (placeholder HIGH,
                                                       literal *.run.app URL LOW — see node_for_url)
  googleapis.run.*  / googleapis.cloudfunctions.*    args.name → INVOKE the job / service / function
  googleapis.workflowexecutions.*                    args.parent → INVOKE the workflow
  googleapis.pubsub.v1.projects.topics.publish       args.topic → PUBLISH
  googleapis.cloudtasks.*.tasks.create               args.parent → PUBLISH the queue
  googleapis.firestore.* / googleapis.storage.*      READ or WRITE by method; args.bucket for storage
  googleapis.bigquery.v2.jobs.*                      READ / WRITE (query / insert)
  <subworkflow name>                                 inlined
A literal name (`projects/p/topics/orders`) is matched on the node label
(MEDIUM); a Workflows expression (`${"namespaces/" + project + "/jobs/" + job}`)
cannot be evaluated, so when the graph holds exactly one node of the kind the
connector addresses that node is taken at LOW confidence, and the evidence says so.

    google_cloud_scheduler_job.http_target.uri          → INVOKE the service at that URL
    google_cloud_scheduler_job.pubsub_target.topic_name → PUBLISH

The walked structure is stored on the workflow node as attrs["workflow"], the
same `WorkflowStep.to_dict()` list the step_functions rule records, so the
traversal replays ordering, `parallel:` branches, `for:` loops and `switch:`
choices unchanged: sequential steps → Task, `parallel.branches` → Parallel,
`parallel.for` / `for` → Map, `switch` → Choice (each `next:` continues from
that step, `steps:` runs inline; the fall-through is the `default` branch),
`sys.sleep` → Wait.

Deliberately left: `except:` blocks (an error path), `retry:` policies,
`events.await_callback`, and connectors not listed above (no edge, no warning).
"""

from __future__ import annotations

from typing import Any

import yaml

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources, WorkflowStep
from iacsim.graph.inference._common import (
    block,
    first_node,
    heredoc_body,
    label_index,
    node_for_url,
    raw_by_address,
    raws_of_type,
    short,
)

HTTP_CALLS = ("http.get", "http.post", "http.put", "http.patch", "http.delete", "http.request")

# connector prefix (longest match wins) → (edge kind, argument naming the target, node kinds, subtypes)
CONNECTORS: dict[str, tuple[EdgeKind, str, tuple[NodeKind, ...], tuple[str, ...]]] = {
    "googleapis.run.v1.namespaces.jobs.run": (EdgeKind.INVOKE, "name", (NodeKind.COMPUTE,), ("cloud_run_job",)),
    "googleapis.run.v2.projects.locations.jobs.run": (EdgeKind.INVOKE, "name", (NodeKind.COMPUTE,), ("cloud_run_job",)),
    "googleapis.run.": (EdgeKind.INVOKE, "name", (NodeKind.COMPUTE,), ("cloud_run", "cloud_run_job")),
    "googleapis.cloudfunctions.": (EdgeKind.INVOKE, "name", (NodeKind.COMPUTE,),
                                   ("cloud_functions", "cloud_functions_v2")),
    "googleapis.workflowexecutions.": (EdgeKind.INVOKE, "parent", (NodeKind.ORCHESTRATOR,), ("workflows",)),
    "googleapis.pubsub.v1.projects.topics.publish": (EdgeKind.PUBLISH, "topic", (NodeKind.QUEUE,), ("pubsub",)),
    "googleapis.cloudtasks.": (EdgeKind.PUBLISH, "parent", (NodeKind.QUEUE,), ("cloud_tasks",)),
    "googleapis.firestore.": (EdgeKind.READ, "name", (NodeKind.DATASTORE,), ("firestore",)),
    "googleapis.storage.": (EdgeKind.READ, "bucket", (NodeKind.DATASTORE,), ("gcs",)),
    "googleapis.bigquery.": (EdgeKind.READ, "projectId", (NodeKind.DATASTORE,), ("bigquery",)),
}
WRITE_METHODS = ("insert", "patch", "create", "createdocument", "delete", "commit", "batchwrite", "update", "copy",
                 "rewrite", "compose", "insertall", "tabledata.insertall")
FLOW_END = ("end", "break", "continue")


@INFERENCE_RULES.register("gcp_workflows")
class GcpWorkflowsRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        raws = raw_by_address(raw)
        edges: list[Edge] = []
        for wf in graph.nodes_of_kind(NodeKind.ORCHESTRATOR):
            r = raws.get(wf.id)
            if r is None or r.type != "google_workflows_workflow":
                continue
            document, problem = _load(r.attrs.get("source_contents"))
            if document is None:
                graph.warnings.append(f"{wf.id}: workflow source_contents could not be read ({problem})")
                continue
            walker = _Walker(graph, wf.id, document)
            wf.attrs["workflow"] = [step.to_dict() for step in walker.walk()]
            edges.extend(walker.edges)
        edges.extend(_scheduler_jobs(graph, raw))
        return edges


# ------------------------------------------------------------------ definition loading

def _load(value: Any) -> tuple[dict | None, str]:
    """{subworkflow name → {params, steps}} from the source, or (None, why not)."""
    if isinstance(value, dict) and "__templatefile__" in value:
        return None, "a templatefile() the parser did not render"
    if not isinstance(value, str):
        return None, "no source_contents string"
    if "${file(" in value or value.strip().startswith("${"):
        return None, "a file() / expression the parser did not render"
    try:
        document = yaml.safe_load(heredoc_body(value))
    except yaml.YAMLError as e:
        return None, f"invalid YAML ({str(e).splitlines()[0]})"
    if isinstance(document, list):                                   # the single-list form: just steps
        return {"main": {"steps": document}}, "ok"
    if isinstance(document, dict) and document:
        if "steps" in document and "main" not in document:
            return {"main": document}, "ok"
        return document, "ok"
    return None, "not a workflow document"


# ------------------------------------------------------------------ walking

class _Walker:
    def __init__(self, graph: InfraGraph, workflow: str, document: dict) -> None:
        self.graph = graph
        self.workflow = workflow
        self.subworkflows = {name: body for name, body in document.items() if isinstance(body, dict)}
        self.edges: list[Edge] = []
        self._seen_targets: set[str] = set()

    def walk(self) -> list[WorkflowStep]:
        main = self.subworkflows.get("main") or next(iter(self.subworkflows.values()), {})
        return self._flow(_steps(main.get("steps")), 0, frozenset(), depth=0)

    def _flow(self, steps: list[tuple[str, dict]], start: int, on_path: frozenset[str],
              depth: int) -> list[WorkflowStep]:
        out: list[WorkflowStep] = []
        i = start
        while 0 <= i < len(steps):
            name, body = steps[i]
            if name in on_path:
                break                                             # a loop back: the path has been walked
            on_path = on_path | {name}
            out.extend(self._visit(name, body, steps, i, on_path, depth))
            if "switch" in body:
                break                                             # continuations live inside the Choice
            nxt = body.get("next")
            if isinstance(nxt, str):
                if nxt in FLOW_END or (i := _index(steps, nxt)) is None:
                    break
            else:
                i += 1
        return out

    def _visit(self, name: str, body: Any, steps: list[tuple[str, dict]], i: int,
               on_path: frozenset[str], depth: int) -> list[WorkflowStep]:
        if not isinstance(body, dict):
            return []
        if "steps" in body:
            return self._flow(_steps(body["steps"]), 0, frozenset(), depth)
        if "try" in body:
            return self._visit(name, body["try"], steps, i, on_path, depth)
        if "parallel" in body:
            return [self._parallel(name, block(body["parallel"]), depth)]
        if "for" in body:
            return [WorkflowStep("Map", name, body=self._flow(_steps(block(body["for"]).get("steps")), 0,
                                                              frozenset(), depth))]
        if "switch" in body:
            return [self._switch(name, body, steps, i, on_path, depth)]
        if "call" in body:
            return self._call(name, body, depth)
        return []                                                 # assign / return / raise / next-only

    def _parallel(self, name: str, spec: dict, depth: int) -> WorkflowStep:
        if "for" in spec:
            loop = block(spec["for"])
            return WorkflowStep("Map", name, concurrency=_int(spec.get("concurrency_limit")),
                                body=self._flow(_steps(loop.get("steps")), 0, frozenset(), depth))
        branches = []
        for branch in spec.get("branches") or []:
            for _branch_name, branch_body in (branch.items() if isinstance(branch, dict) else []):
                if isinstance(branch_body, dict):
                    branches.append(self._flow(_steps(branch_body.get("steps")), 0, frozenset(), depth))
        return WorkflowStep("Parallel", name, branches=branches)

    def _switch(self, name: str, body: dict, steps: list[tuple[str, dict]], i: int,
                on_path: frozenset[str], depth: int) -> WorkflowStep:
        choices: dict[str, list[WorkflowStep]] = {}
        for n, case in enumerate(body.get("switch") or [], start=1):
            if not isinstance(case, dict):
                continue
            if isinstance(case.get("next"), str):
                choices[case["next"]] = self._continue(steps, case["next"], on_path, depth)
            elif "steps" in case:
                choices[f"case{n}"] = (self._flow(_steps(case["steps"]), 0, frozenset(), depth)
                                       + self._flow(steps, i + 1, on_path, depth))
        fallthrough = body.get("next") if isinstance(body.get("next"), str) else None
        choices["default"] = (self._continue(steps, fallthrough, on_path, depth) if fallthrough
                              else self._flow(steps, i + 1, on_path, depth))
        return WorkflowStep("Choice", name, choices=choices)

    def _continue(self, steps: list[tuple[str, dict]], target: str, on_path: frozenset[str],
                  depth: int) -> list[WorkflowStep]:
        index = None if target in FLOW_END else _index(steps, target)
        return [] if index is None else self._flow(steps, index, on_path, depth)

    def _call(self, name: str, body: dict, depth: int) -> list[WorkflowStep]:
        call = body.get("call")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if not isinstance(call, str):
            return []
        if call == "sys.sleep":
            return [WorkflowStep("Wait", name, seconds=_float(args.get("seconds")))]
        if call in self.subworkflows and depth < 8:                          # a subworkflow: inline it
            return self._flow(_steps(self.subworkflows[call].get("steps")), 0, frozenset(), depth + 1)
        if call in HTTP_CALLS:
            hit = node_for_url(self.graph, args.get("url"))
            resource = args.get("url") if isinstance(args.get("url"), str) else call
            if hit is None:
                return [WorkflowStep("Task", name, kind=EdgeKind.INVOKE.value, resource=resource)]
            target, confidence, how = hit
            self._emit(name, call, target, EdgeKind.INVOKE, confidence, f"args.url is {how}")
            return [WorkflowStep("Task", name, target=target, kind=EdgeKind.INVOKE.value, resource=resource)]
        connector = max((p for p in CONNECTORS if call.startswith(p)), key=len, default=None)
        if connector is None:
            return []
        kind, arg, kinds, subtypes = CONNECTORS[connector]
        if kind == EdgeKind.READ and call.rsplit(".", 1)[-1].lower().startswith(WRITE_METHODS):
            kind = EdgeKind.WRITE
        hit = self._resolve(args.get(arg), kinds, subtypes)
        if hit is None:
            return [WorkflowStep("Task", name, kind=kind.value, resource=call)]
        target, confidence, how = hit
        self._emit(name, call, target, kind, confidence, f"args.{arg} is {how}")
        return [WorkflowStep("Task", name, target=target, kind=kind.value, resource=call)]

    def _resolve(self, value: Any, kinds: tuple[NodeKind, ...],
                 subtypes: tuple[str, ...]) -> tuple[str, Confidence, str] | None:
        if (target := first_node(self.graph, value, kinds)) is not None:
            return target, Confidence.HIGH, "a placeholder"
        if isinstance(value, str) and "${" not in value:
            ids = label_index(self.graph, subtypes=subtypes).get(value.rsplit("/", 1)[-1])
            if ids:
                return ids[0], Confidence.MEDIUM, f"a literal name matching {short(ids[0])}"
        candidates = [n.id for n in self.graph.nodes.values() if n.subtype in subtypes]
        if len(candidates) == 1:
            only = candidates[0]
            return only, Confidence.LOW, f"an expression; {short(only)} is the only {subtypes[0]} node"
        return None

    def _emit(self, name: str, call: str, target: str, kind: EdgeKind, confidence: Confidence, how: str) -> None:
        if target == self.workflow or target in self._seen_targets:
            return
        self._seen_targets.add(target)
        self.edges.append(Edge(
            self.workflow, target, kind, confidence,
            f"step '{name}' (call {call}) in {short(self.workflow)} source_contents calls {short(target)}: {how}",
        ))


# ------------------------------------------------------------------ Cloud Scheduler

def _scheduler_jobs(graph: InfraGraph, raw: RawResources) -> list[Edge]:
    edges: list[Edge] = []
    for job in raws_of_type(raw, "google_cloud_scheduler_job"):
        if job.address not in graph.nodes:
            continue
        uri = block(job.attrs.get("http_target")).get("uri")
        if (hit := node_for_url(graph, uri)) is not None and hit[0] != job.address:
            edges.append(Edge(job.address, hit[0], EdgeKind.INVOKE, hit[1],
                              f"{short(job.address)} http_target.uri is {hit[2]} for {short(hit[0])}"))
        topic = first_node(graph, block(job.attrs.get("pubsub_target")).get("topic_name"), [NodeKind.QUEUE])
        if topic:
            edges.append(Edge(job.address, topic, EdgeKind.PUBLISH, Confidence.HIGH,
                              f"{short(job.address)} pubsub_target.topic_name is {short(topic)}"))
    return edges


# ------------------------------------------------------------------ small helpers

def _steps(value: Any) -> list[tuple[str, dict]]:
    """A Workflows `steps:` list of single-key maps → [(name, body)]."""
    out: list[tuple[str, dict]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and len(item) == 1:
            (name, body), = item.items()
            if isinstance(body, dict):
                out.append((str(name), body))
    return out


def _index(steps: list[tuple[str, dict]], name: str) -> int | None:
    return next((i for i, (n, _) in enumerate(steps) if n == name), None)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
