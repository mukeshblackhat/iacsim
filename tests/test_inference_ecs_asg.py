"""WP2: ECS services reach their datastores through the task definition
(container env + task role), and an Auto Scaling group is a compute node with
N instances that an ALB routes to — on a tiny inline Terraform project."""

from pathlib import Path

from iacsim.core.config import load_config
from iacsim.core.models import EdgeKind, NodeKind
from iacsim.core.pipeline import build_graph

TF = '''
provider "aws" {
  region = "us-east-1"
}

resource "aws_vpc" "v" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "a" {
  vpc_id            = aws_vpc.v.id
  availability_zone = "us-east-1a"
  cidr_block        = "10.0.1.0/24"
}

resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  hash_key     = "id"
  billing_mode = "PAY_PER_REQUEST"
}

resource "aws_sqs_queue" "events" {
  name = "events"
}

resource "aws_iam_role" "task" {
  name               = "task"
  assume_role_policy = "{}"
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:GetItem"], Resource = [aws_dynamodb_table.orders.arn] },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = [aws_sqs_queue.events.arn] },
    ]
  })
}

resource "aws_ecs_cluster" "c" {
  name = "c"
}

resource "aws_ecs_task_definition" "web" {
  family        = "web"
  task_role_arn = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name        = "web"
    image       = "web:1"
    environment = [{ name = "TABLE_NAME", value = aws_dynamodb_table.orders.name }]
    secrets     = [{ name = "QUEUE_URL", valueFrom = aws_sqs_queue.events.url }]
  }])
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.c.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 3
  network_configuration {
    subnets = [aws_subnet.a.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 80
  }
}

resource "aws_lb" "lb" {
  name               = "lb"
  load_balancer_type = "application"
  subnets            = [aws_subnet.a.id]
}

resource "aws_lb_target_group" "web" {
  name     = "web"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.v.id
}

resource "aws_lb_target_group" "legacy" {
  name     = "legacy"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.v.id
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.lb.arn
  port              = 443
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener" "legacy" {
  load_balancer_arn = aws_lb.lb.arn
  port              = 8443
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.legacy.arn
  }
}

resource "aws_launch_template" "lt" {
  name          = "lt"
  instance_type = "t3.medium"
}

resource "aws_autoscaling_group" "legacy" {
  name                = "legacy"
  desired_capacity    = 4
  min_size            = 2
  max_size            = 8
  vpc_zone_identifier = [aws_subnet.a.id]
  target_group_arns   = [aws_lb_target_group.legacy.arn]
  launch_template {
    id = aws_launch_template.lt.id
  }
}
'''


def _graph(tmp_path: Path):
    (tmp_path / "main.tf").write_text(TF)
    return build_graph(tmp_path, load_config(tmp_path))


def test_ecs_service_edges_come_through_the_task_definition(tmp_path):
    graph, raw = _graph(tmp_path)
    assert graph.warnings == [] and raw.warnings == []
    svc = graph.nodes["aws_ecs_service.web"]
    assert svc.kind == NodeKind.COMPUTE and svc.subtype == "fargate" and svc.attrs["instances"] == 3
    assert "aws_ecs_task_definition.web" not in graph.nodes                 # glue, followed not shown
    table = graph.find_edge("aws_ecs_service.web", "aws_dynamodb_table.orders")
    assert table.kind == EdgeKind.READ and table.ops == [EdgeKind.READ, EdgeKind.WRITE]
    assert set(table.rule.split("+")) == {"env_var", "iam_policy"}
    assert "container_definitions TABLE_NAME" in table.evidence
    queue = graph.find_edge("aws_ecs_service.web", "aws_sqs_queue.events")
    assert queue.kind == EdgeKind.PUBLISH and set(queue.rule.split("+")) == {"env_var", "iam_policy"}
    assert graph.find_edge("aws_lb.lb", "aws_ecs_service.web").kind == EdgeKind.ROUTE


def test_autoscaling_group_is_a_compute_node_the_alb_routes_to(tmp_path):
    graph, _ = _graph(tmp_path)
    asg = graph.nodes["aws_autoscaling_group.legacy"]
    assert asg.kind == NodeKind.COMPUTE and asg.subtype == "ec2"
    assert asg.attrs["instances"] == 4 and asg.label == "legacy"
    assert asg.placement.az == "us-east-1a"
    route = graph.find_edge("aws_lb.lb", "aws_autoscaling_group.legacy")
    assert route.kind == EdgeKind.ROUTE and "legacy" in route.evidence
    assert "aws_launch_template.lt" not in graph.nodes
