"""`iacsim diff` — run the pipeline twice, align scenarios by name, report deltas.  [M4]"""

from __future__ import annotations

from pathlib import Path

from iacsim.core.config import Config


def diff_targets(before: Path, before_cfg: Config, after: Path, after_cfg: Config) -> str:
    raise NotImplementedError("M4: diff")
