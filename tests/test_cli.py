"""Every CLI command through typer's CliRunner against the real examples."""

import json
import re
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


def test_calibrate_is_an_honest_stub():
    r = invoke("calibrate", "--out", "x.yaml")
    assert r.exit_code == 1 and "M7" in r.output
    assert invoke("calibrate", "--source", "nope").exit_code != 0


@pytest.mark.parametrize("cmd", [[], ["--help"], ["run", "--help"]])
def test_help(cmd):
    r = invoke(*cmd)
    assert "Infrastructure-as-Code" in r.output or "Usage" in r.output
