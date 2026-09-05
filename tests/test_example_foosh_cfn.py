"""M5 correctness check: the real Foosh CloudFormation template (cdk synth,
2026-09-04) must produce the same graph as the hand-written Terraform twin
(examples/foosh-serverless), aligned by label.

Every difference is enumerated here on purpose — it is the exact list of
places where the twin and the real stack disagree, not a tolerance."""

from pathlib import Path

from iacsim.core.config import load_config
from iacsim.core.models import EdgeKind, NodeKind
from iacsim.core.pipeline import run
from iacsim.diff.differ import diff_graphs

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# M6 aligned the twin's resource names with config/environments/staging.json; WP2
# aligned the env vars (seven tables exported, three IAM-only) and the state
# machine role (read/write on workflows + executions), and made edge kinds
# independent of which rule found them. The only remaining difference is the
# hand-condensed ASL: the twin gives lipsync / image-to-image their own Task
# states and treats the input nodes as Pass states; the real definition does
# the opposite. Both machines still reach all 15 workers (the leftovers come
# through the role's lambda:InvokeFunction grant), so the *edge sets* are equal —
# only the rule that produced four of them differs.
SM = "AsyncWorkflowStagingStateMachine"
SFN_TASKS_ONLY_IN_TWIN = {"async-workflow-lipsync-processing-staging", "async-workflow-image-to-image-staging"}
SFN_TASKS_ONLY_IN_REAL = {"async-workflow-image-input-staging", "async-workflow-text-input-staging"}
HIGH_CONFIDENCE_RULES = {"normaliser", "lambda_permission", "api_gateway_integration", "step_functions"}


def rules(e) -> set[str]:
    return set((e.rule or "").split("+"))


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
    """Entry points, API → Lambda, and every Step Functions Task edge agree,
    except the four Task-vs-Pass differences of the condensed ASL."""
    tf, cfn = foosh[0], foosh_cfn[0]
    tl, cl = labels(tf), labels(cfn)
    high = lambda e: rules(e) & HIGH_CONFIDENCE_RULES
    tf_edges = {(tl[e.src], tl[e.dst], e.kind) for e in tf.edges if high(e)}
    cfn_edges = {(cl[e.src], cl[e.dst], e.kind) for e in cfn.edges if high(e)}
    assert tf_edges - cfn_edges == {(SM, x, EdgeKind.INVOKE) for x in SFN_TASKS_ONLY_IN_TWIN}
    assert cfn_edges - tf_edges == {(SM, x, EdgeKind.INVOKE) for x in SFN_TASKS_ONLY_IN_REAL}
    assert len(cfn_edges) == 14                               # internet→api, api gw→api lambda, sfn→12 workers


def test_every_edge_matches_kind_and_ops(foosh, foosh_cfn):
    """The hand-written Terraform twin and the real cdk synth template produce
    the same edges with the same priced kind and the same set of operations —
    zero differences (WP2)."""
    tf, cfn = foosh[0], foosh_cfn[0]
    tl, cl = labels(tf), labels(cfn)
    tf_edges = {(tl[e.src], tl[e.dst], e.kind, tuple(e.ops)) for e in tf.edges}
    cfn_edges = {(cl[e.src], cl[e.dst], e.kind, tuple(e.ops)) for e in cfn.edges}
    assert tf_edges == cfn_edges
    assert len(tf_edges) == len(tf.edges) == len(cfn.edges)   # no duplicate pairs on either side
    # the three IAM-only tables are reached by every Lambda through the grant alone
    api = "async-workflow-api-staging"
    for t in ("PaymentIdempotencyStaging", "PublishedAppsStaging", "AppExecutionsStaging"):
        for g, lab in ((tf, tl), (cfn, cl)):
            e = next(x for x in g.edges if lab[x.src] == api and lab[x.dst] == t)
            assert e.kind == EdgeKind.READ and e.ops == [EdgeKind.READ, EdgeKind.WRITE] and rules(e) == {"iam_policy"}
    diff = diff_graphs(tf, cfn, align_by="label")
    assert diff.edges_added == [] and diff.edges_removed == []


def test_step_functions_workflow_replays_from_the_real_definition(foosh_cfn):
    graph = foosh_cfn[0]
    sm = next(n for n in graph.nodes.values() if n.subtype == "step_functions")
    workflow = sm.attrs["workflow"]
    assert workflow[0]["type"] == "Task" and workflow[0]["state"] == "ParseWorkflowStaging"
    kinds = [w["type"] for w in workflow]
    assert "Choice" in kinds                                  # ExecutionModeChoice → Map branches
    targets = {e.dst for e in graph.edges if e.src == sm.id and "step_functions" in rules(e)}
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
