"""Writing reports to disk — shared by the CLI and the viewer so both produce
the same files the same way (`report.json`, `graph.json`, `report.md`, `report.html`, …)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from iacsim.core.interfaces import REPORTERS, Reporter, ReporterOptions
from iacsim.core.models import InfraGraph

REPORT_EXTENSIONS = {"json": "json", "markdown": "md", "text": "txt", "html": "html"}


def write_reports(outputs: list[str], render: Callable[[Reporter], str], stem: str, out_dir: Path,
                  options: ReporterOptions | None = None) -> tuple[str | None, list[Path]]:
    """Render every reporter in `outputs`. The text reporter's output is returned
    (the CLI prints it); every other reporter writes `<out_dir>/<stem>.<ext>`.
    Returns (text, written paths)."""
    options = options or ReporterOptions()
    text, written = None, []
    for name in outputs:
        rendered = render(REPORTERS.get(name)(options))
        if name == "text":
            text = rendered
        else:
            path = out_dir / f"{stem}.{REPORT_EXTENSIONS.get(name, name)}"
            path.write_text(rendered, encoding="utf-8")
            written.append(path)
    return text, written


def write_graph(graph: InfraGraph, out_dir: Path) -> Path:
    """graph.json — the viewer's fallback input and `iacsim graph`'s output."""
    path = out_dir / "graph.json"
    path.write_text(json.dumps(graph.to_dict(), indent=2, default=str, allow_nan=False))
    return path
