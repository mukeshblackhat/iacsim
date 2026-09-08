"""examples/dashboard/ is real output and cannot go stale: every file there is
regenerated here and must equal the committed copy, ignoring the timestamp and
the absolute `before` / `after` paths a diff records. `make dashboard-sample`
refreshes the folder when a change to the output is intended."""

import json
from pathlib import Path

import pytest
from conftest import example_copy
from typer.testing import CliRunner

from iacsim.cli import app

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "dashboard"
VOLATILE = ("generated_at", "before", "after")


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    root = tmp_path_factory.mktemp("dashboard")
    ex = {name: example_copy(name, root) for name in ("gcp-web", "foosh-serverless", "classic-web", "classic-web-bad")}
    runner = CliRunner()
    for args in (["run", ex["gcp-web"], "-o", "json", "-o", "html"],
                 ["run", ex["foosh-serverless"], "--walker", "load", "-o", "json", "-o", "html"],
                 ["diff", ex["classic-web"], ex["classic-web-bad"], "-o", "json", "-o", "html"]):
        r = runner.invoke(app, [str(a) for a in args])
        assert r.exit_code == 0, r.output
    return {"gcp-web/report": ex["gcp-web"] / ".iacsim" / "report",
            "foosh-load/report": ex["foosh-serverless"] / ".iacsim" / "report",
            "classic-web-diff/diff": ex["classic-web-bad"] / ".iacsim" / "diff"}


def _stable(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in VOLATILE}


def _page(text: str) -> tuple[str, dict]:
    """(the html around the blob, the blob with the volatile keys dropped)."""
    head, rest = text.split('id="iacsim-data">', 1)
    blob, tail = rest.split("</script>", 1)
    doc = json.loads(blob)
    doc[doc["kind"]] = _stable(doc[doc["kind"]])
    return head + tail, doc


@pytest.mark.parametrize("stem", ["gcp-web/report", "foosh-load/report", "classic-web-diff/diff"])
def test_sample_matches_a_fresh_run(regenerated, stem):
    fresh = regenerated[stem]
    committed = SAMPLE / stem
    assert committed.with_suffix(".json").is_file() and committed.with_suffix(".html").is_file(), \
        f"{committed}: missing — run `make dashboard-sample`"
    assert _stable(json.loads(fresh.with_suffix(".json").read_text())) == \
        _stable(json.loads(committed.with_suffix(".json").read_text()))
    assert _page(fresh.with_suffix(".html").read_text()) == _page(committed.with_suffix(".html").read_text())


def test_sample_readme_names_every_folder():
    readme = (SAMPLE / "README.md").read_text()
    for folder in ("gcp-web", "foosh-load", "classic-web-diff"):
        assert folder in readme and (SAMPLE / folder).is_dir()
