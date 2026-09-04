"""Pick a parser for a path: forced by --format, otherwise the first whose
`detect()` says yes. Order matters only if two formats coexist in one folder."""

from __future__ import annotations

from pathlib import Path

from iacsim.core.interfaces import PARSERS, Parser

DETECTION_ORDER = ["terraform", "cloudformation"]


def detect_parser(path: Path, forced: str | None = None) -> type[Parser]:
    if forced:
        return PARSERS.get(forced)
    for name in DETECTION_ORDER:
        if name in PARSERS and PARSERS.get(name).detect(path):
            return PARSERS.get(name)
    raise ValueError(
        f"no parser recognised {path}. Expected .tf files or a CloudFormation template; "
        f"use --format to force one of: {', '.join(PARSERS.names())}"
    )
