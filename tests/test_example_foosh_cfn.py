"""M5 correctness check: the real Foosh CloudFormation template (cdk synth,
2026-09-04) must produce the same graph as the hand-written Terraform twin
(examples/foosh-serverless), aligned by label.

Every difference is enumerated here on purpose — it is the exact list of
places where the twin and the real stack disagree, not a tolerance."""

from pathlib import Path

from iacsim.core.config import load_config
from iacsim.core.models import EdgeKind, NodeKind
from iacsim.core.pipeline import run
from iacsim.differ import diff_graphs

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# M6 aligned the twin's four resource names with config/environments/staging.json,
# so labels now match one-to-one. Kept as a (now empty) map so the edge tests
# below read the same way if a future rename reopens a gap.
LABEL_GAP: dict[str, str] = {}
# The twin gives every Lambda env vars for all ten tables; the real stack only
# passes seven (+ USER_CREDITS, which has no table) and reaches the other
# three through IAM alone. Same edge, different evidence → different kind.
IAM_ONLY_TABLES = {"PaymentIdempotencyStaging", "PublishedAppsStaging", "AppExecutionsStaging"}
# The twin's ASL (step_functions/workflow.asl.json) is a hand-condensed version
# of the real 200 KB definition. It treats the input nodes as Pass states and
# gives lipsync / image-to-image their own Task states; the real definition
# does the opposite. Both machines still reach all 15 workers (the leftovers
# come through the state machine role's lambda:InvokeFunction grant).
SM = "AsyncWorkflowStagingStateMachine"
SFN_TASKS_ONLY_IN_TWIN = {"async-workflow-lipsync-processing-staging", "async-workflow-image-to-image-staging"}
SFN_TASKS_ONLY_IN_REAL = {"async-workflow-image-input-staging", "async-workflow-text-input-staging"}
# The real state machine role may also write the workflows / executions tables
# (CDK grant_read_write_data); the twin's role only invokes Lambdas.
SFN_TABLES_ONLY_IN_REAL = {"AsyncWorkflowsStaging", "WorkflowExecutionsStagingSF"}
HIGH_CONFIDENCE_RULES = {"normaliser", "lambda_permission", "api_gateway_integration", "step_functions"}


def labels(graph):
    return {n.id: (n.label or n.id) for n in graph.nodes.values()}


def test_parses_cleanly_with_the_same_shape_as_the_twin(foosh, foosh_cfn):
    tf, cfn_graph, cfn_raw = foosh[0], foosh_cfn[0], foosh_cfn[1]
    assert cfn_raw.warnings == [] and cfn_graph.warnings == []
    assert cfn_raw.format == "cloudformation"
    kinds = lambda g: sorted((n.kind, n.subtype) for n in g.nodes.values())
    assert kinds(cfn_graph) == kinds(tf)                     # 16 lambdas, 10 tables, 1 sfn, 1 bucket, 1 api, internet
    assert len(cfn_graph.nodes) == 30
    assert all(n.placement.region == "us-east-1" for n in cfn_graph.nodes.values() if n.kind != NodeKind.EXTERNAL)


def test_node_diff_by_label_is_empty(foosh, foosh_cfn):
    """Every resource in the real template has a twin with the same label — and vice versa."""
    tf, cfn = foosh[0], foosh_cfn[0]
    diff = diff_graphs(tf, cfn, align_by="label")
    assert diff.nodes_removed == [] and diff.nodes_added == [] and diff.nodes_moved == []
    assert sorted(labels(tf).values()) == sorted(labels(cfn).values())


def test_high_confidence_edges_match_exactly(foosh, foosh_cfn):
    """Entry points, API → Lambda, and every Step Functions Task edge agree once
    the four renamed resources are mapped onto each other."""
    tf, cfn = foosh[0], foosh_cfn[0]
    tl, cl = labels(tf), labels(cfn)
    rename = lambda label: LABEL_GAP.get(label, label)
    tf_edges = {(rename(tl[e.src]), rename(tl[e.dst]), e.kind) for e in tf.edges if e.rule in HIGH_CONFIDENCE_RULES}
    cfn_edges = {(cl[e.src], cl[e.dst], e.kind) for e in cfn.edges if e.rule in HIGH_CONFIDENCE_RULES}
    assert tf_edges - cfn_edges == {(SM, x, EdgeKind.INVOKE) for x in SFN_TASKS_ONLY_IN_TWIN}
    assert cfn_edges - tf_edges == {(SM, x, EdgeKind.INVOKE) for x in SFN_TASKS_ONLY_IN_REAL}
    assert len(cfn_edges) == 14                               # internet→api, api gw→api lambda, sfn→12 workers


def test_all_edges_match_except_the_documented_evidence_gap(foosh, foosh_cfn):
    tf, cfn = foosh[0], foosh_cfn[0]
    tl, cl = labels(tf), labels(cfn)
    rename = lambda label: LABEL_GAP.get(label, label)
    tf_edges = {(rename(tl[e.src]), rename(tl[e.dst]), e.kind) for e in tf.edges}
    cfn_edges = {(cl[e.src], cl[e.dst], e.kind) for e in cfn.edges}

    only_tf = tf_edges - cfn_edges
    only_cfn = cfn_edges - tf_edges
    sfn_extra = {(SM, t, EdgeKind.WRITE) for t in SFN_TABLES_ONLY_IN_REAL}
    assert sfn_extra <= only_cfn
    only_cfn -= sfn_extra

    # 16 matched Lambdas × 3 IAM-only tables: env_var READ in the twin, iam_policy WRITE in reality
    assert {(s, d) for s, d, _ in only_tf} == {(s, d) for s, d, _ in only_cfn}
    assert all(k == EdgeKind.READ and d in IAM_ONLY_TABLES for _, d, k in only_tf)
    assert all(k == EdgeKind.WRITE and d in IAM_ONLY_TABLES for _, d, k in only_cfn)
    assert len(only_tf) == 16 * 3

    # Everything else — 146 edges — is identical.
    assert len(tf_edges & cfn_edges) == len(tf_edges) - 48


def test_step_functions_workflow_replays_from_the_real_definition(foosh_cfn):
    graph = foosh_cfn[0]
    sm = next(n for n in graph.nodes.values() if n.subtype == "step_functions")
    workflow = sm.attrs["workflow"]
    assert workflow[0]["type"] == "Task" and workflow[0]["state"] == "ParseWorkflowStaging"
    kinds = [w["type"] for w in workflow]
    assert "Choice" in kinds                                  # ExecutionModeChoice → Map branches
    targets = {e.dst for e in graph.edges if e.src == sm.id and e.rule == "step_functions"}
    assert len(targets) == 12


def test_cfn_fanout_uses_the_inner_map(foosh_cfn):
    """The real definition nests Choice → Map(1) → Map(5) around the node-level
    tasks (and Map(1) alone in the sequential branch): a fan-out on the input
    preparer must see concurrency 5 — and say that the branches disagree."""
    import math

    from iacsim.core.models import NodeKind, Scenario, Step
    from iacsim.simulator.traversal import Planner
    g = foosh_cfn[0] if isinstance(foosh_cfn, tuple) else foosh_cfn
    sfn = next(n.id for n in g.nodes.values() if n.kind == NodeKind.ORCHESTRATOR)
    prep = next(n.id for n in g.nodes.values() if n.label == "async-workflow-input-preparer-staging")
    for count in (3, 10, 11):
        plan = Planner(g, None).plan(Scenario("t", sfn, [Step(fanout=(prep, count))]))
        assert plan.shape["fanout_waves"] == math.ceil(count / 5), count
    assert any("Map states with concurrency {1, 5}" in w for w in plan.warnings)


def test_run_works_on_the_template_with_inferred_scenarios():
    target = EXAMPLES / "foosh-cfn"
    out = run(target, load_config(target))
    assert out.results and all(r.total_ms > 0 for r in out.results)
    assert all(s.source == "inferred" for s in out.scenarios)
