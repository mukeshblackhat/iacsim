"""Shared fixtures: each example parsed once per session through the real pipeline."""

from pathlib import Path

import pytest

from iacsim.core.config import load_config
from iacsim.core.pipeline import build_graph
from iacsim.core.registry import load_builtin_plugins

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="session", autouse=True)
def _plugins():
    load_builtin_plugins()


def _build(name: str):
    target = EXAMPLES / name
    graph, raw = build_graph(target, load_config(target))
    return graph, raw


@pytest.fixture(scope="session")
def classic_web():
    return _build("classic-web")


@pytest.fixture(scope="session")
def classic_web_bad():
    return _build("classic-web-bad")


@pytest.fixture(scope="session")
def foosh():
    return _build("foosh-serverless")


@pytest.fixture(scope="session")
def order_queue():
    """API GW → Lambda → SQS → Lambda (event source mapping) → DynamoDB (WP2)."""
    return _build("order-queue")


@pytest.fixture(scope="session")
def foosh_cfn():
    """The real CDK-synthesized template of the same stack (M5)."""
    return _build("foosh-cfn")


def edge(graph, src: str, dst: str):
    e = graph.find_edge(src, dst)
    assert e is not None, f"missing edge {src} → {dst}; have: " + "\n".join(f"{x.src} → {x.dst}" for x in graph.edges)
    return e


def _run(name: str):
    from iacsim.core.pipeline import run
    target = EXAMPLES / name
    return run(target, load_config(target))


@pytest.fixture(scope="session")
def classic_web_run():
    return _run("classic-web")


@pytest.fixture(scope="session")
def classic_web_bad_run():
    return _run("classic-web-bad")


@pytest.fixture(scope="session")
def foosh_run():
    return _run("foosh-serverless")


@pytest.fixture(scope="session")
def order_queue_run():
    return _run("order-queue")


def result(output, scenario: str):
    return next(r for r in output.results if r.scenario == scenario)


def example_copy(name: str, dest: Path) -> Path:
    """A throwaway copy of an example so tests never write into examples/*/.iacsim/.
    Module sources (`../modules/...`) are rewritten to absolute paths."""
    import shutil
    src = EXAMPLES / name
    target = dest / name
    shutil.copytree(src, target, ignore=shutil.ignore_patterns(".iacsim", "__pycache__"))
    for f in target.rglob("*.tf"):
        f.write_text(f.read_text().replace('"../modules/', f'"{EXAMPLES / "modules"}/'))
    for f in target.rglob("iacsim.yaml"):
        f.write_text(f.read_text().replace("../foosh-serverless/", f"{EXAMPLES / 'foosh-serverless'}/"))
    return target


# ---------------------------------------------------------------- hand-built graph

def tiny_graph(**overrides):
    """A small costed-ready graph covering every hop shape the traversal handles:

        internet → gw → fn → table            (READ)
                        fn → sfn → w1 → table (orchestrator, workers)
                             sfn → w2
        lb → web → db                         (ROUTE, then READ)

    All nodes are in us-east-1a unless overridden: `db_region="eu-west-1"`,
    `placements={"fn": Placement()}` (unknown region), etc. WP8 migrates the
    per-module `_graph()` builders onto this.
    """
    from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, Node, NodeKind, Placement

    def place(region="us-east-1", az="us-east-1a"):
        return Placement(region=region, az=az)

    db_region = overrides.get("db_region", "us-east-1")
    placements = overrides.get("placements", {})
    nodes = [
        Node("internet", NodeKind.EXTERNAL, "internet"),
        Node("gw", NodeKind.GATEWAY, "api_gateway", place(az=None), label="gw"),
        Node("fn", NodeKind.COMPUTE, "lambda", place(az=None), label="fn"),
        Node("table", NodeKind.DATASTORE, "dynamodb", place(az=None), label="table"),
        Node("sfn", NodeKind.ORCHESTRATOR, "step_functions", place(az=None), label="sfn"),
        Node("w1", NodeKind.COMPUTE, "lambda", place(az=None), label="w1"),
        Node("w2", NodeKind.COMPUTE, "lambda", place(az=None), label="w2"),
        Node("lb", NodeKind.LB, "alb", place(az=None), label="lb"),
        Node("web", NodeKind.COMPUTE, "ec2", place(), label="web"),
        Node("db", NodeKind.DATASTORE, "rds", place(region=db_region, az=f"{db_region}a"), label="db"),
    ]
    g = InfraGraph()
    for n in nodes:
        if n.id in placements:
            n.placement = placements[n.id]
        g.add_node(n)
    for src, dst, kind in [("internet", "gw", EdgeKind.INVOKE), ("internet", "lb", EdgeKind.INVOKE),
                           ("gw", "fn", EdgeKind.INVOKE), ("fn", "table", EdgeKind.READ),
                           ("fn", "sfn", EdgeKind.INVOKE), ("sfn", "w1", EdgeKind.INVOKE),
                           ("sfn", "w2", EdgeKind.INVOKE), ("w1", "table", EdgeKind.READ),
                           ("lb", "web", EdgeKind.ROUTE), ("web", "db", EdgeKind.READ)]:
        g.add_edge(Edge(src, dst, kind, Confidence.HIGH, f"{src} calls {dst}"))
    return g


@pytest.fixture(scope="session")
def default_profile():
    """The built-in defaults.yaml as a Profile (rung 0)."""
    from iacsim.core.config import Config
    from iacsim.core.pipeline import load_profile
    return load_profile(Config())
