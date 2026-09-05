"""`iacsim diff` on the real examples: classic-web → classic-web-bad must
explain itself as 'the database moved', foosh vs itself must be silent."""

import json

import pytest
from conftest import example_copy
from typer.testing import CliRunner

from iacsim.cli import app
from iacsim.core.interfaces import REPORTERS
from iacsim.diff.differ import diff_reports

CROSS_REGION_DELTA = 298.8


@pytest.fixture(scope="module")
def classic_diff(classic_web_run, classic_web_bad_run):
    return diff_reports(classic_web_run.findings, classic_web_bad_run.findings,
                        classic_web_run.graph, classic_web_bad_run.graph)


def test_total_delta_is_the_cross_region_penalty(classic_diff):
    page_load = next(s for s in classic_diff.scenarios if s.name == "page_load")
    assert page_load.status == "both"
    assert page_load.delta_ms == pytest.approx(CROSS_REGION_DELTA, abs=0.1)


def test_exactly_the_two_rds_hops_changed(classic_diff):
    page_load = next(s for s in classic_diff.scenarios if s.name == "page_load")
    changed = page_load.hops_with("changed")
    assert len(changed) == 2 and all("aws_db_instance" in h.label for h in changed)
    assert page_load.hops_with("added") == [] and page_load.hops_with("removed") == []
    assert all(h.breakdown_deltas()["distance"] == (0.6, 150.0) for h in changed)


def test_graph_level_says_the_database_moved(classic_diff):
    moves = {(m.node_id, m.field, m.before, m.after) for m in classic_diff.graph.nodes_moved}
    assert ("module.database.aws_db_instance.this", "region", "us-east-1", "eu-west-1") in moves
    assert any("(peer)" in e for e in classic_diff.graph.edges_added)
    assert classic_diff.graph.nodes_removed == []


def test_colocate_recommendation_appears(classic_diff):
    page_load = next(s for s in classic_diff.scenarios if s.name == "page_load")
    appeared = [r for r in page_load.recommendations if r.status == "appeared"]
    assert appeared and appeared[0].subject.startswith("co-locate")
    assert appeared[0].saving_ms == pytest.approx(CROSS_REGION_DELTA, rel=0.05)


def test_category_shift_is_distance(classic_diff):
    page_load = next(s for s in classic_diff.scenarios if s.name == "page_load")
    distance = next(c for c in page_load.categories if c.subject == "distance")
    assert distance.delta_ms == pytest.approx(CROSS_REGION_DELTA, abs=0.1)
    assert distance.before_share < 0.8 and distance.after_share > 0.95


def test_foosh_against_itself_is_empty(foosh_run):
    report = diff_reports(foosh_run.findings, foosh_run.findings, foosh_run.graph, foosh_run.graph)
    assert report.is_empty
    assert all(s.status == "both" and not s.hops_with("changed") for s in report.scenarios)


@pytest.mark.parametrize("name", ["text", "markdown", "json"])
def test_every_reporter_renders_the_diff(classic_diff, classic_web_run, classic_web_bad_run, name):
    out = REPORTERS.get(name)().render_diff(classic_diff, classic_web_run.graph, classic_web_bad_run.graph)
    assert "page_load" in out and "eu-west-1" in out
    if name == "json":
        payload = json.loads(out)
        assert payload["schema_version"] == "2" and payload["graph"]["nodes_moved"]
        assert payload["scenarios"][0]["hops"][2]["status"] == "changed"


# ------------------------------------------------------------------ CLI exit codes
# The CLI writes diff.* next to the *after* target, so these run on throwaway copies.

@pytest.fixture(scope="module")
def copies(tmp_path_factory):
    root = tmp_path_factory.mktemp("diff-cli")
    return {name: example_copy(name, root) for name in ("classic-web", "classic-web-bad", "foosh-serverless")}


def test_cli_fail_on_regression_exits_2(copies):
    result = CliRunner().invoke(app, ["diff", str(copies["classic-web"]), str(copies["classic-web-bad"]),
                                      "--fail-on-regression", "50ms", "-o", "json"])
    assert result.exit_code == 2, result.output
    assert "REGRESSION" in result.output


def test_cli_no_change_exits_0(copies):
    result = CliRunner().invoke(app, ["diff", str(copies["foosh-serverless"]), str(copies["foosh-serverless"]),
                                      "--fail-on-regression", "1ms", "-o", "json"])
    assert result.exit_code == 0, result.output
    assert "no latency change" in result.output


def test_cli_scenario_filter(copies):
    result = CliRunner().invoke(app, ["diff", str(copies["classic-web"]), str(copies["classic-web-bad"]),
                                      "--scenario", "page_load", "-o", "markdown"])
    assert result.exit_code == 0, result.output
    md = (copies["classic-web-bad"] / ".iacsim" / "diff.md").read_text()
    assert md.count("# ") >= 2 and "load_balancer.lb/database" not in md
