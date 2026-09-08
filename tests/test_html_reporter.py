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
from iacsim.reporter.html_ import SECTION_TITLES, HtmlReporter, _embed, render_page
from iacsim.reporter.json_ import JsonReporter

TITLES = [title for _key, title in SECTION_TITLES]


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


def test_diff_html_renders():
    from iacsim.diff.differ import run_diff
    before, after = EXAMPLES / "classic-web", EXAMPLES / "classic-web-bad"
    report, g_before, g_after = run_diff(before, load_config(before), after, load_config(after))
    html = REPORTERS.get("html")().render_diff(report, g_before, g_after)
    blob = json.loads(html.split('id="iacsim-data">', 1)[1].split("</script>", 1)[0])
    assert blob["kind"] == "diff" and blob["diff"]["graph"]["nodes_moved"]
    assert set(blob["diff"]["graphs"]) == {"before", "after"} and blob["diff"]["generated_at"]
