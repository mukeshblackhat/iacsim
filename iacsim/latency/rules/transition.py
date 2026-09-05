"""Cost rule: orchestrator state transition.

Every hop *out of* a Step Functions state machine (state machine → worker,
state machine → table) is one state transition, and each costs
`processing.step_functions.transition` on top of whatever the destination
charges. The hop *into* the state machine (StartExecution) is priced by the
`processing` rule as before — it uses the same `transition` key — so a
12-task workflow now pays 13 transitions, not 1.
"""

from __future__ import annotations

from iacsim.core.interfaces import COST_RULES, CostRule
from iacsim.core.models import Edge, InfraGraph, NodeKind, Profile


@COST_RULES.register("transition")
class TransitionRule(CostRule):
    def cost(self, edge: Edge, graph: InfraGraph, profile: Profile) -> dict[str, float]:
        src = graph.nodes.get(edge.src)
        if src is None or src.kind != NodeKind.ORCHESTRATOR:
            return {}
        value = profile.processing_for(src).get("transition")
        return {"transition": float(value)} if value is not None else {}
