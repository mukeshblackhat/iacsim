import pytest
from conftest import edge

from iacsim.core.models import EdgeKind, NodeKind

API_GW = "aws_api_gateway_rest_api.main"
API = "module.api.aws_lambda_function.this"
SFN = "aws_sfn_state_machine.workflow"
BUCKET = "aws_s3_bucket.outputs"
TABLES = ["workflows", "executions", "checkout_sessions", "credit_transactions", "workspaces",
          "workspace_members", "public_workflow_shares", "payment_idempotency", "published_apps", "app_executions"]
ASL_WORKERS = ["parser", "input_preparer", "output_updater", "finalizer", "text_enhancement", "image_generation",
               "image_to_image", "video_creation", "lipsync_processing", "router", "text_iterator", "html_template"]


def worker(name: str) -> str:
    return f'module.worker["{name}"].aws_lambda_function.this'


def table(name: str) -> str:
    return f'module.table["{name}"].aws_dynamodb_table.this'


def test_no_warnings_and_node_census(foosh):
    graph, raw = foosh
    assert raw.warnings == [] and graph.warnings == []
    kinds = {}
    for n in graph.nodes.values():
        kinds[(n.kind, n.subtype)] = kinds.get((n.kind, n.subtype), 0) + 1
    assert kinds[(NodeKind.COMPUTE, "lambda")] == 16
    assert kinds[(NodeKind.DATASTORE, "dynamodb")] == 10
    assert kinds[(NodeKind.DATASTORE, "s3")] == 1
    assert kinds[(NodeKind.GATEWAY, "api_gateway")] == 1
    assert kinds[(NodeKind.ORCHESTRATOR, "step_functions")] == 1


def test_lambda_attrs_come_from_module_inputs(foosh):
    graph, _ = foosh
    assert graph.nodes[worker("video_creation")].attrs["memory_size"] == 3008
    assert graph.nodes[worker("video_creation")].attrs["timeout"] == 900
    assert graph.nodes[API].attrs["reserved_concurrent_executions"] == 100
    assert graph.nodes[API].label == "async-workflow-api-staging"
    assert graph.nodes[table("workflows")].label == "AsyncWorkflowsStaging"       # real name (name_override)


def test_api_gateway_invokes_api_lambda(foosh):
    graph, _ = foosh
    e = edge(graph, "internet", API_GW)
    assert e.rule == "normaliser"
    e = edge(graph, API_GW, API)
    assert e.kind == EdgeKind.INVOKE and e.rule in ("lambda_permission", "api_gateway_integration")


def test_api_lambda_reaches_every_table_the_bucket_and_the_state_machine(foosh):
    graph, _ = foosh
    for t in TABLES:
        e = edge(graph, API, table(t))
        assert e.kind == EdgeKind.READ and e.rule in ("env_var", "iam_policy")
    assert edge(graph, API, BUCKET).kind == EdgeKind.READ
    e = edge(graph, API, SFN)
    assert e.kind == EdgeKind.INVOKE and e.rule in ("env_var", "iam_policy")
    assert "STATE_MACHINE_ARN" in e.evidence or "states:StartExecution" in e.evidence


def test_state_machine_invokes_every_task_lambda_from_the_asl(foosh):
    graph, _ = foosh
    for w in ASL_WORKERS:
        e = edge(graph, SFN, worker(w))
        assert e.kind == EdgeKind.INVOKE and e.rule == "step_functions", w
        assert "workflow.asl.json" in e.evidence


def test_iam_fills_in_lambdas_the_asl_never_calls(foosh):
    graph, _ = foosh
    for w in ("text_input", "image_input", "video_input"):
        e = edge(graph, SFN, worker(w))
        assert e.rule == "iam_policy" and e.kind == EdgeKind.INVOKE


def test_workflow_structure_is_recorded_for_the_simulator(foosh):
    graph, _ = foosh
    wf = graph.nodes[SFN].attrs["workflow"]
    assert [s["state"] for s in wf] == ["ParseWorkflow", "NodeExecution", "FinalizeWorkflow"]
    node_map = wf[1]
    assert node_map["type"] == "Map" and node_map["concurrency"] == 5
    body = node_map["body"]
    assert body[0]["target"] == worker("input_preparer")
    choice = body[1]
    assert choice["type"] == "Choice"
    image = choice["branches"]["ExecuteImageGeneration"]
    assert [s["state"] for s in image][:2] == ["ExecuteImageGeneration", "UpdateOutputs"]
    assert image[0]["target"] == worker("image_generation")
    assert any(s["type"] == "Wait" and s["seconds"] == 20 for s in _flatten(image))


def _flatten(steps):
    for s in steps:
        yield s
        for nested in (s.get("body") or []):
            yield from _flatten([nested])
        for branch in (s.get("branches") or {}).values() if isinstance(s.get("branches"), dict) else (s.get("branches") or []):
            yield from _flatten(branch)


# ---------------------------------------------------------------- M2: run

def test_start_workflow_attributes_every_table_read_to_the_api_lambda(foosh_run):
    from conftest import result
    r = result(foosh_run, "start_workflow")
    assert r.warnings == []
    srcs = {h.src for h in r.hops if h.dst.startswith("module.table[")}
    assert srcs == {API}
    write = next(h for h in r.hops if h.dst == table("executions"))
    assert write.breakdown["processing"] == 8.0            # op: write
    assert r.hops[-1].dst == API and "response leg" in r.hops[-1].evidence


def test_run_workflow_costs_parallel_as_max_and_fanout_once(foosh_run):
    from conftest import result
    r = result(foosh_run, "run_workflow_3_nodes")
    assert r.warnings == []
    assert r.shape["parallel_groups"] == 1 and r.shape["fanout_copies"] == 3
    critical = sum(h.latency_ms for h in r.hops if h.on_critical_path)
    assert r.total_ms == pytest.approx(critical)
    assert r.shape["parallel_savings_ms"] > 0
    off = [h for h in r.hops if not h.on_critical_path]
    assert {h.dst for h in off} >= {worker("html_template")}
    # every worker invocation is attributed to the state machine, not the previous worker
    for h in r.hops:
        if h.dst.startswith("module.worker["):
            assert h.src == SFN, (h.src, h.dst)
