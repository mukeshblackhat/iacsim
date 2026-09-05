"""examples/iacsim.yaml claims to be the full default set — prove it stays that way."""

from pathlib import Path

import yaml

from iacsim.core.config import DEFAULTS

ROOT = Path(__file__).resolve().parent.parent


def test_examples_iacsim_yaml_matches_defaults():
    doc = yaml.safe_load((ROOT / "examples" / "iacsim.yaml").read_text())
    assert doc == DEFAULTS
