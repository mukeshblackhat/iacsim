"""Wires the extension points together. This is the only file that knows the
order of stages; every stage is fetched from a registry by the name in Config.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from iacsim.core.config import Config
from iacsim.core.interfaces import (
    ANALYZERS,
    COST_RULES,
    INFERENCE_RULES,
    NORMALISERS,
    PROFILE_SOURCES,
    SCENARIO_SOURCES,
    WALKERS,
)
from iacsim.core.models import (
    CONFIDENCE_ORDER,
    KIND_PRIORITY,
    Edge,
    Findings,
    InfraGraph,
    Latency,
    Profile,
    RawResources,
    Result,
    Scenario,
)
from iacsim.parsers.detect import detect_parser

AUTO_PROVIDER = "auto"       # DEFAULTS["provider"]: resolved here by detect_provider (G1)
FALLBACK_PROVIDER = "aws"    # nothing voted (an empty or unrecognised directory), or a tie


@dataclass
class PipelineOutput:
    graph: InfraGraph
    scenarios: list[Scenario]
    results: list[Result]
    findings: list[Findings]
    profile: Profile


def detect_provider(raw: RawResources) -> tuple[str, str | None]:
    """Which normaliser a directory is for, when `provider:` is not set (G1).

    Every registered normaliser declares the resource-type prefixes it owns
    (`Normaliser.PREFIXES`: `aws_` → aws, `google_` → gcp); each resource votes
    for the normaliser whose prefix it carries and the majority wins. Types no
    normaliser claims (`random_`, `tls_`, `azurerm_`…) do not vote; no votes at
    all, or a tie, is aws. Returns the name and, for a mixed directory, a warning
    saying what the minority loses: its resources go through the chosen
    normaliser's unknown-type path (kept as network nodes, one warning each)
    instead of becoming real nodes."""
    prefixes = {name: NORMALISERS.get(name).PREFIXES for name in NORMALISERS.names()}
    votes = dict.fromkeys(prefixes, 0)
    for r in raw.resources:
        for name, owned in prefixes.items():
            if owned and r.type.startswith(owned):
                votes[name] += 1
    chosen = max(votes, key=lambda n: (votes[n], n == FALLBACK_PROVIDER), default=FALLBACK_PROVIDER)
    if not votes.get(chosen):
        return FALLBACK_PROVIDER, None
    minority = {n: c for n, c in votes.items() if c and n != chosen}
    if not minority:
        return chosen, None
    lost = "; ".join(f"{c} {_glob(prefixes[n])} resource(s)" for n, c in sorted(minority.items()))
    return chosen, (f"mixed providers: normalised as {chosen} ({votes[chosen]} {_glob(prefixes[chosen])} resources); "
                    f"{lost} left to the unknown-type path — set `provider:` in iacsim.yaml or pass --provider")


def _glob(prefixes: tuple[str, ...]) -> str:
    return "|".join(f"{p}*" for p in prefixes)


def build_graph(target: Path, cfg: Config) -> tuple[InfraGraph, RawResources]:
    """Stages 1–3: parse → normalise → infer edges."""
    parser_cls = detect_parser(target, forced=cfg.get("format"))
    options = cfg.get(f"parsers.{parser_cls.registry_name}") or {}
    raw = parser_cls(**{k: v for k, v in options.items() if v is not None}).parse(target)

    provider, mixed = cfg.get("provider"), None
    if provider in (None, AUTO_PROVIDER):                # an explicit `provider:` / --provider wins
        provider, mixed = detect_provider(raw)
    graph = NORMALISERS.get(provider)().normalise(raw)
    graph.source_format = raw.format
    if mixed:
        graph.warnings.insert(0, mixed)                  # before the per-resource unknown-type warnings it explains
    graph.warnings.extend(raw.warnings)

    index: dict[tuple[str, str], Edge] = {}
    for existing in graph.edges:                       # the normaliser's internet → entry edges
        existing.ops = existing.ops or [existing.kind]
        index[(existing.src, existing.dst)] = existing
    for rule_name in cfg.get("inference.rules"):
        rule = INFERENCE_RULES.get(rule_name)()
        for edge in rule.apply(graph, raw):
            edge.rule = rule_name
            _merge_edge(graph, index, edge)
    return graph, raw


def _merge_edge(graph: InfraGraph, index: dict[tuple[str, str], Edge], edge: Edge) -> None:
    """One edge per (src, dst), independent of the order the rules ran in.

    A second rule finding the same pair *adds* its operation to `ops`, joins its
    evidence and rule name, and lifts the confidence; the priced `kind` is the
    highest-priority operation any rule found (KIND_PRIORITY: a path reads by
    default, `op: write` in a scenario overrides)."""
    edge.ops = sorted(set(edge.ops or [edge.kind]), key=KIND_PRIORITY.index)
    edge.kind = edge.ops[0]
    old = index.get((edge.src, edge.dst))
    if old is None:
        index[(edge.src, edge.dst)] = edge
        graph.add_edge(edge)
        return
    old.ops = sorted(set(old.ops) | set(edge.ops), key=KIND_PRIORITY.index)
    old.kind = old.ops[0]
    old.confidence = min((old.confidence, edge.confidence), key=CONFIDENCE_ORDER.index)
    if edge.rule and edge.rule not in (old.rule or "").split("+"):
        old.rule = f"{old.rule}+{edge.rule}" if old.rule else edge.rule
        old.evidence = f"{old.evidence}; {edge.rule}: {edge.evidence}"


def load_profile(cfg: Config) -> Profile:
    """Stage 4a: merge profile layers in order; later layers win per key."""
    from iacsim.latency.profile import merge_profiles
    layers = []
    for spec in cfg.get("latency.profiles"):
        source_name = "yaml_file" if spec.endswith((".yaml", ".yml")) else spec
        layers.append((spec, PROFILE_SOURCES.get(source_name)().load(spec)))
    return merge_profiles(layers)


def make_pricer(graph: InfraGraph, profile: Profile, cfg: Config) -> Callable[[Edge], Latency]:
    """One function that prices any edge — real or synthetic — with the enabled
    cost rules. The walker gets this so hops with no inferred edge (or with a
    per-step `op` override) are priced by exactly the same rules."""
    rules = [COST_RULES.get(name)() for name in cfg.get("latency.rules")]

    def price(edge: Edge) -> Latency:
        breakdown: dict[str, float] = {}
        for rule in rules:
            breakdown.update(rule.cost(edge, graph, profile))
        return Latency(expected=sum(breakdown.values()), breakdown=breakdown)

    return price


def cost_graph(graph: InfraGraph, profile: Profile, cfg: Config,
               price: Callable[[Edge], Latency] | None = None) -> None:
    """Stage 4b: every edge gets a Latency from the enabled cost rules.
    `run()` builds the pricer once and shares it with `simulate`."""
    price = price or make_pricer(graph, profile, cfg)
    for edge in graph.edges:
        edge.latency = price(edge)


def load_scenarios(graph: InfraGraph, target: Path, cfg: Config) -> list[Scenario]:
    """Stage 5: declared scenarios first, then inferred ones for entry points not covered."""
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    root = target if target.is_dir() else target.parent      # a template file: scenarios.yaml sits beside it
    for source_name in cfg.get("scenarios.sources"):
        for scenario in SCENARIO_SOURCES.get(source_name)().load(graph, root):
            if scenario.name not in seen:
                scenarios.append(scenario)
                seen.add(scenario.name)
    return scenarios


def simulate(graph: InfraGraph, scenarios: list[Scenario], cfg: Config, profile: Profile,
             root: Path | None = None, price: Callable[[Edge], Latency] | None = None) -> list[Result]:
    """Stage 6. The walker receives `price` so it can cost synthetic hops, and
    the full scenario list + `root` so the `load` walker can share resources
    across scenarios and find load.yaml next to scenarios.yaml."""
    walker = WALKERS.get(cfg.get("simulation.walker"))()
    price = price or make_pricer(graph, profile, cfg)
    return [walker.run(graph, s, price=price, profile=profile,
                       samples=cfg.get("simulation.samples"), seed=cfg.get("simulation.seed"),
                       scenarios=scenarios, root=root, load=cfg.get("simulation.load"),
                       tail_factor=cfg.get("simulation.tail_factor"))
            for s in scenarios]


def analyse(results: list[Result], graph: InfraGraph, profile: Profile, cfg: Config,
            scenarios: list[Scenario] | None = None) -> list[Findings]:
    """Stage 7."""
    analyzers = [ANALYZERS.get(name)() for name in cfg.get("analysis.analyzers")]
    by_name = {s.name: s for s in scenarios} if scenarios else {}
    out = []
    for result in results:
        findings = [f for a in analyzers for f in a.analyse(result, graph)]
        scenario = by_name.get(result.scenario)
        out.append(Findings(
            result.scenario, result.total_ms, findings, profile.sources,
            description=scenario.description if scenario else None,
            source=scenario.source if scenario else "declared",
            hops=result.hops, shape=result.shape, warnings=result.warnings,
            percentiles=result.percentiles, samples=result.samples, load=result.load,
        ))
    return out


def _select_scenarios(scenarios: list[Scenario], names: list[str]) -> list[Scenario]:
    import difflib
    by_name = {s.name: s for s in scenarios}
    for name in names:
        if name not in by_name:
            hint = difflib.get_close_matches(name, by_name, n=1)
            suffix = f" — did you mean '{hint[0]}'?" if hint else ""
            raise ValueError(f"unknown scenario '{name}'; have: {', '.join(by_name)}{suffix}")
    return [by_name[n] for n in names]


def run(target: Path, cfg: Config, only: list[str] | None = None) -> PipelineOutput:
    """The whole thing, for `iacsim run`. `only` keeps just the named scenarios
    (`--scenario`); an unknown name is a ValueError with a did-you-mean hint."""
    graph, _raw = build_graph(target, cfg)
    profile = load_profile(cfg)
    price = make_pricer(graph, profile, cfg)          # built once; costing and the walker share it
    cost_graph(graph, profile, cfg, price=price)
    scenarios = load_scenarios(graph, target, cfg)
    if only:
        scenarios = _select_scenarios(scenarios, only)
    root = target if target.is_dir() else target.parent
    results = simulate(graph, scenarios, cfg, profile, root=root, price=price)
    findings = analyse(results, graph, profile, cfg, scenarios)
    return PipelineOutput(graph, scenarios, results, findings, profile)
