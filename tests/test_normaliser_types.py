"""The AWS normaliser's type table is Terraform-only: every CloudFormation type
arrives canonicalised to its `aws_*` spelling first, so the `AWS::…` keys it used
to carry were unreachable (WP8 removed them). This pins the contract: each of
those CloudFormation types still lands on a mapped or deliberately-ignored
Terraform type."""

import pytest

from iacsim.graph.normalisers.aws import IGNORED_PREFIXES, TYPE_MAP
from iacsim.parsers.cloudformation.canonical import canonical_type

# The CloudFormation types the normaliser once listed directly (TYPE_MAP + IGNORED_PREFIXES).
FORMER_CFN_KEYS = [
    "AWS::Lambda::Function", "AWS::EC2::Instance", "AWS::ECS::Service", "AWS::DynamoDB::Table",
    "AWS::RDS::DBInstance", "AWS::S3::Bucket", "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::ApiGateway::RestApi", "AWS::ApiGatewayV2::Api", "AWS::CloudFront::Distribution",
    "AWS::SQS::Queue", "AWS::SNS::Topic", "AWS::StepFunctions::StateMachine", "AWS::EC2::VPC",
    "AWS::EC2::Subnet",
    # glue that was matched by prefix
    "AWS::IAM::Role", "AWS::IAM::Policy", "AWS::EC2::SecurityGroup",
    "AWS::ElasticLoadBalancingV2::TargetGroup", "AWS::ElasticLoadBalancingV2::Listener",
    "AWS::ApiGateway::Method", "AWS::ApiGateway::Deployment", "AWS::Lambda::Permission",
    "AWS::Lambda::EventSourceMapping", "AWS::S3::BucketPolicy", "AWS::Logs::LogGroup", "AWS::CDK::Metadata",
]


@pytest.mark.parametrize("cfn_type", FORMER_CFN_KEYS)
def test_every_former_cfn_key_still_lands_on_a_terraform_type(cfn_type):
    tf_type = canonical_type(cfn_type)
    assert tf_type.startswith("aws_"), tf_type
    assert tf_type in TYPE_MAP or tf_type.startswith(IGNORED_PREFIXES), f"{cfn_type} → {tf_type} is unmapped"


def test_normaliser_table_has_no_cloudformation_keys():
    assert not [k for k in TYPE_MAP if "::" in k]
    assert not [p for p in IGNORED_PREFIXES if "::" in p]
