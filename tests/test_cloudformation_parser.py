"""CloudFormation parser unit tests: intrinsics → placeholders, YAML short tags,
canonical (Terraform-shaped) types and attrs, synthetic resources, physical-name
resolution, DefinitionString reassembly, and that the shipped example is redacted."""

import json
import re
from pathlib import Path

import pytest

from iacsim.core.refs import addresses_in
from iacsim.parsers.cloudformation.canonical import canonical_type, snake_case
from iacsim.parsers.cloudformation.parser import CloudFormationParser

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def parse(tmp_path: Path, template, name="stack.template.json", **options):
    file = tmp_path / name
    file.write_text(template if isinstance(template, str) else json.dumps(template))
    return CloudFormationParser(**options).parse(file)


def by_address(raw):
    return {r.address: r for r in raw.resources}


SMALL = {
    "AWSTemplateFormatVersion": "2010-09-09",
    "Parameters": {"Stage": {"Type": "String", "Default": "prod"}, "NoDefault": {"Type": "String"}},
    "Resources": {
        "Orders": {"Type": "AWS::DynamoDB::Table", "Properties": {"TableName": "orders-prod"}},
        "Fn": {"Type": "AWS::Lambda::Function", "Properties": {
            "FunctionName": {"Fn::Sub": "svc-${Stage}"},
            "MemorySize": 512, "Timeout": 30, "Runtime": "python3.12",
            "Role": {"Fn::GetAtt": ["Role", "Arn"]},
            "Environment": {"Variables": {
                "TABLE": {"Ref": "Orders"},
                "TABLE_ARN": {"Fn::Sub": "${Orders.Arn}/index/*"},
                "BY_NAME": "orders-prod",
                "LITERAL": {"Fn::Sub": "${!NotAVar}"},
                "REGION": {"Ref": "AWS::Region"},
                "MISSING": {"Ref": "NoDefault"},
            }},
        }},
        "Role": {"Type": "AWS::IAM::Role", "Properties": {"AssumeRolePolicyDocument": {}}},
        "Policy": {"Type": "AWS::IAM::Policy", "Properties": {
            "Roles": [{"Ref": "Role"}],
            "PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "dynamodb:PutItem",
                                              "Resource": {"Fn::GetAtt": ["Orders", "Arn"]}}]},
        }},
        "Api": {"Type": "AWS::ApiGateway::RestApi", "Properties": {"Name": "svc-api"}},
        "Proxy": {"Type": "AWS::ApiGateway::Resource", "Properties": {
            "RestApiId": {"Ref": "Api"}, "ParentId": {"Fn::GetAtt": ["Api", "RootResourceId"]},
            "PathPart": "{proxy+}"}},
        "Method": {"Type": "AWS::ApiGateway::Method", "Properties": {
            "RestApiId": {"Ref": "Api"}, "ResourceId": {"Ref": "Proxy"}, "HttpMethod": "ANY",
            "Integration": {"Type": "AWS_PROXY", "Uri": {"Fn::Join": ["", [
                "arn:", {"Ref": "AWS::Partition"}, ":apigateway:", {"Ref": "AWS::Region"},
                ":lambda:path/2015-03-31/functions/", {"Fn::GetAtt": ["Fn", "Arn"]}, "/invocations"]]}}}},
        "Options": {"Type": "AWS::ApiGateway::Method", "Properties": {
            "RestApiId": {"Ref": "Api"}, "ResourceId": {"Ref": "Proxy"}, "HttpMethod": "OPTIONS",
            "Integration": {"Type": "MOCK"}}},
        "Tg": {"Type": "AWS::ElasticLoadBalancingV2::TargetGroup", "Properties": {
            "Targets": [{"Id": {"Ref": "Web"}, "Port": 8080}], "VpcId": "vpc-1"}},
        "Web": {"Type": "AWS::EC2::Instance", "Properties": {
            "AvailabilityZone": {"Fn::Select": [0, {"Fn::GetAZs": ""}]},
            "Tags": [{"Key": "Name", "Value": "web"}],
            "Monitoring": {"Fn::If": ["IsProd", True, False]}}},
        "Odd": {"Type": "AWS::Foo::BarBaz", "Properties": {}},
    },
}


def test_addresses_and_types_are_terraform_shaped(tmp_path):
    raw = by_address(parse(tmp_path, SMALL))
    assert raw["aws_dynamodb_table.Orders"].type == "aws_dynamodb_table"
    assert raw["aws_dynamodb_table.Orders"].attrs["cfn_type"] == "AWS::DynamoDB::Table"
    assert raw["aws_lambda_function.Fn"].attrs["memory_size"] == 512     # PascalCase → snake_case
    assert raw["aws_dynamodb_table.Orders"].attrs["name"] == "orders-prod"  # TableName → name alias
    assert raw["aws_iam_role_policy.Policy"].attrs["policy"]["Statement"][0]["Action"] == "dynamodb:PutItem"
    assert canonical_type("AWS::Foo::BarBaz") == "aws_foo_bar_baz" and "aws_foo_bar_baz.Odd" in raw
    assert snake_case("DBSubnetGroupName") == "db_subnet_group_name"


def test_intrinsics_become_placeholders(tmp_path):
    raw = parse(tmp_path, SMALL, region="eu-west-1")
    env = by_address(raw)["aws_lambda_function.Fn"].attrs["environment"]["variables"]
    assert env["TABLE"] == "${aws_dynamodb_table.Orders.id}"                       # Ref
    assert env["TABLE_ARN"] == "${aws_dynamodb_table.Orders.arn}/index/*"          # Fn::Sub ${X.Attr}
    assert env["BY_NAME"] == "${aws_dynamodb_table.Orders.name}"                   # literal physical name
    assert env["LITERAL"] == "${NotAVar}"                                          # Fn::Sub ${!x} escape
    assert env["REGION"] == "eu-west-1"                                            # pseudo parameter, from option
    assert env["MISSING"] == "<NoDefault>"                                         # parameter without Default
    fn = by_address(raw)["aws_lambda_function.Fn"]
    assert fn.attrs["function_name"] == "svc-prod"                                 # parameter Default
    assert fn.attrs["role"] == "${aws_iam_role.Role.arn}"                          # Fn::GetAtt
    assert "aws_dynamodb_table.Orders" in fn.references and "aws_iam_role.Role" in fn.references
    assert fn.region == "eu-west-1"


def test_join_select_if_and_warnings(tmp_path):
    raw = parse(tmp_path, SMALL)
    web = by_address(raw)["aws_instance.Web"]
    assert web.attrs["availability_zone"] == "us-east-1a"            # Select 0 of GetAZs (default region)
    assert web.attrs["monitoring"] is True                           # Fn::If → true branch
    assert web.attrs["tags"] == {"Name": "web"}                      # Tags list → dict
    assert any("Fn::If" in w and "IsProd" in w for w in raw.warnings)
    assert any("NoDefault" in w for w in raw.warnings)
    integ = by_address(raw)["aws_api_gateway_integration.Method"]
    assert integ.attrs["uri"] == ("arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/"
                                  "${aws_lambda_function.Fn.arn}/invocations")


def test_synthetic_integration_and_attachment(tmp_path):
    raw = by_address(parse(tmp_path, SMALL))
    integ = raw["aws_api_gateway_integration.Method"]
    assert integ.attrs["rest_api_id"] == "${aws_api_gateway_rest_api.Api.id}"
    assert integ.attrs["resource_id"] == "${aws_api_gateway_resource.Proxy.id}"
    assert integ.attrs["http_method"] == "ANY"
    assert "aws_api_gateway_integration.Options" not in raw                       # MOCK has no Uri
    att = raw["aws_lb_target_group_attachment.Tg_0"]
    assert att.attrs["target_group_arn"] == "${aws_lb_target_group.Tg.arn}"
    assert att.attrs["target_id"] == "${aws_instance.Web.id}"


def test_region_is_guessed_from_arns_in_the_template(tmp_path):
    t = {"Resources": {"P": {"Type": "AWS::Lambda::Permission", "Properties": {
        "SourceArn": "arn:aws:execute-api:ap-south-1:123:abc/*/*", "Principal": "apigateway.amazonaws.com",
        "FunctionName": "x", "Action": "lambda:InvokeFunction"}}}}
    assert parse(tmp_path, t).resources[0].region == "ap-south-1"


def test_yaml_short_tags(tmp_path):
    yaml_text = """
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  Q:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: jobs
  Fn:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub "worker-${AWS::Region}"
      Role: !GetAtt Role.Arn
      Environment:
        Variables:
          QUEUE_URL: !Ref Q
          QUEUE_ARN: !Join ["", [!GetAtt Q.Arn, "/x"]]
          PICK: !Select [1, ["a", "b"]]
  Role:
    Type: AWS::IAM::Role
    Properties: {}
"""
    raw = by_address(parse(tmp_path, yaml_text, name="template.yaml"))
    env = raw["aws_lambda_function.Fn"].attrs["environment"]["variables"]
    assert env["QUEUE_URL"] == "${aws_sqs_queue.Q.id}"
    assert env["QUEUE_ARN"] == "${aws_sqs_queue.Q.arn}/x"
    assert env["PICK"] == "b"
    assert raw["aws_lambda_function.Fn"].attrs["function_name"] == "worker-us-east-1"
    assert raw["aws_lambda_function.Fn"].attrs["role"] == "${aws_iam_role.Role.arn}"
    assert raw["aws_sqs_queue.Q"].attrs["name"] == "jobs"


def test_definition_string_is_reassembled_into_json_with_placeholders(tmp_path):
    t = {"Resources": {
        "W": {"Type": "AWS::Lambda::Function", "Properties": {"FunctionName": "w"}},
        "Sm": {"Type": "AWS::StepFunctions::StateMachine", "Properties": {
            "StateMachineName": "sm",
            "DefinitionString": {"Fn::Join": ["", [
                '{"StartAt":"A","States":{"A":{"Type":"Task","Resource":"arn:', {"Ref": "AWS::Partition"},
                ':states:::lambda:invoke","Parameters":{"FunctionName":"', {"Fn::GetAtt": ["W", "Arn"]},
                '","Payload.$":"$"},"End":true}}}']]}}}}}
    sm = by_address(parse(tmp_path, t))["aws_sfn_state_machine.Sm"]
    definition = json.loads(sm.attrs["definition"])                              # valid JSON again
    task = definition["States"]["A"]
    assert task["Resource"] == "arn:aws:states:::lambda:invoke"
    assert task["Parameters"]["FunctionName"] == "${aws_lambda_function.W.arn}"
    assert addresses_in(task["Parameters"]) == ["aws_lambda_function.W"]
    assert sm.attrs["name"] == "sm"


def test_detects_file_and_directory():
    assert CloudFormationParser.detect(EXAMPLES / "foosh-cfn")
    assert CloudFormationParser.detect(EXAMPLES / "foosh-cfn" / "template.json")
    assert not CloudFormationParser.detect(EXAMPLES / "classic-web")


def test_shipped_example_has_no_secrets():
    t = json.loads((EXAMPLES / "foosh-cfn" / "template.json").read_text())
    secret = re.compile(r"KEY|SECRET|TOKEN|PASSWORD", re.IGNORECASE)
    checked = 0
    for r in t["Resources"].values():
        for k, v in r.get("Properties", {}).get("Environment", {}).get("Variables", {}).items():
            if secret.search(k):
                assert v == "REDACTED", k
                checked += 1
    assert checked > 0
    assert "686410906351" not in json.dumps(t)                                   # real account id replaced


@pytest.mark.parametrize("cfn,tf", [
    ("AWS::Lambda::Function", "aws_lambda_function"),
    ("AWS::StepFunctions::StateMachine", "aws_sfn_state_machine"),
    ("AWS::IAM::Policy", "aws_iam_role_policy"),
    ("AWS::ApiGateway::Method", "aws_api_gateway_method"),
])
def test_canonical_type_table(cfn, tf):
    assert canonical_type(cfn) == tf
