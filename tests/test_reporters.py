"""Every reporter renders every example; report.json matches the documented contract."""

import json

import pytest

from iacsim.core.interfaces import REPORTERS

SCENARIO_KEYS = {"name", "description", "source", "total_ms", "percentiles", "samples", "load", "shape", "profile",
                 "hops",
                 "findings", "warnings"}
FINDING_KEYS = {"analyzer", "subject", "latency_ms", "share", "detail", "refs", "layer", "additive"}
HOP_KEYS = {"src", "dst", "label", "latency_ms", "breakdown", "evidence", "on_critical_path", "group", "percentiles"}
ANALYZERS = {"per_hop", "per_node", "per_category", "critical_path", "recommendations", "tail_risk"}


@pytest.fixture(params=["classic_web_run", "classic_web_bad_run", "foosh_run"])
def output(request):
    return request.getfixturevalue(request.param)


@pytest.mark.parametrize("name", ["text", "markdown", "json"])
def test_every_reporter_renders_every_example(output, name):
    rendered = REPORTERS.get(name)().render(output.findings, output.graph)
    assert rendered and output.findings[0].scenario in rendered


def test_text_report_has_the_brief_sections(classic_web_bad_run):
    text = REPORTERS.get("text")().render(classic_web_bad_run.findings, classic_web_bad_run.graph)
    for section in ("Where the time goes", "Top bottlenecks", "Recommendations", "Hops"):
        assert section in text
    assert "Critical path" not in text.split("scenario:")[1]      # page_load has no parallel group


def test_all_hops_flag_shows_path_order(foosh_run):
    top = REPORTERS.get("text")(top_n=3).render(foosh_run.findings, foosh_run.graph)
    everything = REPORTERS.get("text")(all_hops=True).render(foosh_run.findings, foosh_run.graph)
    assert "top 3 of" in top and "all 13 hops in path order" in everything


def test_markdown_is_github_tables(classic_web_run):
    md = REPORTERS.get("markdown")().render(classic_web_run.findings, classic_web_run.graph)
    assert md.startswith("# page_load") and "| layer | category |" in md and "<sub>" in md


def test_json_contract(output):
    doc = json.loads(REPORTERS.get("json")().render(output.findings, output.graph))
    assert doc["schema_version"] == "2" and doc["generated_at"] and "sources" in doc["profile"]
    assert set(doc["graph"]) >= {"nodes", "edges", "warnings"}
    for scenario in doc["scenarios"]:
        assert set(scenario) == SCENARIO_KEYS
        assert set(scenario["findings"]) <= ANALYZERS
        for hop in scenario["hops"]:
            assert set(hop) == HOP_KEYS
        for items in scenario["findings"].values():
            for finding in items:
                assert set(finding) == FINDING_KEYS
        additive = [f["share"] for f in scenario["findings"]["per_category"] if f["additive"]]
        assert sum(additive) == pytest.approx(1.0)
