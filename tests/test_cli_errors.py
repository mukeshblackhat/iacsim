"""WP4: every failure is one line on stderr with a documented exit code — no
tracebacks — and relative paths resolve against the target directory first."""

import json
import re
import socket

from conftest import EXAMPLES, example_copy
from typer.testing import CliRunner

from iacsim.cli import app

runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_unknown_scenario_node_is_one_line_with_a_hint(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    (copy / "scenarios.yaml").write_text(
        "typo:\n  entry: module.load_balancer.aws_lb.this\n  steps:\n    - module.database.aws_db_instance.thiss\n")
    r = invoke("run", copy, "-o", "json")
    assert r.exit_code == 2, r.output
    assert "did you mean" in r.output and "run:" in r.output
    assert "Traceback" not in r.output


def test_missing_scenario_entry_is_exit_2(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    (copy / "scenarios.yaml").write_text("broken:\n  steps:\n    - module.database.aws_db_instance.this\n")
    r = invoke("validate", copy)
    assert r.exit_code == 2 and "missing 'entry'" in r.output and "Traceback" not in r.output


def test_missing_profile_names_both_places(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    r = invoke("run", copy, "-o", "json", "--profile", "nope.yaml")
    assert r.exit_code == 2, r.output
    assert "nope.yaml: not found in" in r.output and str(copy) in r.output and "Traceback" not in r.output


def test_unknown_walker_lists_the_available_ones(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    r = invoke("run", copy, "-o", "json", "--walker", "nope")
    assert r.exit_code == 2 and "expected_value" in r.output and "monte_carlo" in r.output
    assert "Traceback" not in r.output


def test_bad_regression_threshold_is_exit_2(tmp_path):
    a, b = example_copy("classic-web", tmp_path / "a"), example_copy("classic-web-bad", tmp_path / "b")
    r = invoke("diff", a, b, "-o", "json", "--fail-on-regression", "5x")
    assert r.exit_code == 2 and "--fail-on-regression expects" in r.output and "Traceback" not in r.output


def test_unknown_format_is_exit_2(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    r = invoke("graph", copy, "--format", "pulumi")
    assert r.exit_code == 2 and "pulumi" in r.output and "Traceback" not in r.output


def test_version():
    r = invoke("--version")
    assert r.exit_code == 0 and re.fullmatch(r"iacsim \d+\.\d+\.\d+\n", r.output)


def test_run_scenario_filter_and_unknown_name(tmp_path):
    copy = example_copy("foosh-serverless", tmp_path)
    r = invoke("run", copy, "-o", "json", "--scenario", "poll_status", "--scenario", "save_workflow")
    assert r.exit_code == 0, r.output
    doc = json.loads((copy / ".iacsim" / "report.json").read_text())
    assert [s["name"] for s in doc["scenarios"]] == ["poll_status", "save_workflow"]
    bad = invoke("run", copy, "-o", "json", "--scenario", "pol_status")
    assert bad.exit_code == 2 and "did you mean 'poll_status'" in bad.output and "Traceback" not in bad.output


def test_validate_strict_only_fails_on_warnings(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    # an unknown resource type is a benign graph warning
    (copy / "extra.tf").write_text('resource "aws_sagemaker_endpoint" "x" {\n  name = "x"\n}\n')
    lax = invoke("validate", copy)
    assert lax.exit_code == 0, lax.output
    assert "warning:" in lax.output
    strict = invoke("validate", copy, "--strict")
    assert strict.exit_code == 1
    # an unwired scenario step fails either way
    (copy / "scenarios.yaml").write_text(
        "odd:\n  entry: module.load_balancer.aws_lb.this\n  steps:\n    - module.network.aws_vpc.this\n")
    assert invoke("validate", copy).exit_code == 1


def test_view_busy_port_is_exit_2_and_url_is_printed_before_serving(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        r = invoke("view", copy, "--no-open", "--port", busy, "--duration", "0.1")
        assert r.exit_code == 2 and f"port {busy} in use" in r.output and "Traceback" not in r.output
    ok = invoke("view", copy, "--no-open", "--duration", "0.2")
    assert ok.exit_code == 0, ok.output
    assert ok.output.index("viewer: http://127.0.0.1:") < ok.output.index("press Ctrl-C")


def test_calibrate_then_relative_profile_resolves_against_the_target(tmp_path):
    copy = example_copy("foosh-serverless", tmp_path)
    r = invoke("calibrate", copy)
    assert r.exit_code == 0, r.output
    assert (copy / "calibrated.yaml").is_file()
    assert "--profile calibrated.yaml" in r.output          # the printed next step is what we now type
    run = invoke("run", copy, "-o", "json", "--profile", "calibrated.yaml")
    assert run.exit_code == 0, run.output
    doc = json.loads((copy / ".iacsim" / "report.json").read_text())
    assert doc["profile"]["sources"][1].endswith("calibrated.yaml (fake, 7d)")
    out = invoke("calibrate", copy, "--out", "measured/x.yaml")
    assert out.exit_code == 0 and (copy / "measured" / "x.yaml").is_file()


def test_diff_on_template_files_writes_next_to_the_after_file(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    for d in (before, after):
        d.mkdir()
        (d / "template.json").write_text((EXAMPLES / "foosh-cfn" / "template.json").read_text())
    r = invoke("diff", before / "template.json", after / "template.json", "-o", "json")
    assert r.exit_code == 0, r.output
    assert (after / ".iacsim" / "diff.json").is_file() and "no latency change" in r.output


def test_report_out_dir_is_created_with_parents(tmp_path):
    copy = example_copy("classic-web", tmp_path)
    (copy / "iacsim.yaml").write_text("report:\n  out_dir: out/deep/dir\n")
    r = invoke("run", copy, "-o", "json")
    assert r.exit_code == 0, r.output
    assert (copy / "out" / "deep" / "dir" / "report.json").is_file()
