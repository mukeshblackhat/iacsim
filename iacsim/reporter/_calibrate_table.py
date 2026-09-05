"""The `iacsim calibrate` coverage table — rich rendering lives with the other
reporters, not in the CLI."""

from __future__ import annotations

import io

from rich.console import Console
from rich.table import Table

from iacsim.core.interfaces import MetricSource
from iacsim.core.models import InfraGraph

WIDTH = 110


def coverage_table(result, graph: InfraGraph) -> str:
    """Covered nodes with the keys measured, skipped nodes with the reason, and
    how many measurable nodes stay on defaults.yaml."""
    console = Console(file=io.StringIO(), force_terminal=False, width=WIDTH)
    covered = Table(title=f"calibrated {len(result.covered)} node(s) — source={result.meta['source']}, "
                          f"window={result.meta['window']}", show_lines=False)
    for column in ("node", "kind", "measured"):
        covered.add_column(column)
    for node_id in result.covered:
        node = graph.nodes[node_id]
        keys = ", ".join(f"{k}={v}" for k, v in result.measured[node_id].items())
        if result.filled.get(node_id):
            keys += f"  ({', '.join(result.filled[node_id])} from defaults)"
        covered.add_row(node.label or node_id, node.subtype, keys)
    console.print(covered)
    if result.skipped:
        skipped = Table(title=f"skipped {len(result.skipped)} node(s) — defaults kept")
        for column in ("node", "kind", "reason"):
            skipped.add_column(column)
        for node_id, reason in result.skipped:
            node = graph.nodes[node_id]
            skipped.add_row(node.label or node_id, node.subtype, reason)
        console.print(skipped)
    measurable = sum(1 for n in graph.nodes.values() if n.subtype in MetricSource.KINDS)
    console.print(f"{measurable - len(result.covered)} of {measurable} measurable node(s) stay on defaults.yaml")
    return console.file.getvalue()
