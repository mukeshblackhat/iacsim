"""Real-world Terraform must not crash, and every warning must be a named one.

Fixtures under examples/real-world/ are unmodified public projects (see each
ATTRIBUTION.md). The table below is the contract per fixture: the minimum node
count the parser must find, and the warning phrases that are allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import EXAMPLES, example_copy
from typer.testing import CliRunner

from iacsim.cli import app
from iacsim.core.config import load_config
from iacsim.core.models import EdgeKind
from iacsim.core.pipeline import build_graph

REAL_WORLD = EXAMPLES / "real-world"

KNOWN_WARNINGS = (
    "data.* sources are not evaluated",
    "remote source",
    "unknown type",
    "region not resolved",
    "region unknown",
    "duplicate resource",
    "count not resolved",
    "for_each not resolved",
    "dynamic",
    "not evaluated",
)

# fixture → (minimum non-network nodes, region flag needed)
MIN_NODES = {
    "serverless-apigw-lambda-dynamodb": 4,
    "serverless-pipes-sqs-stepfunctions": 3,
    "ecs-alb": 3,
    "two-tier": 2,
    "eks-cluster": 0,
}


def _fixtures() -> list[Path]:
    return sorted(p for p in REAL_WORLD.iterdir() if p.is_dir())


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda p: p.name)
def test_graph_builds_and_every_warning_is_named(fixture: Path):
    cfg = load_config(fixture, {"parsers.terraform.region": "us-east-1"})
    graph, _ = build_graph(fixture, cfg)
    real_nodes = [n for n in graph.nodes.values() if n.kind not in ("network", "external")]
    assert len(real_nodes) >= MIN_NODES[fixture.name], [n.id for n in real_nodes]
    unknown = [w for w in graph.warnings if not any(phrase in w for phrase in KNOWN_WARNINGS)]
    assert unknown == [], unknown


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda p: p.name)
def test_run_exits_zero(fixture: Path, tmp_path):
    copy = example_copy(f"real-world/{fixture.name}", tmp_path)
    result = CliRunner().invoke(app, ["run", str(copy), "--region", "us-east-1", "-o", "json"])
    assert result.exit_code == 0, result.output
    assert (copy / ".iacsim" / "report.json").is_file()


def test_ecs_alb_routes_to_the_service_and_sees_the_asg():
    fixture = REAL_WORLD / "ecs-alb"
    graph, _ = build_graph(fixture, load_config(fixture, {"parsers.terraform.region": "us-east-1"}))
    alb = next(n.id for n in graph.nodes.values() if n.subtype == "alb")
    service = next(n.id for n in graph.nodes.values() if n.subtype == "fargate")
    asg = graph.nodes["aws_autoscaling_group.app"]
    edge = graph.find_edge(alb, service)
    assert edge is not None and edge.kind == EdgeKind.ROUTE and edge.rule == "target_group"
    assert asg.subtype == "ec2" and asg.attrs.get("instances")  # desired_capacity from the ASG


def test_two_tier_classic_elb_routes_to_the_instance():
    fixture = REAL_WORLD / "two-tier"
    graph, _ = build_graph(fixture, load_config(fixture))
    edge = graph.find_edge("aws_elb.web", "aws_instance.web")
    assert edge is not None and edge.kind == EdgeKind.ROUTE
    assert graph.warnings == []          # provisioner/connection blocks are skipped, tfvars template ignored


def test_eks_cluster_names_every_registry_module_once():
    fixture = REAL_WORLD / "eks-cluster"
    graph, _ = build_graph(fixture, load_config(fixture))
    remote = [w for w in graph.warnings if "remote source" in w]
    assert len(remote) == 3 and all("terraform init" in w for w in remote)
