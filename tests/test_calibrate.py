"""Calibration without any cloud: fake source, calibrator mapping, writer round-trip,
profile precedence and the rung label in report headers."""

from pathlib import Path

import pytest
import yaml

from iacsim.core.config import Config
from iacsim.core.interfaces import METRIC_SOURCES, PROFILE_SOURCES, MetricSource
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, Node, NodeKind, Placement
from iacsim.latency.calibrate import make_metric_source
from iacsim.latency.calibrate.calibrator import calibrate
from iacsim.latency.calibrate.fake import FakeMetricSource
from iacsim.latency.calibrate.writer import write_profile
from iacsim.latency.profile import describe_layer, merge_profiles

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FIXTURE = EXAMPLES / "foosh-serverless" / "calibrate-fixture.yaml"


def _graph() -> InfraGraph:
    g = InfraGraph()
    us = Placement(region="us-east-1")
    g.add_node(Node("fn", NodeKind.COMPUTE, "lambda", us, label="my-fn"))
    g.add_node(Node("fn2", NodeKind.COMPUTE, "lambda", us, label="idle-fn"))
    g.add_node(Node("fn3", NodeKind.COMPUTE, "lambda", us, label=None))
    g.add_node(Node("tbl", NodeKind.DATASTORE, "dynamodb", us, label="orders"))
    g.add_node(Node("db", NodeKind.DATASTORE, "rds", us, label="pg"))
    g.add_node(Node("bucket", NodeKind.DATASTORE, "s3", us, label="files"))
    g.add_edge(Edge("fn", "tbl", EdgeKind.READ, Confidence.DECLARED, "test"))
    return g


def _source(**data):
    """`lambda` is a keyword, so callers pass lambda_=…"""
    return FakeMetricSource(data={k.rstrip("_"): v for k, v in data.items()})


# ------------------------------------------------------------------ fake source

def test_fake_source_from_fixture_and_from_data():
    src = FakeMetricSource(fixture=str(FIXTURE))
    assert src.supports("lambda") and src.supports("dynamodb") and not src.supports("rds")
    assert src.measure("lambda", "async-workflow-parser-staging", "7d")["cold_prob"] == 0.3
    assert src.measure("lambda", "async-workflow-router-staging", "7d") is None
    assert src.describe() == {"fixture": str(FIXTURE)}
    assert _source(rds={"pg": {"read": 1}}).measure("rds", "pg", "1d") == {"read": 1}
    relative = FakeMetricSource(fixture="calibrate-fixture.yaml")
    assert not relative.supports("lambda")            # not loaded until prepare(root)
    relative.prepare(FIXTURE.parent)
    assert relative.supports("lambda")
    with pytest.raises(FileNotFoundError):
        FakeMetricSource(fixture="nope.yaml").prepare(Path("/nowhere"))


def test_make_metric_source_passes_config_options_and_drops_none():
    cfg = Config()
    cfg.set("calibrate.source", "fake")
    cfg.set("calibrate.sources.fake.fixture", "calibrate-fixture.yaml")
    src = make_metric_source(cfg, root=FIXTURE.parent)        # prepare(root) resolves the relative path
    assert isinstance(src, FakeMetricSource) and src.supports("lambda")
    cfg.set("calibrate.sources.fake.fixture", None)
    assert make_metric_source(cfg).options["fixture"] is None and not make_metric_source(cfg).supports("lambda")


def test_a_plugin_source_is_just_a_registered_class():
    @METRIC_SOURCES.register("test_datadog")
    class Datadog(MetricSource):
        def supports(self, kind):
            return kind == "lambda"

        def measure(self, kind, name, window, region=None):
            return {"warm": 7}
    cfg = Config()
    cfg.set("calibrate.source", "test_datadog")
    cfg.set("calibrate.sources.test_datadog", {"site": "eu", "api_key_env": "DD_API_KEY"})
    src = make_metric_source(cfg)
    assert src.options == {"site": "eu", "api_key_env": "DD_API_KEY"} and src.describe() == {}
    result = calibrate(_graph(), src, "1d", fmt="terraform")
    assert result.meta["source"] == "test_datadog" and "fn" in result.covered


# ------------------------------------------------------------------ calibrator

def test_calibrator_writes_complete_blocks_by_id_and_label_and_reports_skips():
    src = _source(lambda_={"my-fn": {"warm": 12}}, dynamodb={"orders": {"read": 2.5, "write": 4}})
    result = calibrate(_graph(), src, "7d", fmt="terraform")
    lam = result.profile["processing"]["lambda"]
    assert lam["by_label"]["my-fn"] == {"warm": 12, "cold": 400, "cold_prob": 0.05}   # complete block
    assert "per_resource" not in lam                                                  # unique label → by_label only
    assert result.filled["fn"] == ["cold", "cold_prob"] and result.filled["tbl"] == []
    assert result.profile["processing"]["dynamodb"]["by_label"]["orders"] == {"read": 2.5, "write": 4}
    assert result.covered == ["fn", "tbl"]
    assert dict(result.skipped) == {"fn2": "no data in window", "fn3": "no physical name",
                                    "db": "source does not support rds"}
    assert "bucket" not in dict(result.skipped)          # s3 is not a metric kind at all
    assert result.meta["covered"] == 2 and result.meta["nodes_seen"] == 5
    assert result.meta["region"] == "us-east-1" and result.meta["format"] == "terraform"
    assert list(result.profile) == ["meta", "processing"] and "variance" not in result.profile


def test_variance_sigma_is_the_median_of_at_least_three_measured_sigmas():
    g = InfraGraph()
    for i, sigma in enumerate((0.2, 0.9, 0.4)):
        g.add_node(Node(f"f{i}", NodeKind.COMPUTE, "lambda", Placement(region="eu-west-1"), label=f"f{i}"))
    data = {f"f{i}": {"warm": 1, "sigma": s} for i, s in enumerate((0.2, 0.9, 0.4))}
    result = calibrate(g, _source(**{"lambda": data}), "1d", fmt="terraform")
    assert result.profile["variance"] == {"processing_sigma": 0.4}
    assert list(result.profile) == ["meta", "variance", "processing"]
    two = calibrate(g, _source(**{"lambda": {k: v for k, v in list(data.items())[:2]}}), "1d", fmt="terraform")
    assert "variance" not in two.profile


def test_duplicate_labels_fall_back_to_per_resource():
    g = InfraGraph()
    for i in range(2):
        g.add_node(Node(f"env{i}.fn", NodeKind.COMPUTE, "lambda", Placement(region="us-east-1"), label="shared-name"))
    result = calibrate(g, _source(lambda_={"shared-name": {"warm": 9}}), "1d", fmt="terraform")
    lam = result.profile["processing"]["lambda"]
    assert "by_label" not in lam and set(lam["per_resource"]) == {"env0.fn", "env1.fn"}


def test_calibrator_on_the_real_foosh_graph(foosh):
    graph, _ = foosh
    result = calibrate(graph, FakeMetricSource(fixture=str(FIXTURE)), "7d", fmt="terraform")
    assert len(result.covered) == 26 and len(result.skipped) == 2
    assert {r for _, r in result.skipped} == {"no data in window"}
    sfn = result.profile["processing"]["step_functions"]["by_label"]["AsyncWorkflowStagingStateMachine"]
    assert sfn == {"transition": 30}
    assert result.filled["module.worker[\"text_input\"].aws_lambda_function.this"] == ["cold", "cold_prob"]
    assert result.meta["fixture"] == str(FIXTURE)
    assert list(result.profile["processing"]) == ["lambda", "dynamodb", "api_gateway", "step_functions"]


# ------------------------------------------------------------------ writer + read-back

def test_writer_round_trips_through_the_profile_source_in_order(tmp_path):
    src = _source(lambda_={"my-fn": {"warm": 12, "sigma": 0.3}}, dynamodb={"orders": {"read": 2}})
    result = calibrate(_graph(), src, "7d", fmt="terraform")
    path = write_profile(result, tmp_path / "sub" / "measured.yaml")
    text = path.read_text()
    assert text.startswith("# generated by iacsim calibrate — rung 2")
    assert "source=fake" in text and "format=terraform" in text and "2 of 5" in text
    loaded = PROFILE_SOURCES.get("yaml_file")().load(str(path))
    assert loaded == result.profile
    assert list(loaded) == ["meta", "processing"]
    assert list(loaded["processing"]) == ["lambda", "dynamodb"]
    assert yaml.safe_load(text)["meta"]["window"] == "7d"


# ------------------------------------------------------------------ profile lookup + rung label

def test_processing_for_precedence_defaults_then_label_then_id():
    node = Node("fn", NodeKind.COMPUTE, "lambda", Placement(), label="my-fn")
    layers = [("defaults", PROFILE_SOURCES.get("defaults")().load("defaults")),
              ("cal", {"processing": {"lambda": {"by_label": {"my-fn": {"warm": 20, "cold": 900}},
                                                 "per_resource": {"fn": {"warm": 30}}}}})]
    block = merge_profiles(layers).processing_for(node)
    assert block == {"warm": 30, "cold": 900, "cold_prob": 0.05}
    unlabelled = Node("fn", NodeKind.COMPUTE, "lambda", Placement())
    assert merge_profiles(layers).processing_for(unlabelled)["warm"] == 30
    assert merge_profiles(layers).processing_for(Node("other", NodeKind.COMPUTE, "lambda", Placement()))["warm"] == 5


def test_rung_label_shows_source_and_window_only_for_measured_layers():
    assert describe_layer("team.yaml", {"processing": {}}) == "team.yaml"
    assert describe_layer("defaults", {"meta": {"source": "defaults"}}) == "defaults"
    assert describe_layer("m.yaml", {"meta": {"source": "cloudwatch", "window": "7d"}}) == "m.yaml (cloudwatch, 7d)"
    assert describe_layer("m.yaml", {"meta": {"source": "manual"}}) == "m.yaml"
    assert describe_layer("m.yaml", {"meta": {"source": "m.yaml"}}) == "m.yaml"
    assert describe_layer("m.yaml", {"meta": {"source": "datadog"}}) == "m.yaml (datadog)"
    profile = merge_profiles([("defaults", {"meta": {"source": "defaults"}}),
                              ("m.yaml", {"meta": {"source": "fake", "window": "24h"}})])
    assert profile.sources == ["defaults", "m.yaml (fake, 24h)"]
