"""Wires the extension points together. This is the only file that knows the
order of stages; every stage is fetched from a registry by the name in Config.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iacsim.core.config import Config
from iacsim.core.interfaces import (
    ANALYZERS, COST_RULES, INFERENCE_RULES, NORMALISERS, PARSERS, PROFILE_SOURCES,
    SCENARIO_SOURCES, WALKERS,
)
from iacsim.core.models import Findings, InfraGraph, Latency, Profile, RawResources, Result, Scenario
from iacsim.parsers.detect import detect_parser


@dataclass
class PipelineOutput:
    graph: InfraGraph
    scenarios: list[Scenario]
    results: list[Result]
    findings: list[Findings]
    profile: Profile


def build_graph(target: Path, cfg: Config) -> tuple[InfraGraph, RawResources]:
    """Stages 1–3: parse → normalise → infer edges."""
    parser_cls = detect_parser(target, forced=cfg.get("format"))
    raw = parser_cls().parse(target)

    graph = NORMALISERS.get(cfg.get("provider"))().normalise(raw)
    graph.source_format = raw.format
    graph.warnings.extend(raw.warnings)

    for rule_name in cfg.get("inference.rules"):
        rule = INFERENCE_RULES.get(rule_name)()
        for edge in rule.apply(graph, raw):
            edge.rule = rule_name
            if graph.find_edge(edge.src, edge.dst) is None:
                graph.add_edge(edge)
    return graph, raw


def load_profile(cfg: Config) -> Profile:
    """Stage 4a: merge profile layers in order; later layers win per key."""
    from iacsim.latency.profile import merge_profiles
    layers = []
    for spec in cfg.get("latency.profiles"):
        source_name = "yaml_file" if spec.endswith((".yaml", ".yml")) else spec
        layers.append((spec, PROFILE_SOURCES.get(source_name)().load(spec)))
    return merge_profiles(layers)


def cost_graph(graph: InfraGraph, profile: Profile, cfg: Config) -> None:
    """Stage 4b: every edge gets a Latency from the enabled cost rules."""
    rules = [COST_RULES.get(name)() for name in cfg.get("latency.rules")]
    for edge in graph.edges:
        breakdown: dict[str, float] = {}
        for rule in rules:
            breakdown.update(rule.cost(edge, graph, profile))
        edge.latency = Latency(expected=sum(breakdown.values()), breakdown=breakdown)


def load_scenarios(graph: InfraGraph, target: Path, cfg: Config) -> list[Scenario]:
    """Stage 5: declared scenarios first, then inferred ones for entry points not covered."""
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for source_name in cfg.get("scenarios.sources"):
        for scenario in SCENARIO_SOURCES.get(source_name)().load(graph, target):
            if scenario.name not in seen:
                scenarios.append(scenario)
                seen.add(scenario.name)
    return scenarios


def simulate(graph: InfraGraph, scenarios: list[Scenario], cfg: Config) -> list[Result]:
    """Stage 6."""
    walker = WALKERS.get(cfg.get("simulation.walker"))()
    return [walker.run(graph, s, samples=cfg.get("simulation.samples")) for s in scenarios]


def analyse(results: list[Result], graph: InfraGraph, profile: Profile, cfg: Config) -> list[Findings]:
    """Stage 7."""
    analyzers = [ANALYZERS.get(name)() for name in cfg.get("analysis.analyzers")]
    out = []
    for result in results:
        findings = [f for a in analyzers for f in a.analyse(result, graph)]
        out.append(Findings(result.scenario, result.total_ms, findings, profile.sources))
    return out


def run(target: Path, cfg: Config) -> PipelineOutput:
    """The whole thing, for `iacsim run`."""
    graph, _raw = build_graph(target, cfg)
    profile = load_profile(cfg)
    cost_graph(graph, profile, cfg)
    scenarios = load_scenarios(graph, target, cfg)
    results = simulate(graph, scenarios, cfg)
    findings = analyse(results, graph, profile, cfg)
    return PipelineOutput(graph, scenarios, results, findings, profile)
