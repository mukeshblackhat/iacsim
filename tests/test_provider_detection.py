"""G1: with no `provider:` the pipeline picks the normaliser by majority
resource-type prefix — each normaliser's own `PREFIXES` — warns on a mixed
directory, and an explicit `provider:` or `--provider` still wins. Plus the
G21 `display_name` generalisation: the prefix stripped is whichever cloud's."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iacsim.cli import app
from iacsim.core.config import DEFAULTS, load_config
from iacsim.core.interfaces import NORMALISERS
from iacsim.core.models import InfraGraph, Node, NodeKind, Placement, RawResource, RawResources
from iacsim.core.pipeline import AUTO_PROVIDER, build_graph, detect_provider

ROOT = Path(__file__).resolve().parent.parent
GCP = ROOT / "tests" / "fixtures" / "gcp"       # 5 google_* resources and 1 aws_s3_bucket, no iacsim.yaml
runner = CliRunner()


def _raw(*types: str) -> RawResources:
    return RawResources([RawResource(address=f"{t}.r{i}", type=t, attrs={}) for i, t in enumerate(types)],
                        "terraform")


# ------------------------------------------------------------------ detect_provider

def test_the_prefix_table_lives_on_the_normalisers_not_in_the_engine():
    assert NORMALISERS.get("aws").PREFIXES == ("aws_",)
    assert NORMALISERS.get("gcp").PREFIXES == ("google_",)
    assert DEFAULTS["provider"] == AUTO_PROVIDER


def test_all_google_picks_gcp_all_aws_picks_aws_without_a_warning():
    assert detect_provider(_raw("google_cloud_run_v2_service", "google_sql_database_instance")) == ("gcp", None)
    assert detect_provider(_raw("aws_lambda_function", "aws_dynamodb_table")) == ("aws", None)


def test_nothing_to_vote_falls_back_to_aws():
    assert detect_provider(_raw()) == ("aws", None)
    # non-cloud and unmapped prefixes do not vote: still the fallback, still no warning
    assert detect_provider(_raw("random_id", "tls_private_key", "archive_file", "null_resource",
                                "kubernetes_deployment", "azurerm_function_app")) == ("aws", None)
    # ...and they do not dilute a real majority either
    assert detect_provider(_raw("random_id", "kubernetes_deployment", "google_cloud_run_v2_service")) == ("gcp", None)


def test_mixed_directory_picks_the_majority_and_warns_with_both_counts():
    name, warning = detect_provider(_raw("google_cloud_run_v2_service", "google_compute_instance",
                                         "google_sql_database_instance", "aws_s3_bucket", "aws_sqs_queue"))
    assert name == "gcp"
    assert "normalised as gcp (3 google_* resources)" in warning
    assert "2 aws_* resource(s) left to the unknown-type path" in warning
    assert "--provider" in warning and "`provider:`" in warning
    name, warning = detect_provider(_raw("aws_lambda_function", "aws_sqs_queue", "google_storage_bucket"))
    assert name == "aws" and "normalised as aws (2 aws_* resources); 1 google_* resource(s)" in warning


def test_a_tie_is_aws():
    assert detect_provider(_raw("aws_lambda_function", "google_cloud_run_v2_service"))[0] == "aws"


# ------------------------------------------------------------------ through the pipeline

def test_gcp_fixture_end_to_end_with_no_config_and_no_flag():
    cfg = load_config(GCP)
    assert cfg.get("provider") == AUTO_PROVIDER
    graph, _ = build_graph(GCP, cfg)
    assert {n.subtype for n in graph.nodes.values()} >= {"cloud_run", "gce", "cloud_sql"}
    assert graph.nodes["google_cloud_run_v2_service.api"].placement.region == "us-central1"
    assert graph.nodes["google_sql_database_instance.eu"].placement.region == "europe-west4"
    assert graph.nodes["module.svc.google_compute_instance.this"].placement.region == "europe-west4"
    # the one aws_ resource: named in the mixed-provider warning, then the unknown-type path
    assert graph.warnings[0].startswith("mixed providers: normalised as gcp (5 google_* resources); "
                                        "1 aws_* resource(s) left to the unknown-type path")
    assert any("aws_s3_bucket.assets: unknown type aws_s3_bucket" in w for w in graph.warnings[1:])
    assert graph.nodes["aws_s3_bucket.assets"].kind == NodeKind.NETWORK


def test_explicit_provider_wins_over_the_vote():
    cfg = load_config(GCP)
    cfg.set("provider", "aws")
    graph, _ = build_graph(GCP, cfg)
    assert not any(w.startswith("mixed providers") for w in graph.warnings)
    assert all(n.kind == NodeKind.NETWORK for n in graph.nodes.values() if n.id.startswith(("google_", "module.")))
    assert graph.nodes["aws_s3_bucket.assets"].subtype == "s3"


def test_provider_flag_reaches_the_pipeline(tmp_path):
    target = tmp_path / "gcp"
    shutil.copytree(GCP, target)
    auto = runner.invoke(app, ["graph", str(target)])
    assert auto.exit_code == 0 and "warning: mixed providers: normalised as gcp" in auto.output
    forced = runner.invoke(app, ["graph", str(target), "--provider", "aws"])
    assert forced.exit_code == 0 and "mixed providers" not in forced.output
    assert "unknown type google_cloud_run_v2_service" in forced.output
    assert runner.invoke(app, ["validate", str(target), "--provider", "gcp"]).output.startswith("ok: ")
    bad = runner.invoke(app, ["graph", str(target), "--provider", "azure"])
    assert bad.exit_code == 2 and "unknown normaliser 'azure'" in bad.output


# ------------------------------------------------------------------ display_name (G21)

@pytest.mark.parametrize("node_id, expected", [
    ('module.compute.aws_instance.this["a"]', 'compute.instance["a"]'),
    ("module.data.aws_db_instance.this", "data.db_instance"),               # only the first word goes
    ("module.svc.google_cloud_run_v2_service.this", "svc.cloud_run_v2_service"),
    ('module.worker["text_input"].google_cloud_run_v2_service.this', 'worker["text_input"].cloud_run_v2_service'),
    ("module.a.module.my_mod.aws_db_instance.this", "a.my_mod.db_instance"),  # a module's own underscore stays
    ('module.m.google_compute_instance.this["a_b.c"]', 'm.compute_instance["a_b.c"]'),
    ("module.m.aws_iam_role.custom_name", "m.iam_role.custom_name"),
    ("internet", "internet"),
    ("aws_lambda_function.fn", "aws_lambda_function.fn"),                   # root-level: unchanged from before
])
def test_display_name_strips_whichever_cloud_prefix(node_id, expected):
    g = InfraGraph()
    g.add_node(Node(node_id, NodeKind.COMPUTE, "x", Placement()))
    assert g.display_name(node_id) == expected


def test_display_name_prefers_a_unique_label():
    g = InfraGraph()
    g.add_node(Node("module.svc.google_cloud_run_v2_service.this", NodeKind.COMPUTE, "cloud_run", Placement(),
                    label="worker"))
    g.add_node(Node("google_cloud_run_v2_service.api", NodeKind.COMPUTE, "cloud_run", Placement(), label="api"))
    assert g.display_name("module.svc.google_cloud_run_v2_service.this") == "worker"
    g.add_node(Node("google_cloud_run_v2_service.dup", NodeKind.COMPUTE, "cloud_run", Placement(), label="api"))
    g._label_counts = None
    assert g.display_name("google_cloud_run_v2_service.api") == "google_cloud_run_v2_service.api"
