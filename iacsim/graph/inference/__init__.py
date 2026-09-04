"""Edge inference rules, one file each, ordered by confidence in config.

Each rule reads the graph + raw resources and returns Edges with an `evidence`
sentence. The pipeline dedupes on (src, dst), first rule wins — so config
order is a priority order.
"""
from iacsim.graph.inference import (  # noqa: F401  (register all)
    api_gateway_integration,
    env_var,
    event_source_mapping,
    iam_policy,
    lambda_permission,
    step_functions,
    target_group,
    vpc_peering,
)
