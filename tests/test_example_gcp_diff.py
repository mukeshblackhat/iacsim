"""`iacsim diff` on the GCP pair: gcp-web → gcp-web-bad must explain itself as
'Cloud SQL moved to europe-west1' — the whole delta is placement, the LB chain
contributes 0 ms in both (G9), and nothing else moved. The expected delta is
built from the profile, never typed in."""

import json

import pytest
from conftest import example_copy, result
from typer.testing import CliRunner

from iacsim.cli import app
from iacsim.core.interfaces import REPORTERS
from iacsim.diff.differ import diff_reports

RUN = "google_cloud_run_v2_service.web"
DB = "google_sql_database_instance.db"
READS = 2                                   # page_load's two sequential Cloud SQL queries
CHAIN_HOP_INDEXES = (2, 3, 4, 5)            # 1-based: forwarding rule → proxy → URL map → backend → NEG


@pytest.fixture(scope="module")
def gcp_diff(gcp_web_run, gcp_web_bad_run):
    return diff_reports(gcp_web_run.findings, gcp_web_bad_run.findings, gcp_web_run.graph, gcp_web_bad_run.graph)


@pytest.fixture(scope="module")
def cross_region_delta(default_profile):
    """2 × reads × (one-way cross-region − one-way same-region), from the profile."""
    d = default_profile.distance
    return 2 * READS * (d["cross_region"]["europe-west1/us-central1"] - d["same_region_unknown_az"])


def _page_load(report):
    return next(s for s in report.scenarios if s.name == "page_load")


def test_total_delta_is_the_cross_region_penalty(gcp_diff, cross_region_delta):
    page_load = _page_load(gcp_diff)
    assert page_load.status == "both"
    assert page_load.delta_ms == pytest.approx(cross_region_delta, abs=0.1)


def test_exactly_the_two_cloud_sql_hops_changed(gcp_diff, default_profile):
    d = default_profile.distance
    page_load = _page_load(gcp_diff)
    changed = page_load.hops_with("changed")
    assert len(changed) == READS and all(DB in h.label for h in changed)
    assert page_load.hops_with("added") == [] and page_load.hops_with("removed") == []
    before_after = (2 * d["same_region_unknown_az"], 2 * d["cross_region"]["europe-west1/us-central1"])
    assert all(h.breakdown_deltas()["distance"] == pytest.approx(before_after) for h in changed)


def test_the_lb_chain_costs_nothing_in_both(gcp_diff):
    """G9: the four hops inside the chain are 0 ms before and after — moving the
    database cannot move them."""
    chain = [h for h in _page_load(gcp_diff).hops if h.index_before in CHAIN_HOP_INDEXES]
    assert len(chain) == len(CHAIN_HOP_INDEXES)
    assert all(h.status == "unchanged" and h.before_ms == 0 and h.after_ms == 0 for h in chain)


def test_only_the_datastore_hops_differ_hop_by_hop(gcp_web_run, gcp_web_bad_run, default_profile):
    d = default_profile.distance
    good, bad = result(gcp_web_run, "page_load"), result(gcp_web_bad_run, "page_load")
    assert [(h.src, h.dst) for h in good.hops] == [(h.src, h.dst) for h in bad.hops]
    for before, after in zip(good.hops, bad.hops, strict=True):
        if after.dst == DB:
            assert after.breakdown["distance"] == pytest.approx(2 * d["cross_region"]["europe-west1/us-central1"])
        else:
            assert after.breakdown == before.breakdown


def test_graph_level_says_only_the_database_moved(gcp_diff):
    moves = {(m.node_id, m.field, m.before, m.after) for m in gcp_diff.graph.nodes_moved}
    assert moves == {(DB, "region", "us-central1", "europe-west1")}
    # a VPC is global in GCP: unlike classic-web-bad there is no peering to add
    assert gcp_diff.graph.edges_added == [] and gcp_diff.graph.edges_removed == []
    assert gcp_diff.graph.nodes_added == [] and gcp_diff.graph.nodes_removed == []


def test_colocate_recommendation_appears(gcp_diff, cross_region_delta):
    appeared = [r for r in _page_load(gcp_diff).recommendations if r.status == "appeared"]
    assert appeared and appeared[0].subject.startswith("co-locate")
    assert DB in appeared[0].subject or "gcp-web-db" in appeared[0].subject
    assert appeared[0].saving_ms == pytest.approx(cross_region_delta, rel=0.05)


def test_category_shift_is_distance(gcp_diff, cross_region_delta):
    distance = next(c for c in _page_load(gcp_diff).categories if c.subject == "distance")
    assert distance.delta_ms == pytest.approx(cross_region_delta, abs=0.1)
    assert distance.before_share < 0.3 and distance.after_share > 0.75


def test_the_cache_path_is_untouched(gcp_diff):
    cached = next(s for s in gcp_diff.scenarios if s.name == "cached_page")
    assert cached.status == "both" and cached.delta_ms == pytest.approx(0)
    assert cached.hops_with("changed") == []


@pytest.mark.parametrize("name", ["text", "markdown", "json"])
def test_every_reporter_renders_the_diff(gcp_diff, gcp_web_run, gcp_web_bad_run, name):
    out = REPORTERS.get(name)().render_diff(gcp_diff, gcp_web_run.graph, gcp_web_bad_run.graph)
    assert "page_load" in out and "europe-west1" in out
    if name == "json":
        payload = json.loads(out)
        assert payload["graph"]["nodes_moved"][0]["node_id"] == DB
        assert [h["status"] for h in payload["scenarios"][0]["hops"]][:5] == ["unchanged"] * 5


# ------------------------------------------------------------------ CLI exit codes
# The CLI writes diff.* next to the *after* target, so these run on throwaway copies.

@pytest.fixture(scope="module")
def copies(tmp_path_factory):
    root = tmp_path_factory.mktemp("gcp-diff-cli")
    return {name: example_copy(name, root) for name in ("gcp-web", "gcp-web-bad")}


def test_cli_fail_on_regression_exits_2(copies):
    res = CliRunner().invoke(app, ["diff", str(copies["gcp-web"]), str(copies["gcp-web-bad"]),
                                   "--fail-on-regression", "50ms", "-o", "json"])
    assert res.exit_code == 2, res.output
    assert "REGRESSION" in res.output and "page_load" in res.output
    assert (copies["gcp-web-bad"] / ".iacsim" / "diff.json").is_file()


def test_cli_unchanged_scenario_exits_0(copies):
    res = CliRunner().invoke(app, ["diff", str(copies["gcp-web"]), str(copies["gcp-web-bad"]),
                                   "--scenario", "cached_page", "--fail-on-regression", "1ms", "-o", "json"])
    assert res.exit_code == 0, res.output
