"""Every CLI command through typer's CliRunner against the real examples."""

import json
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iacsim.cli import app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args])


@pytest.fixture(scope="module")
def ex(tmp_path_factory):
    """Throwaway copies of the examples the CLI writes into (.iacsim/), so the
    suite never touches examples/ and tests do not depend on each other's output."""
    from conftest import example_copy
    root = tmp_path_factory.mktemp("examples")
    names = ("classic-web", "classic-web-bad", "foosh-serverless", "foosh-cfn")
    return {name: example_copy(name, root) for name in names}


def test_run_text_prints_the_brief(ex):
    r = invoke("run", ex["classic-web"], "-o", "text")
    assert r.exit_code == 0, r.output
    assert "scenario: page_load" in r.output and "Where the time goes" in r.output


def test_run_json_and_markdown_write_files(ex, tmp_path):
    r = invoke("run", ex["classic-web-bad"], "-o", "json", "-o", "markdown")
    assert r.exit_code == 0, r.output
    out = ex["classic-web-bad"] / ".iacsim"
    assert (out / "report.json").is_file() and (out / "report.md").is_file()
    doc = json.loads((out / "report.json").read_text())
    assert doc["schema_version"] == "2" and doc["scenarios"][0]["total_ms"] > 300


def test_run_with_profile_override_changes_the_number(ex, tmp_path):
    profile = tmp_path / "slow-db.yaml"
    profile.write_text("processing:\n  rds:\n    defaults: {read: 500, write: 500}\n")
    base = json.loads(invoke("run", ex["classic-web"], "-o", "json").output and
                      (ex["classic-web"] / ".iacsim" / "report.json").read_text())
    r = invoke("run", ex["classic-web"], "-o", "json", "--profile", profile)
    assert r.exit_code == 0, r.output
    slow = json.loads((ex["classic-web"] / ".iacsim" / "report.json").read_text())
    assert slow["scenarios"][0]["total_ms"] > base["scenarios"][0]["total_ms"] + 900
    assert slow["profile"]["sources"] == ["defaults", str(profile)]


def test_run_monte_carlo_reports_percentiles(ex):
    r = invoke("run", ex["foosh-serverless"], "-o", "text", "--walker", "monte_carlo",
               "--samples", "200", "--seed", "1")
    assert r.exit_code == 0, r.output
    assert "p50" in r.output and "p99" in r.output and "samples" in r.output
    assert "Tail risk" in r.output


def test_graph_writes_graph_json(ex):
    r = invoke("graph", ex["classic-web"])
    assert r.exit_code == 0 and re.search(r"\d+ nodes, \d+ edges", r.output)
    assert (ex["classic-web"] / ".iacsim" / "graph.json").is_file()


def test_graph_accepts_a_cloudformation_template_file(ex):
    r = invoke("graph", ex["foosh-cfn"] / "template.json", "--region", "us-east-1")
    assert r.exit_code == 0 and "30 nodes" in r.output


def test_validate_is_clean_on_the_examples_and_prints_the_profile(ex):
    r = invoke("validate", ex["foosh-serverless"])
    assert r.exit_code == 0, r.output
    assert "ok:" in r.output and "profile: defaults" in r.output


def test_validate_warns_on_unwired_scenario_steps(tmp_path):
    from conftest import example_copy
    tmp_path = example_copy("classic-web", tmp_path)
    (tmp_path / "scenarios.yaml").write_text(
        "odd:\n  entry: module.load_balancer.aws_lb.this\n  steps:\n    - module.network.aws_vpc.this\n")
    r = invoke("validate", tmp_path)
    assert r.exit_code == 1 and "has no inferred edges" in r.output


def test_diff_reports_the_regression_and_exits_2_past_the_threshold(ex):
    r = invoke("diff", ex["classic-web"], ex["classic-web-bad"], "-o", "text")
    assert r.exit_code == 0, r.output
    assert "moved" in r.output and "database" in r.output and "+298" in r.output
    r2 = invoke("diff", ex["classic-web"], ex["classic-web-bad"], "-o", "json",
                "--fail-on-regression", "50ms")
    assert r2.exit_code == 2 and "REGRESSION" in r2.output
    r3 = invoke("diff", ex["classic-web"], ex["classic-web-bad"], "-o", "json",
                "--fail-on-regression", "1000%")
    assert r3.exit_code == 0


def test_diff_scenario_filter_and_bad_threshold(ex):
    r = invoke("diff", ex["foosh-serverless"], ex["foosh-serverless"], "-o", "json",
               "--scenario", "poll_status")
    assert r.exit_code == 0 and "no latency change" in r.output
    bad = invoke("diff", ex["classic-web"], ex["classic-web-bad"], "--fail-on-regression", "soon")
    assert bad.exit_code != 0


def test_run_html_writes_report_html(ex):
    r = invoke("run", ex["classic-web"], "-o", "html")
    assert r.exit_code == 0, r.output
    page = ex["classic-web"] / ".iacsim" / "report.html"
    assert page.is_file() and f"wrote {page}" in r.output
    html = page.read_text()
    assert html.startswith("<!doctype html>") and "<title>iacsim dashboard</title>" in html
    assert '"kind":"report"' in html and "page_load" in html and "__IACSIM_DATA__" not in html


def test_diff_html_writes_diff_html(ex):
    r = invoke("diff", ex["classic-web"], ex["classic-web-bad"], "-o", "html")
    assert r.exit_code == 0, r.output
    page = ex["classic-web-bad"] / ".iacsim" / "diff.html"
    assert page.is_file()
    html = page.read_text()
    assert '"kind":"diff"' in html and "nodes_moved" in html and "eu-west-1" in html


def test_view_prepares_the_directory_and_serves_briefly(ex):
    r = invoke("view", ex["classic-web"], "--no-open", "--duration", "0.3")
    assert r.exit_code == 0, r.output
    out = ex["classic-web"] / ".iacsim"
    assert (out / "index.html").is_file() and (out / "report.json").is_file()
    assert re.search(r"viewer: http://127\.0\.0\.1:\d+/", r.output)
    assert "<title>iacsim dashboard</title>" in (out / "index.html").read_text()


def test_view_runs_the_pipeline_when_report_is_missing(tmp_path):
    from conftest import example_copy
    tmp_path = example_copy("classic-web", tmp_path)
    r = invoke("view", tmp_path, "--no-open", "--duration", "0.2")
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".iacsim" / "report.json").is_file() and (tmp_path / ".iacsim" / "graph.json").is_file()


def test_view_refreshes_a_stale_report(tmp_path):
    """report.json is rebuilt only when an input beside the target is newer than it."""
    import os

    from conftest import example_copy

    from iacsim.core.config import load_config
    from iacsim.viewer import is_stale, prepare
    target = example_copy("classic-web", tmp_path)
    cfg = load_config(target, {"report.outputs": ["json"]})
    out = prepare(target, cfg)
    report = out / "report.json"
    assert not is_stale(report, target)
    first = report.stat().st_mtime_ns
    prepare(target, cfg)                                   # nothing newer -> untouched
    assert report.stat().st_mtime_ns == first
    newer = first + 2_000_000_000
    os.utime(target / "main.tf", ns=(newer, newer))        # edit the Terraform
    assert is_stale(report, target)
    prepare(target, cfg)
    assert report.stat().st_mtime_ns > first


def test_plugins_lists_every_extension_point():
    r = invoke("plugins")
    assert r.exit_code == 0
    for line in ("parsers", "walkers", "analyzers", "reporters", "metric sources"):
        assert line in r.output
    assert "monte_carlo" in r.output and "tail_risk" in r.output


FIXTURE = EXAMPLES / "foosh-serverless" / "calibrate-fixture.yaml"


def test_calibrate_writes_a_profile_and_reports_coverage(ex, tmp_path):
    # source + fixture come from examples/foosh-serverless/iacsim.yaml — no flags needed,
    # exactly how a team would point at their own monitoring
    out = tmp_path / "measured.yaml"
    r = invoke("calibrate", ex["foosh-serverless"], "--out", out)
    assert r.exit_code == 0, r.output
    assert "calibrated 26 node(s)" in r.output and "skipped 2 node(s)" in r.output
    assert "no data in window" in r.output and "router" in r.output
    assert "(cold, cold_prob from defaults)" in r.output          # the partial text-input entry
    assert "next: iacsim run" in r.output and out.is_file()
    text = out.read_text()
    assert text.startswith("# generated by iacsim calibrate") and "by_label:" in text
    assert "distance:" not in text and "defaults:" not in text    # an overlay, not a full profile
    dry = invoke("calibrate", ex["foosh-serverless"], "--out", tmp_path / "nope.yaml", "--dry-run")
    assert dry.exit_code == 0 and "dry run" in dry.output and not (tmp_path / "nope.yaml").exists()


def _measured_profile(ex, tmp_path):
    """A calibrated profile for foosh, produced through the real CLI."""
    out = tmp_path / "measured.yaml"
    r = invoke("calibrate", ex["foosh-serverless"], "--out", out)
    assert r.exit_code == 0, r.output
    return out


def _report(ex, target):
    return json.loads((ex[target] / ".iacsim" / "report.json").read_text())


def _scenario(doc, name):
    return next(s for s in doc["scenarios"] if s["name"] == name)


def test_run_with_calibrated_profile_shows_the_rung_and_measured_numbers(ex, tmp_path):
    measured = _measured_profile(ex, tmp_path)
    assert invoke("run", ex["foosh-serverless"], "-o", "json").exit_code == 0
    before = _scenario(_report(ex, "foosh-serverless"), "run_workflow_3_nodes")
    r = invoke("run", ex["foosh-serverless"], "-o", "json", "--profile", measured)
    assert r.exit_code == 0, r.output
    doc = _report(ex, "foosh-serverless")
    assert doc["profile"]["sources"] == ["defaults", f"{measured} (fake, 7d)"]
    after = _scenario(doc, "run_workflow_3_nodes")
    image_hop = next(h for h in after["hops"] if "image-generation" in h["dst"] or "image_generation" in h["dst"])
    assert image_hop["latency_ms"] > 15_000
    assert after["total_ms"] > before["total_ms"] + 15_000


def test_calibrated_cold_starts_widen_the_tail(ex, tmp_path):
    """api Lambda: cold 1200 @ 8 % measured vs 400 @ 5 % default → a wider p99 − p50
    on start_workflow (the parallel image branch dominates run_workflow_3_nodes)."""
    measured = _measured_profile(ex, tmp_path)
    mc = ["run", ex["foosh-serverless"], "-o", "json", "--walker", "monte_carlo",
          "--samples", "500", "--seed", "1"]
    assert invoke(*mc).exit_code == 0
    base = _scenario(_report(ex, "foosh-serverless"), "start_workflow")["percentiles"]
    assert invoke(*mc, "--profile", measured).exit_code == 0
    cal = _scenario(_report(ex, "foosh-serverless"), "start_workflow")["percentiles"]
    assert cal["p99"] - cal["p50"] > base["p99"] - base["p50"]


def test_calibrated_profile_serves_the_cloudformation_graph_via_by_label(ex, tmp_path):
    measured = _measured_profile(ex, tmp_path)
    r = invoke("run", ex["foosh-cfn"], "-o", "json", "--profile", measured)
    assert r.exit_code == 0, r.output
    doc = _report(ex, "foosh-cfn")
    hops = [h for s in doc["scenarios"] for h in s["hops"]]
    api_hops = [h for h in hops if "ApiLambda" in h["dst"]]
    assert api_hops and all(h["breakdown"].get("processing") == 60 for h in api_hops)


def test_calibrate_rejects_unknown_source_and_reports_missing_boto3(ex, monkeypatch):
    r = invoke("calibrate", ex["classic-web"], "--source", "nope")
    assert r.exit_code != 0 and "fake" in r.output and "cloudwatch" in r.output
    monkeypatch.setitem(sys.modules, "boto3", None)      # simulate `pip install` without [calibrate]
    r = invoke("calibrate", ex["classic-web"], "--source", "cloudwatch")
    assert r.exit_code == 3 and "boto3" in r.output


def test_calibrate_with_nothing_measurable_exits_1(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("lambda: {}\n")
    from conftest import example_copy
    tmp_path = example_copy("classic-web", tmp_path)
    (tmp_path / "iacsim.yaml").write_text(f"calibrate:\n  sources:\n    fake:\n      fixture: {empty}\n")
    r = invoke("calibrate", tmp_path, "--source", "fake", "--out", tmp_path / "x.yaml")
    assert r.exit_code == 1 and "nothing calibrated" in r.output


@pytest.mark.parametrize("cmd", [[], ["--help"], ["run", "--help"]])
def test_help(cmd):
    r = invoke(*cmd)
    assert "Infrastructure-as-Code" in r.output or "Usage" in r.output


# ---------------------------------------------------------------- M8: --walker load

def test_run_load_walker_writes_capacity(ex):
    import json
    target = ex["foosh-serverless"]
    r = invoke("run", target, "--walker", "load", "--profile", target / "calibrated.yaml", "-o", "json", "-o", "text")
    assert r.exit_code == 0, r.output
    assert "users until it breaks" in r.output and "first to break" in r.output
    doc = json.loads((target / ".iacsim" / "report.json").read_text())
    util_max = doc["capacity"]["utilisation"]["10000"]                 # linear in users: hottest breaks first
    assert doc["capacity"]["first_to_break"]["resource"] == max(util_max, key=util_max.get)
    assert doc["scenarios"][0]["load"]["users"] == [100, 500, 1000, 2000, 5000, 10000]
    assert "Infinity" not in json.dumps(doc)


def test_run_load_walker_without_load_file_is_a_clean_error(ex):
    r = invoke("run", ex["classic-web"], "--walker", "load")
    assert r.exit_code == 2 and "needs a load profile" in r.output


def test_run_load_walker_with_a_bad_load_file_exits_2(ex, tmp_path):
    bad = tmp_path / "load.yaml"
    bad.write_text("per_user: {nope: {every: 1s}}\n")
    r = invoke("run", ex["classic-web"], "--walker", "load", "--load", bad)
    assert r.exit_code == 2 and "unknown scenario" in r.output
