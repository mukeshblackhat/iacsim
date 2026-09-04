"""Inferred scenarios fill gaps without exploding on fan-out-heavy graphs."""

from pathlib import Path

from iacsim.core.interfaces import SCENARIO_SOURCES

RDS = "module.database.aws_db_instance.this"
SFN = "aws_sfn_state_machine.workflow"


def _infer(graph):
    return SCENARIO_SOURCES.get("inferred_from_entrypoints")().load(graph, Path("."))


def test_classic_web_yields_one_path_ending_at_the_database(classic_web):
    graph, _ = classic_web
    scenarios = _infer(graph)
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.source == "inferred" and s.entry == "module.load_balancer.aws_lb.this"
    assert s.steps[-1].node == RDS
    assert s.name.endswith("database.db_instance")


def test_foosh_is_capped_and_deduped_by_leaf_type(foosh):
    graph, _ = foosh
    scenarios = _infer(graph)
    by_entry = {}
    for s in scenarios:
        by_entry.setdefault(s.entry, []).append(s)
    assert list(by_entry) == ["aws_api_gateway_rest_api.main"]
    assert 1 <= len(scenarios) <= 5
    leaves = {graph.nodes[[st for st in s.steps if st.node][-1].node].subtype if s.steps[-1].node else "sfn"
              for s in scenarios}
    assert len(leaves) == len(scenarios)                     # one per leaf subtype


def test_foosh_state_machine_path_is_replayed_from_the_asl(foosh):
    graph, _ = foosh
    sfn_paths = [s for s in _infer(graph) if any(st.node == SFN for st in s.steps)]
    assert len(sfn_paths) == 1
    s = sfn_paths[0]
    visited = [st.node for st in s.steps if st.node]
    assert 'module.worker["parser"].aws_lambda_function.this' in visited
    assert 'module.worker["finalizer"].aws_lambda_function.this' in visited
    assert any(st.parallel for st in s.steps)                 # the Map body
    assert "Choice NodeTypeChoice" in s.description
    assert not any(st.wait_ms for st in _flatten(s.steps))   # no-delay branch chosen


def _flatten(steps):
    for st in steps:
        yield st
        for branch in st.parallel or []:
            yield from _flatten(branch)
