"""`iacsim` command line.

    iacsim run   ./infra [--profile p.yaml] [--walker monte_carlo] [--format terraform]
                         [--all-hops] [-o text -o json -o markdown]
    iacsim graph ./infra                 dump graph.json only (M1 milestone check)
    iacsim diff  ./before ./after        compare two snapshots
                 [--fail-on-regression 50ms|10%] [--scenario NAME] [--align-by id|label]
                 [-o text -o json -o markdown]   exit 2 when a total grows past the threshold
    iacsim validate ./infra              parse + normalise + check scenarios.yaml, no simulation
    iacsim calibrate --out p.yaml        (M7) CloudWatch → profile
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
        "simulation.walker": walker, "simulation.samples": samples, "format": fmt,
        "parsers.cloudformation.region": region,
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
    """Parse, normalise, and check scenarios.yaml — exit non-zero on problems."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target))
    g, _ = pipeline.build_graph(target, cfg)
    scenarios = pipeline.load_scenarios(g, target, cfg)
    typer.echo(f"ok: {len(g.nodes)} nodes, {len(g.edges)} edges, {len(scenarios)} scenarios")
    raise typer.Exit(code=1 if g.warnings else 0)


@app.command()
def calibrate(
    out: Path = typer.Option(Path("calibrated.yaml"), "--out"),
    source: str = typer.Option("cloudwatch"),
    window: str = typer.Option("7d"),
) -> None:
    """(M7) Pull real numbers and write a profile in the defaults.yaml schema."""
    _bootstrap(Path.cwd())
    METRIC_SOURCES.get(source)  # fail early if unknown
    raise typer.Exit("calibrate: not implemented yet (M7)")


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
