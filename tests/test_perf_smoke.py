"""Runtime guard-rails (WP7). Thresholds are deliberately generous — several
times the measured numbers — so they catch a regression to the old O(E²) / re-parse
behaviour without flaking on a slow CI box. Also proves the caches change no output:
foosh's report.json (timestamp stripped) hashes to the value recorded in
tests/fixtures/foosh_report_sha256.txt."""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import example_copy

from iacsim.core.config import load_config
from iacsim.core.pipeline import build_graph

ROOT = Path(__file__).resolve().parent.parent
FOOSH = ROOT / "examples" / "foosh-serverless"
IACSIM = [sys.executable, "-m", "iacsim.cli"]


@pytest.fixture(scope="module")
def foosh_copy(tmp_path_factory) -> Path:
    """The CLI writes .iacsim/ next to its target, so subprocess runs use a throwaway copy."""
    return example_copy("foosh-serverless", tmp_path_factory.mktemp("perf"))


def _ms(fn) -> float:
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def test_second_parse_of_the_same_stack_is_cached():
    cfg = load_config(FOOSH)
    build_graph(FOOSH, cfg)                       # warm: parses every file once
    assert _ms(lambda: build_graph(FOOSH, cfg)) < 50      # measured ~6 ms; was ~137 ms before the cache


def test_run_and_plugins_wall_time_are_bounded(foosh_copy):
    t = time.perf_counter()
    r = subprocess.run([*IACSIM, "run", str(foosh_copy), "-o", "json"], capture_output=True, cwd=ROOT, check=False)
    assert r.returncode == 0, r.stderr
    assert time.perf_counter() - t < 1.5                   # measured ~0.16 s
    t = time.perf_counter()
    r = subprocess.run([*IACSIM, "plugins"], capture_output=True, cwd=ROOT, check=False)
    assert r.returncode == 0, r.stderr
    assert time.perf_counter() - t < 0.5                   # measured ~0.06 s


def test_caches_change_no_output(foosh_copy):
    """The default run's report.json (minus generated_at) is byte-identical to the
    recorded hash. Update the fixture only when a change to the *contract* is
    intended (WP8 regenerated it once: the never-set `Latency.p50/p99/distribution`
    fields, serialised as null on every edge, were removed — nothing else moved)."""
    r = subprocess.run([*IACSIM, "run", str(foosh_copy), "-o", "json"], capture_output=True, cwd=ROOT, check=False)
    assert r.returncode == 0, r.stderr
    doc = json.loads((foosh_copy / ".iacsim" / "report.json").read_text())
    doc.pop("generated_at", None)
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
    expected = (ROOT / "tests" / "fixtures" / "foosh_report_sha256.txt").read_text().strip()
    assert digest == expected
