"""`iacsim` command line.

    iacsim run   ./infra [--profile p.yaml] [--walker monte_carlo] [--format terraform]
    iacsim graph ./infra                 dump graph.json only (M1 milestone check)
    iacsim diff  ./before ./after        compare two snapshots
    iacsim validate ./infra              parse + normalise + check scenarios.yaml, no simulation
    iacsim calibrate --out p.yaml        (M7) CloudWatch → profile
    iacsim plugins                       list every registered implementation
"""

from __future__ import annotations

from pathlib import Path

import typer

from iacsim.core.config import load_config
from iacsim.core.interfaces import (
    ANALYZERS, COST_RULES, INFERENCE_RULES, METRIC_SOURCES, NORMALISERS, PARSERS,
    PROFILE_SOURCES, REPORTERS, SCENARIO_SOURCES, WALKERS,
)
from iacsim.core.registry import load_builtin_plugins, load_external_plugins

app = typer.Typer(help="Infrastructure-as-Code → latency simulation", no_args_is_help=True)


def _bootstrap(target: Path) -> None:
    load_builtin_plugins()
    load_external_plugins(target / "plugins")
    load_external_plugins(Path.cwd() / "plugins")


def _write_outputs(output, cfg, target: Path) -> None:
    out_dir = target / cfg.get("report.out_dir")
    out_dir.mkdir(exist_ok=True)
    for name in cfg.get("report.outputs"):
        rendered = REPORTERS.get(name)().render(output.findings, output.graph)
        if name == "text":
            typer.echo(rendered)
        else:
            (out_dir / f"report.{name}").write_text(rendered)
            typer.echo(f"wrote {out_dir / f'report.{name}'}")


@app.command()
def run(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    profile: list[str] = typer.Option(None, "--profile", "-p", help="latency profile(s), stackable"),
    walker: str = typer.Option(None, help="expected_value | monte_carlo"),
    samples: int = typer.Option(None, help="monte_carlo sample count"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
) -> None:
    """Simulate every scenario and print the bottleneck report."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(target, {
        "latency.profiles": ["defaults", *profile] if profile else None,
        "simulation.walker": walker, "simulation.samples": samples, "format": fmt,
    })
    _write_outputs(pipeline.run(target, cfg), cfg, target)


@app.command()
def graph(
    target: Path = typer.Argument(..., exists=True),
    fmt: str = typer.Option(None, "--format"),
) -> None:
    """Parse + normalise + infer edges; write graph.json. No simulation."""
    import json
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(target, {"format": fmt})
    g, _ = pipeline.build_graph(target, cfg)
    out = target / cfg.get("report.out_dir")
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(json.dumps(g.to_dict(), indent=2, default=str))
    typer.echo(f"{len(g.nodes)} nodes, {len(g.edges)} edges → {out / 'graph.json'}")
    for w in g.warnings:
        typer.echo(f"warning: {w}", err=True)


@app.command()
def diff(
    before: Path = typer.Argument(..., exists=True),
    after: Path = typer.Argument(..., exists=True),
    profile: list[str] = typer.Option(None, "--profile", "-p"),
) -> None:
    """Run both snapshots, align scenarios by name, print per-hop deltas."""
    from iacsim.differ import diff_targets
    _bootstrap(before)
    overrides = {"latency.profiles": ["defaults", *profile] if profile else None}
    typer.echo(diff_targets(before, load_config(before, overrides), after, load_config(after, overrides)))


@app.command()
def validate(target: Path = typer.Argument(..., exists=True)) -> None:
    """Parse, normalise, and check scenarios.yaml — exit non-zero on problems."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(target)
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
