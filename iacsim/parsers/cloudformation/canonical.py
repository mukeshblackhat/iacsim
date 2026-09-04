"""Make CloudFormation resources look like Terraform ones — in ONE place — so
the normaliser and every inference rule work unchanged on both formats.

Three things happen here:

1. **Type**: `AWS::Lambda::Function` → `aws_lambda_function` (CANONICAL_TYPES;
   unknown types fall back to `aws_<service>_<resource>`). The original type
   is kept in attrs["cfn_type"].

2. **Attribute names**: top-level property keys go CamelCase → snake_case
   (`MemorySize` → `memory_size`), then per-type ALIASES fix the handful that
   Terraform names differently:

       AWS::DynamoDB::Table.TableName            → name
       AWS::S3::Bucket.BucketName                → bucket
       AWS::StepFunctions::StateMachine.StateMachineName / DefinitionString → name / definition
       AWS::IAM::Policy.PolicyDocument / Roles   → policy / role
       AWS::RDS::DBInstance.DBInstanceClass / DBInstanceIdentifier → instance_class / identifier
       AWS::ElasticLoadBalancingV2::LoadBalancer.Type → load_balancer_type
       AWS::ElasticLoadBalancingV2::Listener.DefaultActions → default_action
       AWS::ECS::Service.LoadBalancers           → load_balancer
       AWS::Lambda::Function.Environment.Variables → environment.variables (variable names untouched)
       AWS::DynamoDB::Table.ProvisionedThroughput → read_capacity / write_capacity
       Tags [{Key, Value}]                        → {Key: Value}
   (ReservedConcurrentExecutions and DesiredCount snake_case to the Terraform names on their own.)

   Nested keys are otherwise left alone: IAM `Statement` / `Action` / `Resource`,
   ASL state names and env-var names must keep their spelling.

3. **Synthetic resources** for things Terraform models as separate blocks:
       AWS::ApiGateway::Method with an Integration → aws_api_gateway_integration
       AWS::ElasticLoadBalancingV2::TargetGroup.Targets → aws_lb_target_group_attachment (one per target)

Plus `resolve_physical_names()`: CDK often passes table / bucket / queue
names to Lambdas as literal strings (`WORKFLOWS_TABLE = "AsyncWorkflowsStaging"`)
rather than `Ref`s. When an environment variable equals the physical name of a
datastore or queue, it becomes the same placeholder a `Ref` would have
produced, so the env_var rule sees it. Only datastores / queues qualify: a
state machine's *name* in an env var is informational (you need the ARN to
start it), and Terraform would not have resolved a literal either.
"""

from __future__ import annotations

import re
from typing import Any

from iacsim.core.models import RawResource
from iacsim.core.refs import placeholder

CANONICAL_TYPES: dict[str, str] = {
    "AWS::Lambda::Function": "aws_lambda_function",
    "AWS::Lambda::Permission": "aws_lambda_permission",
    "AWS::Lambda::EventSourceMapping": "aws_lambda_event_source_mapping",
    "AWS::DynamoDB::Table": "aws_dynamodb_table",
    "AWS::S3::Bucket": "aws_s3_bucket",
    "AWS::S3::BucketPolicy": "aws_s3_bucket_policy",
    "AWS::RDS::DBInstance": "aws_db_instance",
    "AWS::RDS::DBCluster": "aws_rds_cluster",
    "AWS::RDS::DBSubnetGroup": "aws_db_subnet_group",
    "AWS::ElastiCache::CacheCluster": "aws_elasticache_cluster",
    "AWS::ElastiCache::ReplicationGroup": "aws_elasticache_replication_group",
    "AWS::EC2::Instance": "aws_instance",
    "AWS::EC2::VPC": "aws_vpc",
    "AWS::EC2::Subnet": "aws_subnet",
    "AWS::EC2::SecurityGroup": "aws_security_group",
    "AWS::EC2::VPCPeeringConnection": "aws_vpc_peering_connection",
    "AWS::EC2::NatGateway": "aws_nat_gateway",
    "AWS::EC2::InternetGateway": "aws_internet_gateway",
    "AWS::EC2::Route": "aws_route",
    "AWS::ElasticLoadBalancingV2::LoadBalancer": "aws_lb",
    "AWS::ElasticLoadBalancingV2::TargetGroup": "aws_lb_target_group",
    "AWS::ElasticLoadBalancingV2::Listener": "aws_lb_listener",
    "AWS::ECS::Service": "aws_ecs_service",
    "AWS::ECS::Cluster": "aws_ecs_cluster",
    "AWS::ECS::TaskDefinition": "aws_ecs_task_definition",
    "AWS::ApiGateway::RestApi": "aws_api_gateway_rest_api",
    "AWS::ApiGateway::Resource": "aws_api_gateway_resource",
    "AWS::ApiGateway::Method": "aws_api_gateway_method",
    "AWS::ApiGateway::Deployment": "aws_api_gateway_deployment",
    "AWS::ApiGateway::Stage": "aws_api_gateway_stage",
    "AWS::ApiGateway::Account": "aws_api_gateway_account",
    "AWS::ApiGatewayV2::Api": "aws_apigatewayv2_api",
    "AWS::ApiGatewayV2::Integration": "aws_apigatewayv2_integration",
    "AWS::ApiGatewayV2::Route": "aws_apigatewayv2_route",
    "AWS::ApiGatewayV2::Stage": "aws_apigatewayv2_stage",
    "AWS::SQS::Queue": "aws_sqs_queue",
    "AWS::SQS::QueuePolicy": "aws_sqs_queue_policy",
    "AWS::SNS::Topic": "aws_sns_topic",
    "AWS::SNS::Subscription": "aws_sns_topic_subscription",
    "AWS::Kinesis::Stream": "aws_kinesis_stream",
    "AWS::StepFunctions::StateMachine": "aws_sfn_state_machine",
    "AWS::IAM::Role": "aws_iam_role",
    "AWS::IAM::Policy": "aws_iam_role_policy",
    "AWS::IAM::ManagedPolicy": "aws_iam_policy",
    "AWS::Logs::LogGroup": "aws_cloudwatch_log_group",
    "AWS::CloudFront::Distribution": "aws_cloudfront_distribution",
    "AWS::CDK::Metadata": "aws_cdk_metadata",
}

ALIASES: dict[str, dict[str, str]] = {
    "aws_dynamodb_table": {"table_name": "name"},
    "aws_s3_bucket": {"bucket_name": "bucket"},
    "aws_sfn_state_machine": {"state_machine_name": "name", "definition_string": "definition"},
    "aws_iam_role_policy": {"policy_document": "policy", "roles": "role", "policy_name": "name"},
    "aws_iam_policy": {"policy_document": "policy", "managed_policy_name": "name"},
    "aws_db_instance": {"db_instance_class": "instance_class", "db_instance_identifier": "identifier",
                        "vpc_security_groups": "vpc_security_group_ids"},
    "aws_db_subnet_group": {"db_subnet_group_name": "name"},
    "aws_lb": {"type": "load_balancer_type"},
    "aws_lb_listener": {"default_actions": "default_action"},
    "aws_ecs_service": {"load_balancers": "load_balancer"},
    "aws_sqs_queue": {"queue_name": "name"},
    "aws_sns_topic": {"topic_name": "name"},
    "aws_api_gateway_rest_api": {},
}

# Attributes that hold a resource's physical name (used by resolve_physical_names and labels).
NAME_ATTRS = ("function_name", "name", "bucket", "identifier")
# Resource types whose literal name in an env var counts as a reference.
NAMED_TARGET_TYPES = ("aws_dynamodb_table", "aws_s3_bucket", "aws_sqs_queue", "aws_sns_topic",
                      "aws_kinesis_stream", "aws_db_instance", "aws_rds_cluster", "aws_elasticache_cluster")

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def canonical_type(cfn_type: str) -> str:
    if cfn_type in CANONICAL_TYPES:
        return CANONICAL_TYPES[cfn_type]
    parts = cfn_type.split("::")[1:] or [cfn_type]
    return "aws_" + "_".join(snake_case(p) for p in parts)


def snake_case(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def canonical_attrs(ctype: str, properties: dict[str, Any]) -> dict[str, Any]:
    attrs = {snake_case(k): v for k, v in properties.items()}
    for source, target in ALIASES.get(ctype, {}).items():
        if source in attrs:
            attrs[target] = attrs.pop(source)
    if ctype == "aws_lambda_function" and isinstance(attrs.get("environment"), dict):
        attrs["environment"] = {snake_case(k): v for k, v in attrs["environment"].items()}
    if isinstance(attrs.get("tags"), list):
        attrs["tags"] = {t["Key"]: t.get("Value") for t in attrs["tags"] if isinstance(t, dict) and "Key" in t}
    if ctype == "aws_dynamodb_table" and isinstance(attrs.get("provisioned_throughput"), dict):
        pt = attrs.pop("provisioned_throughput")          # → Terraform's read_capacity / write_capacity
        attrs.setdefault("read_capacity", pt.get("ReadCapacityUnits"))
        attrs.setdefault("write_capacity", pt.get("WriteCapacityUnits"))
    return attrs


def synthetic_resources(raw: RawResource) -> list[RawResource]:
    """Terraform-shaped side resources that CloudFormation folds into a parent."""
    out: list[RawResource] = []
    logical_id = raw.address.split(".", 1)[1]

    if raw.type == "aws_api_gateway_method":
        integration = raw.attrs.get("integration")
        if isinstance(integration, dict) and integration.get("Uri"):
            out.append(RawResource(
                address=f"aws_api_gateway_integration.{logical_id}",
                type="aws_api_gateway_integration",
                attrs={
                    "rest_api_id": raw.attrs.get("rest_api_id"),
                    "resource_id": raw.attrs.get("resource_id"),
                    "http_method": raw.attrs.get("http_method"),
                    "type": integration.get("Type"),
                    "integration_http_method": integration.get("IntegrationHttpMethod"),
                    "uri": integration.get("Uri"),
                    "cfn_type": raw.attrs.get("cfn_type"),
                },
                region=raw.region, source_file=raw.source_file,
            ))

    if raw.type == "aws_lb_target_group":
        for i, target in enumerate(raw.attrs.get("targets") or []):
            if isinstance(target, dict) and target.get("Id"):
                out.append(RawResource(
                    address=f"aws_lb_target_group_attachment.{logical_id}_{i}",
                    type="aws_lb_target_group_attachment",
                    attrs={"target_group_arn": placeholder(raw.address, "arn"), "target_id": target["Id"],
                           "port": target.get("Port"), "cfn_type": raw.attrs.get("cfn_type")},
                    region=raw.region, source_file=raw.source_file,
                ))
    return out


def resolve_physical_names(resources: list[RawResource]) -> int:
    """Turn env-var values that equal another resource's physical name into
    placeholders. Returns how many were rewritten."""
    by_name: dict[str, tuple[str, str]] = {}
    for r in resources:
        if r.type not in NAMED_TARGET_TYPES:
            continue
        for attr in NAME_ATTRS:
            value = r.attrs.get(attr)
            if isinstance(value, str) and "${" not in value and len(value) >= 4:
                by_name.setdefault(value, (r.address, attr))

    rewritten = 0
    for r in resources:
        if r.type != "aws_lambda_function":
            continue
        env = (r.attrs.get("environment") or {}).get("variables")
        if not isinstance(env, dict):
            continue
        for key, value in env.items():
            if isinstance(value, str) and value in by_name and by_name[value][0] != r.address:
                address, attr = by_name[value]
                env[key] = placeholder(address, attr)
                rewritten += 1
    return rewritten
