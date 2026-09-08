"""AWS resource types → neutral (kind, subtype) + placement.               [M1 ✅]

The mapping table is the whole point of this file. Adding a resource type is
one line. Types not in the table fall into three buckets:

  IGNORED_PREFIXES  glue with no latency meaning (IAM, security groups, target
                    group attachments, API Gateway plumbing, ...). Dropped from
                    the graph silently — inference rules still read them from
                    RawResources.
  anything else     kept as a NETWORK node with a warning, so nothing vanishes.

Placement comes from the provider region recorded by the parser, plus
`availability_zone` / `subnet_id` → subnet's AZ / `vpc_id` → VPC address.

The normaliser also adds one EXTERNAL node, `internet`, with an edge into
every public entry point (gateway, load balancer, CDN) so scenarios can start
from a user.

It owns AWS's two behaviour tables as well (`INVOKE_KEYS`, `COLD_START`, both
declared on the `Normaliser` ABC): the latency rules read them through
`behaviour_tables()` rather than hardcoding cloud names, so a new subtype is
still one line per table here and never an edit in `latency/rules/`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from iacsim.core.interfaces import NORMALISERS, Normaliser
from iacsim.core.models import (
    PHYSICAL_NAME_ATTRS,
    Confidence,
    Edge,
    EdgeKind,
    InfraGraph,
    Node,
    NodeKind,
    Placement,
    RawResource,
    RawResources,
)
from iacsim.core.refs import addresses_in

# terraform type → (kind, subtype). CloudFormation types arrive already canonicalised
# to their Terraform spelling (parsers/cloudformation/canonical.py), so only aws_* keys live here.
TYPE_MAP: dict[str, tuple[NodeKind, str]] = {
    # compute
    "aws_lambda_function":            (NodeKind.COMPUTE, "lambda"),
    "aws_instance":                   (NodeKind.COMPUTE, "ec2"),
    "aws_ecs_service":                (NodeKind.COMPUTE, "fargate"),
    "aws_autoscaling_group":          (NodeKind.COMPUTE, "ec2"),     # N instances, see _capacity_attrs
    # datastores
    "aws_dynamodb_table":             (NodeKind.DATASTORE, "dynamodb"),
    "aws_db_instance":                (NodeKind.DATASTORE, "rds"),
    "aws_rds_cluster":                (NodeKind.DATASTORE, "rds"),
    "aws_elasticache_cluster":        (NodeKind.DATASTORE, "elasticache"),
    "aws_elasticache_replication_group": (NodeKind.DATASTORE, "elasticache"),
    "aws_s3_bucket":                  (NodeKind.DATASTORE, "s3"),
    # traffic
    "aws_lb":                         (NodeKind.LB, "alb"),
    "aws_elb":                        (NodeKind.LB, "alb"),      # classic ELB — priced like an ALB
    "aws_alb":                        (NodeKind.LB, "alb"),
    "aws_api_gateway_rest_api":       (NodeKind.GATEWAY, "api_gateway"),
    "aws_apigatewayv2_api":           (NodeKind.GATEWAY, "api_gateway_v2"),
    "aws_cloudfront_distribution":    (NodeKind.CDN, "cloudfront"),
    # async
    "aws_sqs_queue":                  (NodeKind.QUEUE, "sqs"),
    "aws_sns_topic":                  (NodeKind.QUEUE, "sns"),
    "aws_kinesis_stream":             (NodeKind.QUEUE, "kinesis"),
    "aws_sfn_state_machine":          (NodeKind.ORCHESTRATOR, "step_functions"),
    # placement / plumbing
    "aws_vpc":                        (NodeKind.NETWORK, "vpc"),
    "aws_subnet":                     (NodeKind.NETWORK, "subnet"),
    "aws_vpc_peering_connection":     (NodeKind.NETWORK, "vpc_peering"),
    "aws_nat_gateway":                (NodeKind.NETWORK, "nat"),
}

# Glue with no latency meaning; dropped silently (inference rules read them from raw).
IGNORED_PREFIXES = (
    "random_", "null_resource", "terraform_data", "time_sleep", "time_static", "local_file",
    "archive_file", "tls_", "aws_key_pair", "aws_s3_object", "aws_ssm_parameter", "aws_secretsmanager_",
    "aws_kms_", "aws_eip", "aws_route_table", "aws_default_", "aws_acm_", "aws_wafv2_",
    "aws_vpc_security_group_", "aws_ebs_volume", "aws_volume_attachment", "aws_guardduty_",
    "aws_lambda_layer_version", "aws_lambda_function_url", "aws_lambda_alias", "aws_backup_",
    "aws_cloudtrail", "aws_config_", "aws_sns_topic_subscription", "aws_ses_", "aws_athena_",
    "aws_iam_",
    "aws_security_group",
    "aws_lb_target_group", "aws_lb_listener", "aws_alb_target_group", "aws_alb_listener",
    "aws_db_subnet_group", "aws_db_parameter_group",
    "aws_api_gateway_", "aws_apigatewayv2_",
    "aws_lambda_permission", "aws_lambda_event_source_mapping",
    "aws_s3_bucket_",
    "aws_vpc_peering_connection_accepter", "aws_route", "aws_internet_gateway",
    "aws_cloudwatch_", "aws_cdk_",
    "aws_sqs_queue_policy", "aws_sns_topic_subscription", "aws_sns_topic_policy",
    "aws_ecs_cluster", "aws_ecs_task_definition",
    "aws_autoscaling_attachment", "aws_autoscaling_policy", "aws_launch_template", "aws_launch_configuration",
)

# Attributes worth keeping on the node (what latency rules and reports might use).
KEEP_ATTRS = ("memory_size", "timeout", "runtime", "engine", "engine_version", "instance_class",
              "instance_type", "billing_mode", "reserved_concurrent_executions", "load_balancer_type",
              "node_type", "type", "desired_count", "read_capacity", "write_capacity",
              "desired_capacity", "min_size", "max_size")

ENTRY_KINDS = (NodeKind.GATEWAY, NodeKind.LB, NodeKind.CDN)
INTERNET = "internet"


@NORMALISERS.register("aws")
class AwsNormaliser(Normaliser):
    # What a call *into* each subtype costs: the key inside its `processing` block
    # charged on an INVOKE hop, and the fallback for any edge kind the block does
    # not price. Every subtype TYPE_MAP can produce is here except the NETWORK ones,
    # which are never a hop (scenarios/inferred.py NOT_A_HOP) and have no block.
    INVOKE_KEYS: ClassVar[dict[str, str]] = {
        "lambda": "warm", "ec2": "handle", "fargate": "handle",
        "api_gateway": "route", "api_gateway_v2": "route", "alb": "route",
        "step_functions": "transition", "cloudfront": "miss",
        "dynamodb": "read", "rds": "read", "s3": "read", "elasticache": "read",
        "sqs": "publish", "sns": "publish", "kinesis": "publish",
    }
    # Subtypes that pay a cold start; their profile blocks carry `cold` / `cold_prob`.
    COLD_START: ClassVar[frozenset[str]] = frozenset({"lambda"})
    PREFIXES: ClassVar[tuple[str, ...]] = ("aws_",)      # what votes for this normaliser (G1)

    def normalise(self, raw: RawResources) -> InfraGraph:
        graph = InfraGraph()
        by_address = {r.address: r for r in raw.resources}

        for r in raw.resources:
            if r.type in TYPE_MAP:
                kind, subtype = TYPE_MAP[r.type]
            elif r.type.startswith(IGNORED_PREFIXES):
                continue
            else:
                kind, subtype = NodeKind.NETWORK, r.type
                graph.warnings.append(f"{r.address}: unknown type {r.type}; kept as a network node")
            attrs = {k: r.attrs[k] for k in KEEP_ATTRS if k in r.attrs and r.attrs[k] is not None}
            attrs.update(_capacity_attrs(subtype, attrs))
            graph.add_node(Node(
                id=r.address, kind=kind, subtype=subtype,
                placement=_placement(r, by_address),
                attrs=attrs,
                label=_label(r),
            ))

        self._add_internet(graph)
        return graph

    @staticmethod
    def _add_internet(graph: InfraGraph) -> None:
        graph.add_node(Node(id=INTERNET, kind=NodeKind.EXTERNAL, subtype="internet", label="internet"))
        for node in list(graph.nodes.values()):
            if node.kind in ENTRY_KINDS:
                graph.add_edge(Edge(INTERNET, node.id, EdgeKind.INVOKE, Confidence.HIGH,
                                    f"{node.subtype} {node.label or node.id} is a public entry point",
                                    rule="normaliser"))


# ------------------------------------------------------------------ capacity (M8)

def _capacity_attrs(subtype: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Neutral capacity keys the `load` walker reads, derived from what the IaC
    declares: `concurrency` (Lambda reserved slots; absent → shares the account
    pool), `instances` (EC2 = 1 per resource, ECS = desired_count). DynamoDB
    read/write_capacity are kept as-is (None on PAY_PER_REQUEST)."""
    out: dict[str, Any] = {}
    if subtype == "lambda":
        reserved = attrs.get("reserved_concurrent_executions")
        if isinstance(reserved, int) and reserved >= 0:
            out["concurrency"] = reserved
    elif subtype == "ec2":
        # a single aws_instance is one server; an aws_autoscaling_group is
        # desired_capacity (or min_size) of them
        for key in ("desired_capacity", "min_size"):
            if isinstance(attrs.get(key), int) and attrs[key] > 0:
                out["instances"] = attrs[key]
                break
        else:
            out["instances"] = 1
    elif subtype == "fargate" and isinstance(attrs.get("desired_count"), int):
        out["instances"] = attrs["desired_count"]
    return out


# ------------------------------------------------------------------ placement

def _placement(r: RawResource, by_address: dict[str, RawResource]) -> Placement:
    az = _string(r.attrs.get("availability_zone"))
    vpc = _first_address(r.attrs.get("vpc_id"))
    subnet = _first_address(r.attrs.get("subnet_id"))
    spans_azs = False

    subnet_list = r.attrs.get("subnets") or r.attrs.get("vpc_zone_identifier")   # LB / ECS, ASG
    if subnet is None and (group := _first_address(r.attrs.get("db_subnet_group_name"))) in by_address:
        subnet_list = by_address[group].attrs.get("subnet_ids")    # RDS: subnets live on the subnet group
    if subnet is None and isinstance(subnet_list, list) and subnet_list:
        subnet = _first_address(subnet_list[0])                # one subnet is enough to find the VPC
        spans_azs = len(subnet_list) > 1 and az is None        # ...but it may live in several AZs

    if subnet and subnet in by_address:
        sub = by_address[subnet]
        if not spans_azs:
            az = az or _string(sub.attrs.get("availability_zone"))
        vpc = vpc or _first_address(sub.attrs.get("vpc_id"))

    return Placement(region=r.region, az=az, vpc=vpc, subnet=None if spans_azs else subnet)


def _first_address(value: Any) -> str | None:
    found = addresses_in(value)
    return found[0] if found else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and "${" not in value else None


def _label(r: RawResource) -> str | None:
    for key in PHYSICAL_NAME_ATTRS:
        if isinstance(r.attrs.get(key), str) and "${" not in r.attrs[key]:
            return r.attrs[key]
    tags = r.attrs.get("tags")
    if isinstance(tags, dict) and isinstance(tags.get("Name"), str):
        return tags["Name"]
    return None
