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
"""

from __future__ import annotations

from typing import Any

from iacsim.core.interfaces import NORMALISERS, Normaliser
from iacsim.core.models import (
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

# terraform type / cloudformation type → (kind, subtype)
TYPE_MAP: dict[str, tuple[NodeKind, str]] = {
    # compute
    "aws_lambda_function":            (NodeKind.COMPUTE, "lambda"),
    "AWS::Lambda::Function":          (NodeKind.COMPUTE, "lambda"),
    "aws_instance":                   (NodeKind.COMPUTE, "ec2"),
    "AWS::EC2::Instance":             (NodeKind.COMPUTE, "ec2"),
    "aws_ecs_service":                (NodeKind.COMPUTE, "fargate"),
    "AWS::ECS::Service":              (NodeKind.COMPUTE, "fargate"),
    # datastores
    "aws_dynamodb_table":             (NodeKind.DATASTORE, "dynamodb"),
    "AWS::DynamoDB::Table":           (NodeKind.DATASTORE, "dynamodb"),
    "aws_db_instance":                (NodeKind.DATASTORE, "rds"),
    "AWS::RDS::DBInstance":           (NodeKind.DATASTORE, "rds"),
    "aws_rds_cluster":                (NodeKind.DATASTORE, "rds"),
    "aws_elasticache_cluster":        (NodeKind.DATASTORE, "elasticache"),
    "aws_elasticache_replication_group": (NodeKind.DATASTORE, "elasticache"),
    "aws_s3_bucket":                  (NodeKind.DATASTORE, "s3"),
    "AWS::S3::Bucket":                (NodeKind.DATASTORE, "s3"),
    # traffic
    "aws_lb":                         (NodeKind.LB, "alb"),
    "aws_alb":                        (NodeKind.LB, "alb"),
    "AWS::ElasticLoadBalancingV2::LoadBalancer": (NodeKind.LB, "alb"),
    "aws_api_gateway_rest_api":       (NodeKind.GATEWAY, "api_gateway"),
    "AWS::ApiGateway::RestApi":       (NodeKind.GATEWAY, "api_gateway"),
    "aws_apigatewayv2_api":           (NodeKind.GATEWAY, "api_gateway_v2"),
    "AWS::ApiGatewayV2::Api":         (NodeKind.GATEWAY, "api_gateway_v2"),
    "aws_cloudfront_distribution":    (NodeKind.CDN, "cloudfront"),
    "AWS::CloudFront::Distribution":  (NodeKind.CDN, "cloudfront"),
    # async
    "aws_sqs_queue":                  (NodeKind.QUEUE, "sqs"),
    "AWS::SQS::Queue":                (NodeKind.QUEUE, "sqs"),
    "aws_sns_topic":                  (NodeKind.QUEUE, "sns"),
    "AWS::SNS::Topic":                (NodeKind.QUEUE, "sns"),
    "aws_kinesis_stream":             (NodeKind.QUEUE, "kinesis"),
    "aws_sfn_state_machine":          (NodeKind.ORCHESTRATOR, "step_functions"),
    "AWS::StepFunctions::StateMachine": (NodeKind.ORCHESTRATOR, "step_functions"),
    # placement / plumbing
    "aws_vpc":                        (NodeKind.NETWORK, "vpc"),
    "AWS::EC2::VPC":                  (NodeKind.NETWORK, "vpc"),
    "aws_subnet":                     (NodeKind.NETWORK, "subnet"),
    "AWS::EC2::Subnet":               (NodeKind.NETWORK, "subnet"),
    "aws_vpc_peering_connection":     (NodeKind.NETWORK, "vpc_peering"),
    "aws_nat_gateway":                (NodeKind.NETWORK, "nat"),
}

# Glue with no latency meaning; dropped silently (inference rules read them from raw).
IGNORED_PREFIXES = (
    "aws_iam_", "AWS::IAM::",
    "aws_security_group", "AWS::EC2::SecurityGroup",
    "aws_lb_target_group", "aws_lb_listener", "aws_alb_target_group", "aws_alb_listener",
    "AWS::ElasticLoadBalancingV2::TargetGroup", "AWS::ElasticLoadBalancingV2::Listener",
    "aws_db_subnet_group", "aws_db_parameter_group",
    "aws_api_gateway_", "AWS::ApiGateway::", "aws_apigatewayv2_",
    "aws_lambda_permission", "aws_lambda_event_source_mapping", "AWS::Lambda::Permission",
    "AWS::Lambda::EventSourceMapping",
    "aws_s3_bucket_", "AWS::S3::BucketPolicy",
    "aws_vpc_peering_connection_accepter", "aws_route", "aws_internet_gateway",
    "aws_cloudwatch_", "AWS::Logs::", "AWS::CDK::",
    "aws_sqs_queue_policy", "aws_sns_topic_subscription", "aws_sns_topic_policy",
    "aws_ecs_cluster", "aws_ecs_task_definition",
)

# Attributes worth keeping on the node (what latency rules and reports might use).
KEEP_ATTRS = ("memory_size", "timeout", "runtime", "engine", "engine_version", "instance_class",
              "instance_type", "billing_mode", "reserved_concurrent_executions", "load_balancer_type",
              "node_type", "type")

ENTRY_KINDS = (NodeKind.GATEWAY, NodeKind.LB, NodeKind.CDN)
INTERNET = "internet"


@NORMALISERS.register("aws")
class AwsNormaliser(Normaliser):
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
            graph.add_node(Node(
                id=r.address, kind=kind, subtype=subtype,
                placement=_placement(r, by_address),
                attrs={k: r.attrs[k] for k in KEEP_ATTRS if k in r.attrs and r.attrs[k] is not None},
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


# ------------------------------------------------------------------ placement

def _placement(r: RawResource, by_address: dict[str, RawResource]) -> Placement:
    az = _string(r.attrs.get("availability_zone"))
    vpc = _first_address(r.attrs.get("vpc_id"))
    subnet = _first_address(r.attrs.get("subnet_id"))
    spans_azs = False

    subnet_list = r.attrs.get("subnets")
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
    for key in ("function_name", "name", "identifier", "bucket", "rest_api_name"):
        if isinstance(r.attrs.get(key), str) and "${" not in r.attrs[key]:
            return r.attrs[key]
    tags = r.attrs.get("tags")
    if isinstance(tags, dict) and isinstance(tags.get("Name"), str):
        return tags["Name"]
    return None
