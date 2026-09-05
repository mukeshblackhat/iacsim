"""Analyzer: what breaks first under load, at how many users, and what would
raise the ceiling.                                                          [M8]

Reads Result.load (the `load` walker's sweep). Silent for other walkers.
Findings, all `additive=False`, `latency_ms` = the users count the line is about:

  first_to_break   the resource with the lowest U at which ρ = 1, with why
                   (which scenario contributes most, hold time, slots)
  resource:<label> one line per resource that reaches the utilisation threshold
                   inside the sweep — U at threshold, U at saturation
  scenario_p99     the first U in the sweep where this scenario's p99 crosses
                   thresholds.p99_ms (or saturates)
  ceiling          rule-based suggestions that cite the IaC attribute:
                   reserved concurrency to raise, instance_class to bump,
                   provisioned capacity, the chattiest scenario to slow down,
                   and the reminder that provisioned concurrency ≠ capacity

Because utilisation is linear in U, U at ρ = 1 is exact: U / ρ(U).
"""

from __future__ import annotations

import math

from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result

MAX_RESOURCE_LINES = 6


@ANALYZERS.register("saturation")
class SaturationAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        load = result.load
        if not load or not load.get("resources"):
            return []
        self.graph, self.load = graph, load
        self.users = list(load["users"])
        self.limits = self._limits()
        out = self._first_to_break() + self._resource_lines() + self._scenario_p99(result) + self._ceilings()
        return out

    # ------------------------------------------------------------ where each resource breaks

    def _limits(self) -> dict[str, tuple[float | None, float | None]]:
        """resource key → (users at threshold utilisation, users at ρ = 1); None if never inside 10× the sweep."""
        threshold = float(self.load["thresholds"].get("utilisation", 0.8))
        u_max = self.users[-1]
        util_at_max = self.load["utilisation"].get(u_max) or {}
        limits = {}
        for key, rho in util_at_max.items():
            if rho is None:                              # no servers at all: broken at any load
                limits[key] = (0.0, 0.0)
                continue
            if rho <= 0:
                limits[key] = (None, None)
                continue
            at_one = u_max / rho
            limits[key] = (at_one * threshold, at_one)
        return limits

    def _first_to_break(self) -> list[Finding]:
        candidates = [(u, key) for key, (_, u) in self.limits.items() if u is not None]
        if not candidates:
            return []
        u_break, key = min(candidates)
        r = self.load["resources"][key]
        top = self._top_contributor(key)
        detail = (f"{r['label']} reaches 100% utilisation at ~{u_break:,.0f} users — "
                  f"{self._capacity_phrase(r)}; biggest load: {top}. Source: {r['source']}")
        return [Finding("saturation", "first_to_break", round(u_break, 1), 0.0, detail,
                        refs=[key], layer="capacity", additive=False)]

    def _resource_lines(self) -> list[Finding]:
        lines = []
        for key, (u_thr, u_one) in sorted(self.limits.items(), key=lambda kv: kv[1][1] or math.inf):
            if u_one is None or u_one > self.users[-1] * 10:
                continue
            r = self.load["resources"][key]
            rho_max = self.load["utilisation"][self.users[-1]].get(key, 0.0)
            rho_text = f"{rho_max:.0%}" if rho_max is not None else "SAT (no servers)"
            detail = (f"{self.load['thresholds'].get('utilisation', 0.8):.0%} at ~{u_thr:,.0f} users, "
                      f"100% at ~{u_one:,.0f}; {rho_text} at {self.users[-1]:,} users; {self._capacity_phrase(r)}")
            lines.append(Finding("saturation", f"resource:{r['label']}", round(u_one, 1), rho_max or 0.0, detail,
                                 refs=[key], layer="capacity", additive=False))
        return lines[:MAX_RESOURCE_LINES]

    def _scenario_p99(self, result: Result) -> list[Finding]:
        lat = self.load.get("latency") or {}
        if not lat or not self.load.get("traffic", True):
            return []
        limit = float(self.load["thresholds"].get("p99_ms", 2000))
        for u in self.users:
            row = lat.get(u) or {}
            if row.get("saturated"):
                return [Finding("saturation", "scenario_p99", float(u), 0.0,
                                f"saturated at {u:,} users (by {', '.join(row.get('saturated_by', []))})",
                                layer="capacity", additive=False)]
            if row.get("p99_ms") is not None and row["p99_ms"] > limit:
                return [Finding("saturation", "scenario_p99", float(u), 0.0,
                                f"p99 {row['p99_ms']:,.0f} ms crosses the {limit:,.0f} ms threshold at {u:,} users",
                                layer="capacity", additive=False)]
        last = lat.get(self.users[-1]) or {}
        if last.get("p99_ms") is not None:
            return [Finding("saturation", "scenario_p99", float(self.users[-1]), 0.0,
                            f"p99 stays under {limit:,.0f} ms through {self.users[-1]:,} users "
                            f"({last['p99_ms']:,.0f} ms)", layer="capacity", additive=False)]
        return []

    # ------------------------------------------------------------ what would raise the ceiling

    def _ceilings(self) -> list[Finding]:
        out = []
        target = self.users[-1]
        threshold = float(self.load["thresholds"].get("utilisation", 0.8))
        for key, (_, u_one) in sorted(self.limits.items(), key=lambda kv: kv[1][1] or math.inf):
            if u_one is None or u_one > target:
                continue
            r = self.load["resources"][key]
            rho = self.load["utilisation"][target].get(key, 0.0)
            if rho is None:                              # throttled off: any positive capacity is the fix
                out.append(Finding("saturation", "ceiling", float(target), 0.0,
                                   f"{r['label']} has no capacity at all ({r['source']}) — give it some",
                                   refs=[key], layer="capacity", additive=False))
                break
            need = rho / threshold                      # capacity multiplier to sit at threshold at `target`
            out.append(self._ceiling_for(r, key, need, target))
            top = self._top_contributor(key, with_share=True)
            if top:
                name, share = top
                out.append(Finding("saturation", "ceiling", float(target), share,
                                   f"`{name}` is {share:.0%} of the load on {r['label']} — a longer interval "
                                   f"in load.yaml (or fewer calls per request) cuts it proportionally",
                                   refs=[key], layer="capacity", additive=False))
            if r["subtype"] == "lambda":
                out.append(Finding("saturation", "ceiling", float(target), 0.0,
                                   "provisioned concurrency removes cold starts but adds no capacity — "
                                   "it does not move this ceiling", refs=[key], layer="capacity", additive=False))
            break                                       # the first ceiling is the one to fix
        return out

    def _ceiling_for(self, r: dict, key: str, need: float, target: int) -> Finding:
        st = r["subtype"]
        if st == "lambda" and r["slots"] and r.get("members"):
            new = math.ceil(r["slots"] * need)
            text = (f"raise the account Lambda concurrency quota (capacity.lambda.account_concurrency) so the "
                    f"unreserved pool is ≥ {new:,} (now {r['slots']:,.0f}) for {target:,} users, or reserve "
                    f"concurrency for the heaviest holders so they stop sharing")
        elif st == "lambda":
            new = math.ceil(r["slots"] * need)
            text = f"raise reserved_concurrent_executions on {key} from {r['slots']:,.0f} → {new:,} for {target:,} users"
        elif st == "rds":
            text = (f"instance_class → a class with ≥ {math.ceil(r['slots'] * need):,} max_connections "
                    f"(now {r['slots']:,.0f}), or pool connections in front of it")
        elif st == "dynamodb" and "provisioned" in r["source"]:
            text = f"raise read_capacity / write_capacity to ≥ {math.ceil(r['rps'] * need):,} units (now {r['rps']:,.0f})"
        elif st in ("ec2", "fargate"):
            text = f"scale instances / desired_count by ×{need:.1f} on {key}"
        else:
            text = f"×{need:.1f} more capacity on {r['label']} ({r['source']})"
        return Finding("saturation", "ceiling", float(target), 0.0, text, refs=[key], layer="capacity", additive=False)

    # ------------------------------------------------------------ helpers

    def _top_contributor(self, key: str, with_share: bool = False):
        r = self.load["resources"][key]
        by = r.get("by_scenario") or {}
        if not by:
            return None if with_share else "n/a"
        name, erl = max(by.items(), key=lambda kv: kv[1])
        total = sum(by.values()) or 1.0
        share = erl / total
        return (name, share) if with_share else f"`{name}` ({share:.0%} of its load)"

    @staticmethod
    def _capacity_phrase(r: dict) -> str:
        if r.get("slots") is not None:
            return f"{r['slots']:,.0f} concurrent slots"
        return f"{r['rps']:,.0f} requests/s"
