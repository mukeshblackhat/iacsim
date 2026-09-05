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


def test_run_text_prints_the_brief():
    r = invoke("run", EXAMPLES / "classic-web", "-o", "text")
    assert r.exit_code == 0, r.output
    assert "scenario: page_load" in r.output and "Where the time goes" in r.output


def test_run_json_and_markdown_write_files(tmp_path):
    r = invoke("run", EXAMPLES / "classic-web-bad", "-o", "json", "-o", "markdown")
    assert r.exit_code == 0, r.output
    out = EXAMPLES / "classic-web-bad" / ".iacsim"
    assert (out / "report.json").is_file() and (out / "report.md").is_file()
    doc = json.loads((out / "report.json").read_text())
    assert doc["schema_version"] == "2" and doc["scenarios"][0]["total_ms"] > 300


def test_run_with_profile_override_changes_the_number(tmp_path):
    profile = tmp_path / "slow-db.yaml"
    profile.write_text("processing:\n  rds:\n    defaults: {read: 500, write: 500}\n")
    base = json.loads(invoke("run", EXAMPLES / "classic-web", "-o", "json").output and
                      (EXAMPLES / "classic-web" / ".iacsim" / "report.json").read_text())
    r = invoke("run", EXAMPLES / "classic-web", "-o", "json", "--profile", profile)
    assert r.exit_code == 0, r.output
    slow = json.loads((EXAMPLES / "classic-web" / ".iacsim" / "report.json").read_text())
    assert slow["scenarios"][0]["total_ms"] > base["scenarios"][0]["total_ms"] + 900
    assert slow["profile"]["sources"] == ["defaults", str(profile)]


def test_run_monte_carlo_reports_percentiles():
    r = invoke("run", EXAMPLES / "foosh-serverless", "-o", "text", "--walker", "monte_carlo",
               "--samples", "200", "--seed", "1")
    assert r.exit_code == 0, r.output
    assert "p50" in r.output and "p99" in r.output and "samples" in r.output
    assert "Tail risk" in r.output


def test_graph_writes_graph_json():
    r = invoke("graph", EXAMPLES / "classic-web")
    assert r.exit_code == 0 and re.search(r"\d+ nodes, \d+ edges", r.output)
    assert (EXAMPLES / "classic-web" / ".iacsim" / "graph.json").is_file()


def test_graph_accepts_a_cloudformation_template_file():
    r = invoke("graph", EXAMPLES / "foosh-cfn" / "template.json", "--region", "us-east-1")
    assert r.exit_code == 0 and "30 nodes" in r.output


def test_validate_is_clean_on_the_examples_and_prints_the_profile():
    r = invoke("validate", EXAMPLES / "foosh-serverless")
    assert r.exit_code == 0, r.output
    assert "ok:" in r.output and "profile: defaults" in r.output


def test_validate_warns_on_unwired_scenario_steps(tmp_path):
    for f in (EXAMPLES / "classic-web").glob("*.tf"):
        (tmp_path / f.name).write_text(f.read_text().replace('"../modules/', f'"{EXAMPLES / "modules"}/'))
    (tmp_path / "scenarios.yaml").write_text(
        "odd:\n  entry: module.load_balancer.aws_lb.this\n  steps:\n    - module.network.aws_vpc.this\n")
    r = invoke("validate", tmp_path)
    assert r.exit_code == 1 and "has no inferred edges" in r.output


def test_diff_reports_the_regression_and_exits_2_past_the_threshold():
    r = invoke("diff", EXAMPLES / "classic-web", EXAMPLES / "classic-web-bad", "-o", "text")
    assert r.exit_code == 0, r.output
    assert "moved" in r.output and "database" in r.output and "+298" in r.output
    r2 = invoke("diff", EXAMPLES / "classic-web", EXAMPLES / "classic-web-bad", "-o", "json",
                "--fail-on-regression", "50ms")
    assert r2.exit_code == 2 and "REGRESSION" in r2.output
    r3 = invoke("diff", EXAMPLES / "classic-web", EXAMPLES / "classic-web-bad", "-o", "json",
                "--fail-on-regression", "1000%")
    assert r3.exit_code == 0


def test_diff_scenario_filter_and_bad_threshold():
    r = invoke("diff", EXAMPLES / "foosh-serverless", EXAMPLES / "foosh-serverless", "-o", "json",
               "--scenario", "poll_status")
    assert r.exit_code == 0 and "no latency change" in r.output
    bad = invoke("diff", EXAMPLES / "classic-web", EXAMPLES / "classic-web-bad", "--fail-on-regression", "soon")
    assert bad.exit_code != 0


def test_view_prepares_the_directory_and_serves_briefly():
    r = invoke("view", EXAMPLES / "classic-web", "--no-open", "--duration", "0.3")
    assert r.exit_code == 0, r.output
    out = EXAMPLES / "classic-web" / ".iacsim"
    assert (out / "index.html").is_file() and (out / "report.json").is_file()
    assert re.search(r"viewer: http://127\.0\.0\.1:\d+/", r.output)
    assert "<title>iacsim viewer</title>" in (out / "index.html").read_text()


def test_view_runs_the_pipeline_when_report_is_missing(tmp_path):
    for f in (EXAMPLES / "classic-web").glob("*"):
        if f.is_file():
            (tmp_path / f.name).write_text(f.read_text().replace('"../modules/', f'"{EXAMPLES / "modules"}/'))
    r = invoke("view", tmp_path, "--no-open", "--duration", "0.2")
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".iacsim" / "report.json").is_file() and (tmp_path / ".iacsim" / "graph.json").is_file()


def test_plugins_lists_every_extension_point():
    r = invoke("plugins")
    assert r.exit_code == 0
    for line in ("parsers", "walkers", "analyzers", "reporters", "metric sources"):
        assert line in r.output
    assert "monte_carlo" in r.output and "tail_risk" in r.output


FIXTURE = EXAMPLES / "foosh-serverless" / "calibrate-fixture.yaml"


def test_calibrate_writes_a_profile_and_reports_coverage(tmp_path):
    # source + fixture come from examples/foosh-serverless/iacsim.yaml — no flags needed,
    # exactly how a team would point at their own monitoring
    out = tmp_path / "measured.yaml"
    r = invoke("calibrate", EXAMPLES / "foosh-serverless", "--out", out)
    assert r.exit_code == 0, r.output
    assert "calibrated 26 node(s)" in r.output and "skipped 2 node(s)" in r.output
    assert "no data in window" in r.output and "router" in r.output
    assert "(cold, cold_prob from defaults)" in r.output          # the partial text-input entry
    assert "next: iacsim run" in r.output and out.is_file()
    text = out.read_text()
    assert text.startswith("# generated by iacsim calibrate") and "by_label:" in text
    assert "distance:" not in text and "defaults:" not in text    # an overlay, not a full profile
    dry = invoke("calibrate", EXAMPLES / "foosh-serverless", "--out", tmp_path / "nope.yaml", "--dry-run")
    assert dry.exit_code == 0 and "dry run" in dry.output and not (tmp_path / "nope.yaml").exists()


def _measured_profile(tmp_path):
    """A calibrated profile for foosh, produced through the real CLI."""
    out = tmp_path / "measured.yaml"
    r = invoke("calibrate", EXAMPLES / "foosh-serverless", "--out", out)
    assert r.exit_code == 0, r.output
    return out


def _report(target):
    return json.loads((EXAMPLES / target / ".iacsim" / "report.json").read_text())


def _scenario(doc, name):
    return next(s for s in doc["scenarios"] if s["name"] == name)


def test_run_with_calibrated_profile_shows_the_rung_and_measured_numbers(tmp_path):
    measured = _measured_profile(tmp_path)
    assert invoke("run", EXAMPLES / "foosh-serverless", "-o", "json").exit_code == 0
    before = _scenario(_report("foosh-serverless"), "run_workflow_3_nodes")
    r = invoke("run", EXAMPLES / "foosh-serverless", "-o", "json", "--profile", measured)
    assert r.exit_code == 0, r.output
    doc = _report("foosh-serverless")
    assert doc["profile"]["sources"] == ["defaults", f"{measured} (fake, 7d)"]
    after = _scenario(doc, "run_workflow_3_nodes")
    image_hop = next(h for h in after["hops"] if "image-generation" in h["dst"] or "image_generation" in h["dst"])
    assert image_hop["latency_ms"] > 15_000
    assert after["total_ms"] > before["total_ms"] + 15_000


def test_calibrated_cold_starts_widen_the_tail(tmp_path):
    """api Lambda: cold 1200 @ 8 % measured vs 400 @ 5 % default → a wider p99 − p50
    on start_workflow (the parallel image branch dominates run_workflow_3_nodes)."""
    measured = _measured_profile(tmp_path)
    mc = ["run", EXAMPLES / "foosh-serverless", "-o", "json", "--walker", "monte_carlo",
          "--samples", "500", "--seed", "1"]
    assert invoke(*mc).exit_code == 0
    base = _scenario(_report("foosh-serverless"), "start_workflow")["percentiles"]
    assert invoke(*mc, "--profile", measured).exit_code == 0
    cal = _scenario(_report("foosh-serverless"), "start_workflow")["percentiles"]
    assert cal["p99"] - cal["p50"] > base["p99"] - base["p50"]


def test_calibrated_profile_serves_the_cloudformation_graph_via_by_label(tmp_path):
    measured = _measured_profile(tmp_path)
    r = invoke("run", EXAMPLES / "foosh-cfn", "-o", "json", "--profile", measured)
    assert r.exit_code == 0, r.output
    doc = _report("foosh-cfn")
    hops = [h for s in doc["scenarios"] for h in s["hops"]]
    api_hops = [h for h in hops if "ApiLambda" in h["dst"]]
    assert api_hops and all(h["breakdown"].get("processing") == 60 for h in api_hops)


def test_calibrate_rejects_unknown_source_and_reports_missing_boto3(monkeypatch):
    r = invoke("calibrate", EXAMPLES / "classic-web", "--source", "nope")
    assert r.exit_code != 0 and "fake" in r.output and "cloudwatch" in r.output
    monkeypatch.setitem(sys.modules, "boto3", None)      # simulate `pip install` without [calibrate]
    r = invoke("calibrate", EXAMPLES / "classic-web", "--source", "cloudwatch")
    assert r.exit_code == 3 and "boto3" in r.output


def test_calibrate_with_nothing_measurable_exits_1(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("lambda: {}\n")
    (tmp_path / "iacsim.yaml").write_text(f"calibrate:\n  sources:\n    fake:\n      fixture: {empty}\n")
    for f in (EXAMPLES / "classic-web").glob("*.tf"):
        (tmp_path / f.name).write_text(f.read_text().replace('"../modules/', f'"{EXAMPLES / "modules"}/'))
    r = invoke("calibrate", tmp_path, "--source", "fake", "--out", tmp_path / "x.yaml")
    assert r.exit_code == 1 and "nothing calibrated" in r.output


@pytest.mark.parametrize("cmd", [[], ["--help"], ["run", "--help"]])
def test_help(cmd):
    r = invoke(*cmd)
    assert "Infrastructure-as-Code" in r.output or "Usage" in r.output


# ---------------------------------------------------------------- M8: --walker load

def test_run_load_walker_writes_capacity():
    import json
    target = EXAMPLES / "foosh-serverless"          # modules live at ../modules, so run in place
    r = invoke("run", target, "--walker", "load", "--profile", target / "calibrated.yaml", "-o", "json", "-o", "text")
    assert r.exit_code == 0, r.output
    assert "users until it breaks" in r.output and "first to break" in r.output
    doc = json.loads((target / ".iacsim" / "report.json").read_text())
    util_max = doc["capacity"]["utilisation"]["10000"]                 # linear in users: hottest breaks first
    assert doc["capacity"]["first_to_break"]["resource"] == max(util_max, key=util_max.get)
    assert doc["scenarios"][0]["load"]["users"] == [100, 500, 1000, 2000, 5000, 10000]
    assert "Infinity" not in json.dumps(doc)


def test_run_load_walker_without_load_file_is_a_clean_error():
    r = invoke("run", EXAMPLES / "classic-web", "--walker", "load")
    assert r.exit_code == 2 and "needs a load profile" in r.output


def test_run_load_walker_with_a_bad_load_file_exits_2(tmp_path):
    bad = tmp_path / "load.yaml"
    bad.write_text("per_user: {nope: {every: 1s}}\n")
    r = invoke("run", EXAMPLES / "classic-web", "--walker", "load", "--load", bad)
    assert r.exit_code == 2 and "unknown scenario" in r.output
