"""`iacsim` command line.

    iacsim run   ./infra [--profile p.yaml] [--walker monte_carlo --samples N --seed S]
                         [--format terraform] [--all-hops] [-o text -o json -o markdown]
    iacsim graph ./infra                 dump graph.json only (M1 milestone check)
    iacsim diff  ./before ./after        compare two snapshots
                 [--fail-on-regression 50ms|10%] [--scenario NAME] [--align-by id|label]
                 [-o text -o json -o markdown]   exit 2 when a total grows past the threshold
    iacsim validate ./infra              parse + normalise + check scenarios.yaml, no simulation
    iacsim view  ./infra [--port N] [--no-open]   serve the graph viewer for .iacsim/report.json
    iacsim calibrate ./infra [--source cloudwatch|fake] [--window 7d] [--out measured.yaml]
                             [--region R] [--dry-run]   measured numbers → profile YAML (rung 2)
    iacsim plugins                       list every registered implementation
"""

from __future__ import annotations

from pathlib import Path

import typer

from iacsim.core.config import load_config
from iacsim.core.interfaces import (
    ANALYZERS,
    COST_RULES,
    INFERENCE_RULES,
    METRIC_SOURCES,
    NORMALISERS,
    PARSERS,
    PROFILE_SOURCES,
    REPORTERS,
    SCENARIO_SOURCES,
    WALKERS,
)
from iacsim.core.registry import load_builtin_plugins, load_external_plugins

app = typer.Typer(help="Infrastructure-as-Code → latency simulation", no_args_is_help=True)


def _base_dir(target: Path) -> Path:
    """Config, scenarios and outputs live next to a template file, or in the target dir."""
    return target if target.is_dir() else target.parent


def _bootstrap(target: Path) -> None:
    load_builtin_plugins()
    load_external_plugins(target / "plugins")
    load_external_plugins(Path.cwd() / "plugins")


REPORT_EXTENSIONS = {"json": "json", "markdown": "md", "text": "txt"}


def _write_outputs(output, cfg, target: Path, all_hops: bool = False) -> None:
    """Text goes to stdout (coloured on a TTY); every other reporter writes
    <out_dir>/report.<ext>."""
    import sys
    out_dir = _base_dir(target) / cfg.get("report.out_dir")
    out_dir.mkdir(exist_ok=True)
    options = {"top_n": cfg.get("analysis.top_n"), "all_hops": all_hops}
    for name in cfg.get("report.outputs"):
        reporter = REPORTERS.get(name)(**(options | {"color": sys.stdout.isatty()} if name == "text" else options))
        rendered = reporter.render(output.findings, output.graph)
        if name == "text":
            typer.echo(rendered, nl=False)
        else:
            path = out_dir / f"report.{REPORT_EXTENSIONS.get(name, name)}"
            path.write_text(rendered)
            typer.echo(f"wrote {path}")


@app.command()
def run(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    profile: list[str] = typer.Option(None, "--profile", "-p", help="latency profile(s), stackable"),
    walker: str = typer.Option(None, help="expected_value | monte_carlo"),
    samples: int = typer.Option(None, help="monte_carlo sample count"),
    seed: int = typer.Option(None, help="monte_carlo random seed (reproducible runs)"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
    all_hops: bool = typer.Option(False, "--all-hops", help="show every hop in path order, not just the top-N"),
    output: list[str] = typer.Option(None, "--output", "-o", help="reporters: text | json | markdown (repeatable)"),
    region: str = typer.Option(None, help="region for CloudFormation templates (no provider block)"),
) -> None:
    """Simulate every scenario and print the bottleneck report."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {
        "latency.profiles": ["defaults", *profile] if profile else None,
        "simulation.walker": walker, "simulation.samples": samples, "simulation.seed": seed,
        "format": fmt, "parsers.cloudformation.region": region,
        "report.outputs": output or None,
    })
    _write_outputs(pipeline.run(target, cfg), cfg, target, all_hops=all_hops)


@app.command()
def graph(
    target: Path = typer.Argument(..., exists=True),
    fmt: str = typer.Option(None, "--format"),
    region: str = typer.Option(None, help="region for CloudFormation templates"),
) -> None:
    """Parse + normalise + infer edges; write graph.json. No simulation."""
    import json

    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {"format": fmt, "parsers.cloudformation.region": region})
    g, _ = pipeline.build_graph(target, cfg)
    out = _base_dir(target) / cfg.get("report.out_dir")
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(json.dumps(g.to_dict(), indent=2, default=str))
    typer.echo(f"{len(g.nodes)} nodes, {len(g.edges)} edges → {out / 'graph.json'}")
    for w in g.warnings:
        typer.echo(f"warning: {w}", err=True)


@app.command()
def diff(
    before: Path = typer.Argument(..., exists=True),
    after: Path = typer.Argument(..., exists=True),
    profile: list[str] = typer.Option(None, "--profile", "-p", help="latency profile(s), applied to both sides"),
    scenario: str = typer.Option(None, "--scenario", help="compare only this scenario"),
    align_by: str = typer.Option("id", "--align-by", help="match nodes by id (same format) or label (Terraform vs CloudFormation)"),
    fail_on_regression: str = typer.Option(None, "--fail-on-regression", help="exit 2 if any total grows more than e.g. 50ms or 10%"),
    output: list[str] = typer.Option(None, "--output", "-o", help="reporters: text | json | markdown (repeatable)"),
) -> None:
    """Run both snapshots with the same profile and report what changed:
    moved/added resources, per-category shift, changed hops, recommendations."""
    import sys

    from iacsim.differ import parse_threshold, run_diff, summarise
    _bootstrap(before)
    threshold = parse_threshold(fail_on_regression) if fail_on_regression else None
    overrides = {"latency.profiles": ["defaults", *profile] if profile else None,
                 "report.outputs": output or None}
    cfg_before, cfg_after = load_config(_base_dir(before), overrides), load_config(_base_dir(after), overrides)
    report, g_before, g_after = run_diff(before, cfg_before, after, cfg_after,
                                         scenario=scenario, align_by=align_by)

    out_dir = after / cfg_after.get("report.out_dir")
    out_dir.mkdir(exist_ok=True)
    for name in cfg_after.get("report.outputs"):
        reporter = REPORTERS.get(name)(**({"color": sys.stdout.isatty()} if name == "text" else {}))
        rendered = reporter.render_diff(report, g_before, g_after)
        if name == "text":
            typer.echo(rendered, nl=False)
        else:
            path = out_dir / f"diff.{REPORT_EXTENSIONS.get(name, name)}"
            path.write_text(rendered)
            typer.echo(f"wrote {path}")

    typer.echo(summarise(report))
    if threshold:
        regressed = report.regressions(threshold)
        if regressed:
            names = ", ".join(f"{s.name} ({s.delta_ms:+,.1f} ms)" for s in regressed)
            typer.echo(f"REGRESSION past {fail_on_regression}: {names}", err=True)
            raise typer.Exit(code=2)


@app.command()
def validate(target: Path = typer.Argument(..., exists=True)) -> None:
    """Parse, normalise, and check scenarios.yaml — exit non-zero on problems.
    Also warns about scenario steps that name a node no edge touches (a typo,
    or a resource nothing is wired to) and prints the latency profile rungs."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target))
    g, _ = pipeline.build_graph(target, cfg)
    scenarios = pipeline.load_scenarios(g, target, cfg)
    profile = pipeline.load_profile(cfg)
    problems = list(g.warnings) + _unwired_steps(g, scenarios)
    typer.echo(f"ok: {len(g.nodes)} nodes, {len(g.edges)} edges, {len(scenarios)} scenarios")
    typer.echo(f"profile: {' → '.join(profile.sources)}")
    for w in problems:
        typer.echo(f"warning: {w}", err=True)
    raise typer.Exit(code=1 if problems else 0)


def _unwired_steps(g, scenarios) -> list[str]:
    """Scenario steps naming nodes with no incident edge — the walker would price
    them as synthetic hops, which is usually not what the author meant."""
    touched = {e.src for e in g.edges} | {e.dst for e in g.edges}
    warnings = []

    def walk(steps, scenario_name):
        for st in steps:
            for node in [st.node, st.fanout[0] if st.fanout else None]:
                if node and node not in touched:
                    warnings.append(f"scenario '{scenario_name}' step '{node}' has no inferred edges")
            for branch in st.parallel or []:
                walk(branch, scenario_name)

    for sc in scenarios:
        walk(sc.steps, sc.name)
    return warnings


@app.command()
def view(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    port: int = typer.Option(0, help="port to serve on (0 = pick a free one)"),
    no_open: bool = typer.Option(False, "--no-open", help="do not open a browser"),
    duration: float = typer.Option(None, help="serve for N seconds then stop (default: until Ctrl-C)"),
) -> None:
    """Open the graph viewer: runs the pipeline if .iacsim/report.json is
    missing, then serves .iacsim/ over HTTP and opens the browser."""
    from iacsim.viewer import prepare, serve
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {"report.outputs": ["json"]})
    out_dir = prepare(target, cfg)
    typer.echo(f"serving {out_dir} — press Ctrl-C to stop")
    url = serve(out_dir, port=port, open_browser=not no_open, duration=duration)
    typer.echo(f"viewer: {url}")


@app.command()
def calibrate(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    source: str = typer.Option(None, help="metric source: cloudwatch | fake | <plugin> (config: calibrate.source)"),
    window: str = typer.Option(None, help="lookback: 7d | 24h | 30m (config: calibrate.window)"),
    out: Path = typer.Option(None, "--out", help="profile to write (default: <target>/calibrated.yaml)"),
    region: str = typer.Option(None, help="AWS region for the metric source (default: each node's own)"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show coverage, write nothing"),
) -> None:
    """Replace guessed latency numbers with measured ones: query the configured
    metric source for every Lambda / table / LB / API in the graph and write a
    profile YAML (rung 2) to pass back as `--profile`. Exit 0 with skips,
    1 if nothing could be measured, 3 if the source is not set up."""
    from iacsim.core import pipeline
    from iacsim.core.registry import UnknownImplementation
    from iacsim.latency.calibrate import MetricSourceError, make_metric_source
    from iacsim.latency.calibrate.calibrator import calibrate as run_calibration
    from iacsim.latency.calibrate.writer import write_profile

    _bootstrap(target)
    base = _base_dir(target)
    cfg = load_config(base, {"calibrate.source": source, "calibrate.window": window,
                             "format": fmt, "parsers.cloudformation.region": region})
    name = cfg.get("calibrate.source")
    if region:
        cfg.set(f"calibrate.sources.{name}.region", region)

    try:
        metric_source = make_metric_source(cfg, base)
        graph, raw = pipeline.build_graph(target, cfg)
        result = run_calibration(graph, metric_source, cfg.get("calibrate.window"), fmt=raw.format)
    except UnknownImplementation:
        raise typer.BadParameter(
            f"unknown metric source '{name}'; available: {', '.join(METRIC_SOURCES.names())}") from None
    except MetricSourceError as e:
        typer.echo(f"calibrate: {e}", err=True)
        raise typer.Exit(code=3) from None

    typer.echo(_coverage_table(result, graph), nl=False)
    if not result.covered:
        typer.echo("nothing calibrated — check calibrate.source / --window; defaults unchanged", err=True)
        raise typer.Exit(code=1)
    if dry_run:
        typer.echo("dry run — nothing written")
        return
    path = write_profile(result, out if out else base / cfg.get("calibrate.out"))
    typer.echo(f"wrote {path}")
    typer.echo(f"next: iacsim run {target} --profile {path}")


def _coverage_table(result, graph) -> str:
    """Covered nodes with the keys measured, skipped nodes with the reason, and
    how many measurable nodes stay on defaults.yaml."""
    import io

    from rich.console import Console
    from rich.table import Table

    console = Console(file=io.StringIO(), force_terminal=False, width=110)
    covered = Table(title=f"calibrated {len(result.covered)} node(s) — source={result.meta['source']}, "
                          f"window={result.meta['window']}", show_lines=False)
    covered.add_column("node"); covered.add_column("kind"); covered.add_column("measured")
    for node_id in result.covered:
        node = graph.nodes[node_id]
        keys = ", ".join(f"{k}={v}" for k, v in result.measured[node_id].items())
        if result.filled.get(node_id):
            keys += f"  ({', '.join(result.filled[node_id])} from defaults)"
        covered.add_row(node.label or node_id, node.subtype, keys)
    console.print(covered)
    if result.skipped:
        skipped = Table(title=f"skipped {len(result.skipped)} node(s) — defaults kept")
        skipped.add_column("node"); skipped.add_column("kind"); skipped.add_column("reason")
        for node_id, reason in result.skipped:
            node = graph.nodes[node_id]
            skipped.add_row(node.label or node_id, node.subtype, reason)
        console.print(skipped)
    measurable = sum(1 for n in graph.nodes.values() if n.subtype in _MEASURABLE)
    console.print(f"{measurable - len(result.covered)} of {measurable} measurable node(s) stay on defaults.yaml")
    return console.file.getvalue()


_MEASURABLE = ("lambda", "dynamodb", "rds", "alb", "api_gateway", "step_functions")


@app.command()
def plugins() -> None:
    """List every registered implementation for every extension point."""
    _bootstrap(Path.cwd())
    for label, reg in [("parsers", PARSERS), ("normalisers", NORMALISERS),
                       ("inference rules", INFERENCE_RULES), ("scenario sources", SCENARIO_SOURCES),
                       ("profile sources", PROFILE_SOURCES), ("cost rules", COST_RULES),
                       ("walkers", WALKERS), ("analyzers", ANALYZERS), ("reporters", REPORTERS),
                       ("metric sources", METRIC_SOURCES)]:
        typer.echo(f"{label:18} {', '.join(reg.names())}")


if __name__ == "__main__":
    app()
