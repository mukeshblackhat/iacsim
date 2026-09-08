"""Edge inference rules, one file each, ordered by confidence in config.

Each rule reads the graph + raw resources and returns Edges with an `evidence`
sentence. The pipeline merges on (src, dst) — ops union, least-trusted
confidence, rule names joined with `+` — so config order is a priority order
only for the evidence text. AWS and GCP rules share one flat list (G18): a rule
that finds none of its resource types returns [] and costs nothing.
"""
from iacsim.graph.inference import (  # noqa: F401  (register all)
    api_gateway_integration,
    env_var,
    event_source_mapping,
    gcp_eventarc,
    gcp_iam_binding,
    gcp_lb_chain,
    gcp_pubsub_push,
    gcp_workflows,
    iam_policy,
    lambda_permission,
    step_functions,
    target_group,
    vpc_peering,
)
