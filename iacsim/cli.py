"""`iacsim` command line.

    iacsim run   ./infra [--profile p.yaml] [--walker monte_carlo --samples N --seed S]
                         [--walker load --load load.yaml]   users-until-it-breaks (M8)
                         [--scenario NAME ...] [--format terraform] [--all-hops]
                         [-o text -o json -o markdown]
    iacsim graph ./infra                 dump graph.json only (M1 milestone check)
    iacsim diff  ./before ./after        compare two snapshots
                 [--fail-on-regression 50ms|10%] [--scenario NAME] [--align-by id|label]
                 [-o text -o json -o markdown]   exit 2 when a total grows past the threshold
    iacsim validate ./infra [--strict]   parse + normalise + check scenarios.yaml, no simulation
    iacsim view  ./infra [--port N] [--no-open]   serve the graph viewer for .iacsim/report.json
    iacsim calibrate ./infra [--source cloudwatch|fake] [--window 7d] [--out measured.yaml]
                             [--region R] [--dry-run]   measured numbers → profile YAML (rung 2)
    iacsim plugins                       list every registered implementation
    iacsim --version

Exit codes (every command):

    0   ok
    1   problems found — `validate`: a scenario step no edge touches, or any warning
        with --strict; `calibrate`: nothing could be measured
    2   input error — unknown node / implementation / file, a bad flag value — or
        `diff --fail-on-regression` tripped
    3   metric source unusable (`calibrate`: missing SDK, no credentials, access denied)

Relative paths given to --profile, --load and --out are resolved against the
target directory first, then the current directory. Every error is one line on
stderr prefixed with the command name; no tracebacks.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

import typer

import iacsim
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
from iacsim.core.registry import load_builtin_plugins, load_entry_points, load_plugins_dir

EXIT_OK, EXIT_PROBLEMS, EXIT_INPUT, EXIT_SOURCE = 0, 1, 2, 3

app = typer.Typer(help="Infrastructure-as-Code → latency simulation", no_args_is_help=True,
                  pretty_exceptions_enable=False)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"iacsim {iacsim.__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True,
                                 help="print the version and exit"),
) -> None:
    """Infrastructure-as-Code → latency simulation."""


# ---------------------------------------------------------------- shared helpers

def _base_dir(target: Path) -> Path:
    """Config, scenarios and outputs live next to a template file, or in the target dir."""
    return target if target.is_dir() else target.parent


def _bootstrap(target: Path) -> None:
    load_builtin_plugins()
    load_entry_points()                       # once per process
    load_plugins_dir(target / "plugins")
    if Path.cwd() != target:
        load_plugins_dir(Path.cwd() / "plugins")


def _resolve(target: Path, p: str | Path) -> Path:
    """Where a relative --profile / --load / --out lives: the target directory
    first (that is where `calibrate` writes and where scenarios.yaml sits), then
    the current directory. Absolute paths pass through untouched."""
    path = Path(p)
    if path.is_absolute():
        return path
    base = _base_dir(target)
    if (base / path).exists():
        return (base / path).resolve()
    if path.exists():
        return path.resolve()
    raise FileNotFoundError(f"{p}: not found in {base} or {Path.cwd()}")


def _out_dir(target: Path, cfg) -> Path:
    out = _base_dir(target) / cfg.get("report.out_dir")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _guard(command):
    """One error boundary for every command: an input problem is one line on
    stderr and exit 2, a metric source that cannot run is exit 3. The exception
    classes are raised where they always were; only the rendering lives here."""

    @functools.wraps(command)
    def wrapper(*args, **kwargs):
        from iacsim.core.registry import UnknownImplementation
        from iacsim.latency.calibrate import MetricSourceError
        try:
            return command(*args, **kwargs)
        except MetricSourceError as e:
            typer.echo(f"{command.__name__}: {e}", err=True)
            raise typer.Exit(code=EXIT_SOURCE) from None
        except UnknownImplementation as e:
            typer.echo(f"{command.__name__}: {e.args[0] if e.args else e}", err=True)
            raise typer.Exit(code=EXIT_INPUT) from None
        except (FileNotFoundError, ValueError) as e:      # LoadProfileError, UnknownNodeInScenario ⊂ ValueError
            typer.echo(f"{command.__name__}: {e}", err=True)
            raise typer.Exit(code=EXIT_INPUT) from None

    return wrapper


REPORT_EXTENSIONS = {"json": "json", "markdown": "md", "text": "txt"}


def _write_reports(cfg, render, stem: str, out_dir: Path, **options) -> None:
    """Text goes to stdout (coloured on a TTY); every other reporter writes
    <out_dir>/<stem>.<ext>. `render(reporter)` produces the string."""
    for name in cfg.get("report.outputs"):
        color = {"color": sys.stdout.isatty()} if name == "text" else {}
        rendered = render(REPORTERS.get(name)(**(options | color)))
        if name == "text":
            typer.echo(rendered, nl=False)
        else:
            path = out_dir / f"{stem}.{REPORT_EXTENSIONS.get(name, name)}"
            path.write_text(rendered)
            typer.echo(f"wrote {path}")


def _profiles(target: Path, profile: list[str] | None) -> list[str] | None:
    """`defaults` first, then each --profile resolved to an absolute path."""
    return ["defaults", *(str(_resolve(target, p)) for p in profile)] if profile else None


# ---------------------------------------------------------------- commands

@app.command()
@_guard
def run(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    profile: list[str] = typer.Option(None, "--profile", "-p", help="latency profile(s), stackable"),
    scenario: list[str] = typer.Option(None, "--scenario", help="simulate only these scenarios (repeatable)"),
    walker: str = typer.Option(None, help="expected_value | monte_carlo | load"),
    samples: int = typer.Option(None, help="monte_carlo sample count"),
    seed: int = typer.Option(None, help="monte_carlo random seed (reproducible runs)"),
    load: str = typer.Option(None, "--load",
                             help="load walker: arrival rates file (default load.yaml next to scenarios.yaml)"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
    all_hops: bool = typer.Option(False, "--all-hops", help="show every hop in path order, not just the top-N"),
    output: list[str] = typer.Option(None, "--output", "-o", help="reporters: text | json | markdown (repeatable)"),
    region: str = typer.Option(None, help="region fallback: CloudFormation templates, or Terraform "
                                          "providers whose region does not resolve"),
    workspace: str = typer.Option(None, help="value of terraform.workspace (default: default)"),
) -> None:
    """Simulate every scenario and print the bottleneck report."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {
        "latency.profiles": _profiles(target, profile),
        "simulation.walker": walker, "simulation.samples": samples, "simulation.seed": seed,
        "simulation.load": str(_resolve(target, load)) if load else None,
        "format": fmt, "parsers.cloudformation.region": region, "parsers.terraform.region": region,
        "parsers.terraform.workspace": workspace,
        "report.outputs": output or None,
    })
    if cfg.get("simulation.walker") == "load":
        load_path = Path(cfg.get("simulation.load") or "load.yaml")
        if not load_path.is_absolute():
            load_path = _base_dir(target) / load_path
        if not load_path.is_file():
            raise typer.BadParameter(f"--walker load needs a load profile; none at {load_path} "
                                     f"(write one or pass --load)", param_hint="--load")
    output_ = pipeline.run(target, cfg, only=scenario or None)
    _write_reports(cfg, lambda r: r.render(output_.findings, output_.graph), "report",
                   _out_dir(target, cfg), top_n=cfg.get("analysis.top_n"), all_hops=all_hops)


@app.command()
@_guard
def graph(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
    region: str = typer.Option(None, help="region fallback: CloudFormation templates, or Terraform "
                                          "providers whose region does not resolve"),
    workspace: str = typer.Option(None, help="value of terraform.workspace (default: default)"),
) -> None:
    """Parse + normalise + infer edges; write graph.json. No simulation."""
    import json

    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {"format": fmt, "parsers.cloudformation.region": region,
                                          "parsers.terraform.region": region,
                                          "parsers.terraform.workspace": workspace})
    g, _ = pipeline.build_graph(target, cfg)
    out = _out_dir(target, cfg)
    (out / "graph.json").write_text(json.dumps(g.to_dict(), indent=2, default=str))
    typer.echo(f"{len(g.nodes)} nodes, {len(g.edges)} edges → {out / 'graph.json'}")
    for w in g.warnings:
        typer.echo(f"warning: {w}", err=True)


@app.command()
@_guard
def diff(
    before: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    after: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    profile: list[str] = typer.Option(None, "--profile", "-p", help="latency profile(s), applied to both sides"),
    scenario: str = typer.Option(None, "--scenario", help="compare only this scenario"),
    align_by: str = typer.Option("id", "--align-by",
                                 help="match nodes by id (same format) or label (Terraform vs CloudFormation)"),
    fail_on_regression: str = typer.Option(None, "--fail-on-regression",
                                           help="exit 2 if any total grows more than e.g. 50ms or 10%"),
    output: list[str] = typer.Option(None, "--output", "-o", help="reporters: text | json | markdown (repeatable)"),
) -> None:
    """Run both snapshots with the same profile and report what changed:
    moved/added resources, per-category shift, changed hops, recommendations."""
    from iacsim.differ import parse_threshold, run_diff, summarise
    _bootstrap(before)
    threshold = parse_threshold(fail_on_regression) if fail_on_regression else None
    overrides = {"latency.profiles": _profiles(before, profile), "report.outputs": output or None}
    cfg_before, cfg_after = load_config(_base_dir(before), overrides), load_config(_base_dir(after), overrides)
    report, g_before, g_after = run_diff(before, cfg_before, after, cfg_after,
                                         scenario=scenario, align_by=align_by)
    _write_reports(cfg_after, lambda r: r.render_diff(report, g_before, g_after), "diff",
                   _out_dir(after, cfg_after))
    typer.echo(summarise(report))
    if threshold:
        regressed = report.regressions(threshold)
        if regressed:
            names = ", ".join(f"{s.name} ({s.delta_ms:+,.1f} ms)" for s in regressed)
            typer.echo(f"REGRESSION past {fail_on_regression}: {names}", err=True)
            raise typer.Exit(code=EXIT_INPUT)


@app.command()
@_guard
def validate(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    strict: bool = typer.Option(False, "--strict",
                                help="exit 1 on any parser/graph warning, not only unwired scenario steps"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
    region: str = typer.Option(None, help="region fallback: CloudFormation templates, or Terraform "
                                          "providers whose region does not resolve"),
    workspace: str = typer.Option(None, help="value of terraform.workspace (default: default)"),
) -> None:
    """Parse, normalise, and check scenarios.yaml — no simulation. Exit 1 when a
    scenario step names a node no edge touches (a typo, or a resource nothing is
    wired to); parser/graph warnings are printed and, with --strict, also fail."""
    from iacsim.core import pipeline
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {"format": fmt, "parsers.cloudformation.region": region,
                                          "parsers.terraform.region": region,
                                          "parsers.terraform.workspace": workspace})
    g, _ = pipeline.build_graph(target, cfg)
    scenarios = pipeline.load_scenarios(g, target, cfg)
    profile = pipeline.load_profile(cfg)
    unwired = _unwired_steps(g, scenarios)
    typer.echo(f"ok: {len(g.nodes)} nodes, {len(g.edges)} edges, {len(scenarios)} scenarios")
    typer.echo(f"profile: {' → '.join(profile.sources)}")
    for w in [*g.warnings, *unwired]:
        typer.echo(f"warning: {w}", err=True)
    failed = unwired or (strict and g.warnings)
    raise typer.Exit(code=EXIT_PROBLEMS if failed else EXIT_OK)


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
@_guard
def view(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    port: int = typer.Option(0, help="port to serve on (0 = pick a free one)"),
    no_open: bool = typer.Option(False, "--no-open", help="do not open a browser"),
    duration: float = typer.Option(None, help="serve for N seconds then stop (default: until Ctrl-C)"),
) -> None:
    """Open the graph viewer: runs the pipeline if .iacsim/report.json is
    missing, then serves .iacsim/ over HTTP and opens the browser."""
    from iacsim.viewer import bind, prepare
    from iacsim.viewer import run as serve
    _bootstrap(target)
    cfg = load_config(_base_dir(target), {"report.outputs": ["json"]})
    out_dir = prepare(target, cfg)
    try:
        server, url = bind(out_dir, port=port)
    except OSError:
        raise ValueError(f"port {port} in use") from None
    typer.echo(f"viewer: {url}")
    typer.echo(f"serving {out_dir} — press Ctrl-C to stop")
    serve(server, open_browser=not no_open, duration=duration)


@app.command()
@_guard
def calibrate(
    target: Path = typer.Argument(..., exists=True, help="Terraform dir or CloudFormation template"),
    source: str = typer.Option(None, help="metric source: cloudwatch | fake | <plugin> (config: calibrate.source)"),
    window: str = typer.Option(None, help="lookback: 7d | 24h | 30m (config: calibrate.window)"),
    out: str = typer.Option(None, "--out",
                            help="profile to write (default: <target>/calibrated.yaml; relative = next to the target)"),
    region: str = typer.Option(None, help="AWS region for the metric source (default: each node's own)"),
    workspace: str = typer.Option(None, help="value of terraform.workspace (default: default)"),
    fmt: str = typer.Option(None, "--format", help="force parser: terraform | cloudformation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show coverage, write nothing"),
) -> None:
    """Replace guessed latency numbers with measured ones: query the configured
    metric source for every Lambda / table / LB / API in the graph and write a
    profile YAML (rung 2) to pass back as `--profile`. Exit 0 with skips,
    1 if nothing could be measured, 2 for an unknown source, 3 if the source is
    not set up."""
    from iacsim.core import pipeline
    from iacsim.core.registry import UnknownImplementation
    from iacsim.latency.calibrate import make_metric_source
    from iacsim.latency.calibrate.calibrator import calibrate as run_calibration
    from iacsim.latency.calibrate.writer import write_profile

    _bootstrap(target)
    base = _base_dir(target)
    cfg = load_config(base, {"calibrate.source": source, "calibrate.window": window,
                             "format": fmt, "parsers.cloudformation.region": region,
                             "parsers.terraform.region": region, "parsers.terraform.workspace": workspace})
    name = cfg.get("calibrate.source")
    if region:
        cfg.set(f"calibrate.sources.{name}.region", region)

    try:
        metric_source = make_metric_source(cfg, base)
    except UnknownImplementation:
        raise ValueError(f"unknown metric source '{name}'; available: "
                         f"{', '.join(METRIC_SOURCES.names())}") from None
    graph, raw = pipeline.build_graph(target, cfg)
    result = run_calibration(graph, metric_source, cfg.get("calibrate.window"), fmt=raw.format)

    typer.echo(_coverage_table(result, graph), nl=False)
    if not result.covered:
        typer.echo("nothing calibrated — check calibrate.source / --window; defaults unchanged", err=True)
        raise typer.Exit(code=EXIT_PROBLEMS)
    if dry_run:
        typer.echo("dry run — nothing written")
        return
    out_path = Path(out) if out else Path(cfg.get("calibrate.out"))
    if not out_path.is_absolute():
        out_path = base / out_path
    path = write_profile(result, out_path)
    typer.echo(f"wrote {path}")
    shown = path.name if path.parent.resolve() == base.resolve() else path     # relative works: see _resolve
    typer.echo(f"next: iacsim run {target} --profile {shown}")


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
