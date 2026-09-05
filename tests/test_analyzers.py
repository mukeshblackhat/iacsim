"""Analyzers on a hand-built Result — no parser, no walker."""

import pytest
from conftest import tiny_graph

from iacsim.core.interfaces import ANALYZERS
from iacsim.core.models import HopResult, InfraGraph, Node, NodeKind, Placement, Result
from iacsim.core.registry import load_builtin_plugins


def setup_module():
    load_builtin_plugins()


def _graph(db_region="us-east-1") -> InfraGraph:
    """The shared tiny graph plus a `cache` node for the off-critical-path hop."""
    g = tiny_graph(db_region=db_region)
    g.add_node(Node("cache", NodeKind.DATASTORE, "elasticache", Placement(region="us-east-1", az="us-east-1a")))
    return g


def _hop(src, dst, breakdown, on_path=True, group=None, evidence="test") -> HopResult:
    return HopResult(src, dst, sum(breakdown.values()), breakdown, evidence, on_path, group)


def _result(hops, shape=None, name="s") -> Result:
    total = sum(h.latency_ms for h in hops if h.on_critical_path)
    return Result(name, total, hops, "expected_value", shape=shape or {"hop_count": len(hops),
                  "sequential_hops": len(hops), "parallel_groups": 0, "parallel_savings_ms": 0.0,
                  "fanout_copies": 0, "wait_ms": 0.0})


SEQUENTIAL = [
    _hop("internet", "lb", {"distance": 40, "processing": 2}),
    _hop("lb", "web", {"distance": 1}),
    _hop("web", "db", {"distance": 150, "processing": 5}),
    _hop("web", "db", {"distance": 150, "processing": 5}),
    _hop("db", "web", {"processing": 3}),
]


# ---------------------------------------------------------------- per_category

def test_category_shares_sum_to_one_and_carry_layers():
    findings = ANALYZERS.get("per_category")().analyse(_result(SEQUENTIAL), _graph("eu-west-1"))
    additive = [f for f in findings if f.additive]
    assert sum(f.share for f in additive) == pytest.approx(1.0)
    by = {f.subject: f for f in findings}
    assert by["distance"].layer == "A1" and by["processing"].layer == "A2"
    assert "2 cross-region hop(s)" in by["distance"].detail and "eu-west-1" in by["distance"].detail
    assert by["hops"].layer == "A3" and not by["hops"].additive
    assert by["service"].latency_ms == pytest.approx(15) and not by["service"].additive


def test_category_ignores_off_critical_path_hops_and_reports_parallel_savings():
    hops = SEQUENTIAL + [_hop("web", "cache", {"distance": 1, "processing": 50},
                              on_path=False, group="parallel1/branch2")]
    r = _result(hops, shape={"hop_count": 6, "sequential_hops": 4, "parallel_groups": 1,
                             "parallel_savings_ms": 51.0, "fanout_copies": 0, "wait_ms": 0.0})
    findings = ANALYZERS.get("per_category")().analyse(r, _graph())
    by = {f.subject: f for f in findings}
    assert by["processing"].latency_ms == pytest.approx(15)          # the 50 ms off-path hop is not counted
    assert by["parallel_savings"].latency_ms == 51.0 and by["parallel_savings"].share == 0.0


# ---------------------------------------------------------------- per_node

def test_per_node_merges_repeat_calls_and_names_dominant_category():
    findings = ANALYZERS.get("per_node")().analyse(_result(SEQUENTIAL), _graph("eu-west-1"))
    db = next(f for f in findings if f.subject == "db")
    assert db.latency_ms == 310 and "2 call(s)" in db.detail and "dominated by distance" in db.detail
    assert db.refs == ["web → db", "web → db"] and db.layer == "A1"
    assert findings[0].subject == "db"                                # sorted by ms


# ---------------------------------------------------------------- critical_path

def test_critical_path_is_silent_without_parallel_groups():
    assert ANALYZERS.get("critical_path")().analyse(_result(SEQUENTIAL), _graph()) == []


def test_critical_path_reports_slack_per_branch():
    hops = [
        _hop("web", "db", {"distance": 1, "processing": 30}, group="parallel1/branch1"),
        _hop("web", "cache", {"distance": 1, "processing": 5}, on_path=False, group="parallel1/branch2"),
    ]
    r = _result(hops, shape={"hop_count": 2, "sequential_hops": 0, "parallel_groups": 1,
                             "parallel_savings_ms": 6.0, "fanout_copies": 0, "wait_ms": 0.0})
    findings = ANALYZERS.get("critical_path")().analyse(r, _graph())
    assert len(findings) == 2
    critical = next(f for f in findings if "critical branch" in f.detail)
    slack = next(f for f in findings if "slack" in f.detail)
    assert critical.additive and critical.latency_ms == 31
    assert "25.0 ms of slack" in slack.detail and not slack.additive


# ---------------------------------------------------------------- recommendations

def test_recommendations_fire_for_cross_region_and_repeated_calls_and_cite_hops():
    findings = ANALYZERS.get("recommendations")().analyse(_result(SEQUENTIAL), _graph("eu-west-1"))
    titles = [f.subject for f in findings]
    assert titles[0].startswith("co-locate db with web")
    co_locate = findings[0]
    assert co_locate.latency_ms == pytest.approx(2 * (150 - 2))
    assert co_locate.refs == ["web → db", "web → db"] and "Based on:" in co_locate.detail
    assert any(t.startswith("batch the 2 calls from web to db") for t in titles)


def test_recommendations_parallelise_reads_to_different_stores_and_skip_writes():
    hops = [
        _hop("fn", "db", {"distance": 1, "processing": 5}),
        _hop("fn", "cache", {"distance": 1, "processing": 1}),
        _hop("fn", "db", {"distance": 1, "processing": 10}, evidence="… (as write)"),
    ]
    findings = ANALYZERS.get("recommendations")().analyse(_result(hops), _graph())
    par = next(f for f in findings if f.subject.startswith("run the 2 reads from fn in parallel"))
    assert par.latency_ms == pytest.approx(2) and par.refs == ["fn → db", "fn → cache"]


def test_recommendations_cold_start_and_waits():
    hops = [_hop("lb", "fn", {"distance": 1, "processing": 5, "cold_start": 40}),
            _hop("fn", "fn", {"wait": 100}, evidence="deliberate wait")]
    r = _result(hops, shape={"hop_count": 2, "sequential_hops": 1, "parallel_groups": 0,
                             "parallel_savings_ms": 0.0, "fanout_copies": 0, "wait_ms": 100.0})
    findings = ANALYZERS.get("recommendations")().analyse(r, _graph())
    titles = [f.subject for f in findings]
    assert titles[0].startswith("replace Step Functions Wait")
    assert any(t.startswith("provisioned concurrency on fn") for t in titles)
    assert len(findings) <= 5


# ------------------------------------------------------------------ tail_risk (M6)

def _sampled_result():
    from iacsim.core.models import HopResult, Result
    hops = [
        HopResult("internet", "gw", 50.0, {"distance": 40.0, "processing": 10.0}, "entry",
                  percentiles={"p50": 48.0, "p99": 70.0}),
        HopResult("gw", "fn", 26.0, {"distance": 1.0, "processing": 5.0, "cold_start": 20.0}, "invoke",
                  percentiles={"p50": 6.0, "p99": 405.0}),
        HopResult("fn", "table", 9.0, {"distance": 1.0, "processing": 4.0}, "read",
                  percentiles={"p50": 8.5, "p99": 14.0}),
    ]
    return Result("t", 85.0, hops, "monte_carlo", percentiles={"p50": 62.0, "p90": 90.0, "p95": 440.0, "p99": 480.0},
                  samples=1000, shape={"hop_count": 3})


def _tiny_graph():
    return tiny_graph()


def test_tail_risk_is_silent_without_samples():
    from iacsim.core.interfaces import ANALYZERS
    from iacsim.core.models import Result
    r = Result("t", 10.0, [], "expected_value")
    assert ANALYZERS.get("tail_risk")().analyse(r, _tiny_graph()) == []


def test_tail_risk_names_the_cold_start_as_the_biggest_spread():
    from iacsim.core.interfaces import ANALYZERS
    findings = ANALYZERS.get("tail_risk")().analyse(_sampled_result(), _tiny_graph())
    assert findings[0].subject == "p99 − p50" and findings[0].latency_ms == pytest.approx(418.0)
    assert "1,000 samples" in findings[0].detail
    top = findings[1]
    assert top.subject == "gw → fn" and top.latency_ms == pytest.approx(399.0)
    assert "cold start" in top.detail and top.refs == ["gw → fn"] and not top.additive
    assert [f.subject for f in findings[2:]] == ["internet → gw", "fn → table"]
