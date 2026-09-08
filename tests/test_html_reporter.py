"""The html reporter: headings held to the Brief, the data blob defused against
`</script>`, the `view` route byte-identical to `-o html`, the diff page renders."""

import copy
import json
import re

import pytest
from conftest import EXAMPLES, tiny_graph

from iacsim.core.config import load_config
from iacsim.core.interfaces import REPORTERS, ReporterOptions
from iacsim.reporter._brief import BriefBuilder
from iacsim.reporter._diff_brief import DiffBriefBuilder
from iacsim.reporter.html_ import DIFF_ORDER, REPORT_SECTIONS, SECTION_TITLES, HtmlReporter, _embed, render_page
from iacsim.reporter.json_ import JsonReporter

TITLES = [title for _key, title in REPORT_SECTIONS]
DIFF_TITLES = [dict(SECTION_TITLES)[key] for key in DIFF_ORDER]


def _run(name: str, **overrides):
    from iacsim.core.pipeline import run
    target = EXAMPLES / name
    return run(target, load_config(target, overrides))


@pytest.fixture(scope="module")
def sampled_run():
    return _run("classic-web", **{"simulation.walker": "monte_carlo", "simulation.samples": 300, "simulation.seed": 7})


@pytest.fixture(scope="module")
def load_run():
    return _run("foosh-serverless", **{"simulation.walker": "load"})


def test_section_titles_match_brief(classic_web_run, classic_web_bad_run, foosh_run, order_queue_run,
                                    sampled_run, load_run):
    """Every heading a real BriefBuilder emits is in SECTION_TITLES, in the same
    relative order — and every SECTION_TITLES entry is emitted by at least one
    example, so the page can neither drift from the terminal nor invent sections."""
    seen: set[str] = set()
    for output in (classic_web_run, classic_web_bad_run, foosh_run, order_queue_run, sampled_run, load_run):
        builder = BriefBuilder(output.graph)
        briefs = [builder.build(f) for f in output.findings]
        warned = copy.copy(output.findings[0])          # no example warns; the section must still be held
        warned.warnings = ["synthetic warning"]
        briefs.append(builder.build(warned))
        capacity = builder.build_capacity(output.findings)
        emitted = [[s.title for s in b.sections] for b in briefs]
        if capacity:
            emitted.append([capacity.title, *(s.title for s in capacity.sections)])
        for titles in emitted:
            assert [t for t in TITLES if t in titles] == titles, titles
            seen.update(titles)
    assert seen == set(TITLES)


def test_embed_defuses_script_close():
    g = tiny_graph()
    g.warnings.append("</script><!--")
    html = render_page({"schema_version": "2", "graph": g.to_dict(), "scenarios": [], "capacity": {},
                        "profile": {"sources": []}, "generated_at": ""})
    blob = html.split('id="iacsim-data">', 1)[1].split("</script>", 1)[0]
    assert "</script><!--" not in blob and "\\u003c/script>" in blob
    assert json.loads(blob)["report"]["graph"]["warnings"] == ["</script><!--"]
    assert "<" not in _embed({"x": "<b>"})


def test_render_page_from_json_dict(classic_web_run):
    """The `view` route (report.json → page) equals the reporter route (-o html)
    byte for byte, once the two timestamps are aligned."""
    stamp = re.compile(r'"generated_at":"[^"]*"')
    via_json = render_page(json.loads(JsonReporter().render(classic_web_run.findings, classic_web_run.graph)))
    direct = HtmlReporter().render(classic_web_run.findings, classic_web_run.graph)
    assert stamp.sub('"generated_at":""', via_json) == stamp.sub('"generated_at":""', direct)
    assert len(stamp.findall(direct)) == 1


def test_options_reach_the_page(classic_web_run):
    html = HtmlReporter(ReporterOptions(top_n=3, all_hops=True)).render(classic_web_run.findings, classic_web_run.graph)
    assert '"options":{"top_n":3,"all_hops":true}' in html
    with pytest.raises(ValueError):
        render_page({}, kind="nope")


@pytest.fixture(scope="module")
def classic_web_diff():
    from iacsim.diff.differ import run_diff
    before, after = EXAMPLES / "classic-web", EXAMPLES / "classic-web-bad"
    return run_diff(before, load_config(before), after, load_config(after))


def test_diff_section_titles_match_brief(classic_web_diff):
    """The diff page's headings are held to `DiffBriefBuilder` the way the report's
    are to `BriefBuilder`: every heading it emits is a known title, in the diff
    group's order, and the classic-web diff exercises every one of them."""
    diff, g_before, g_after = classic_web_diff
    seen: set[str] = set()
    for brief in DiffBriefBuilder(g_before, g_after).build(diff):
        titles = [s.title for s in brief.sections]
        assert [t for t in DIFF_TITLES if t in titles] == titles, titles
        seen.update(titles)
    assert seen == set(DIFF_TITLES)
    assert not (set(DIFF_TITLES) - {"Recommendations"}) & set(TITLES)   # the diff group is its own


def test_diff_html_renders(classic_web_diff):
    report, g_before, g_after = classic_web_diff
    html = REPORTERS.get("html")().render_diff(report, g_before, g_after)
    blob = json.loads(html.split('id="iacsim-data">', 1)[1].split("</script>", 1)[0])
    assert blob["kind"] == "diff"
    moves = {(m["field"], m["before"], m["after"]) for m in blob["diff"]["graph"]["nodes_moved"]}
    assert ("region", "us-east-1", "eu-west-1") in moves
    assert set(blob["diff"]["graphs"]) == {"before", "after"} and blob["diff"]["generated_at"]
    for title in DIFF_TITLES:
        assert title in html
    page_load = next(s for s in blob["diff"]["scenarios"] if s["name"] == "page_load")
    assert f"{page_load['delta_ms']:+.1f}" == "+298.8"          # the page's `signed()` prints this string
    keys = [s["key"] for s in blob["sections"]]
    assert keys[-4:] == ["what_changed", "where_shift", "hops_changed", "bottleneck_shift"]


def test_findings_the_rail_and_tables_read(classic_web_bad_run, foosh_run, sampled_run):
    """The contract the page's rail and tables rely on, held on real runs: the
    additive per_category shares sum to 1.0 (one 100 % bar), per_node is ranked by
    ms (top five = first five), the top bottleneck's display name and every
    category subject are in the page, hop `percentiles` are `{}` unless the walker
    sampled, and tail-risk findings exist only for a sampled run (the page draws
    the "Tail risk" table only then) — with its scenario sentence first."""
    def page(output):
        html = HtmlReporter().render(output.findings, output.graph)
        assert "█" not in html
        return html, json.loads(html.split('id="iacsim-data">', 1)[1].split("</script>", 1)[0])["report"]

    html, report = page(classic_web_bad_run)
    findings = report["scenarios"][0]["findings"]
    additive = [f for f in findings["per_category"] if f["additive"]]
    assert additive and abs(sum(f["share"] for f in additive) - 1.0) < 1e-6
    for f in findings["per_category"]:
        assert f["subject"] in html
    ranked = [f["latency_ms"] for f in findings["per_node"]]
    assert ranked == sorted(ranked, reverse=True)
    assert classic_web_bad_run.graph.display_name(findings["per_node"][0]["subject"]) in html
    for rec in findings["recommendations"]:
        assert ". Based on: " in rec["detail"]                      # the card's reason / "based on" split

    _html, plain = page(foosh_run)
    assert all(not h["percentiles"] for s in plain["scenarios"] for h in s["hops"])
    assert not any("tail_risk" in s["findings"] for s in plain["scenarios"])
    grouped = [s for s in plain["scenarios"] if any(h["group"] for h in s["hops"])]
    assert grouped
    assert all(re.fullmatch(r"parallel\d+/branch\d+", h["group"]) for s in grouped for h in s["hops"] if h["group"])
    branches = [s for s in grouped if len({h["group"] for h in s["hops"] if h["group"]}) > 1]
    assert branches and all("critical_path" in s["findings"] for s in branches)   # one branch → nothing to compare

    _html, sampled = page(sampled_run)
    for s in sampled["scenarios"]:
        assert all({"p50", "p99"} <= set(h["percentiles"]) for h in s["hops"])
        tail = s["findings"]["tail_risk"]
        assert tail[0]["subject"] == "p99 − p50" and tail[0]["refs"] == []
        assert all(" → " in t["subject"] and len(t["refs"]) == 1 for t in tail[1:])
    assert dict(SECTION_TITLES)["tail_risk"] == "Tail risk (p99 − p50)"


def test_drawer_and_keyboard_shell_in_the_page():
    """WP3: the drawer, its close button, the skip link and the reduced-motion rule
    are in the static page — not conjured by script — so they hold with no data."""
    html = render_page({"schema_version": "2", "graph": tiny_graph().to_dict(), "scenarios": [], "capacity": {},
                        "profile": {"sources": []}, "generated_at": ""})
    assert 'id="drawer" role="dialog" aria-modal="false"' in html
    assert 'class="drawer-close"' in html
    assert html.index('class="skip" href="#tables"') < html.index('id="header"')   # the first focusable thing
    assert "prefers-reduced-motion: reduce" in html and ":focus-visible" in html
