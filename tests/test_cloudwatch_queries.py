"""The CloudWatch maths with canned GetMetricData results — no boto3, no account."""

import math

import pytest

from iacsim.latency.calibrate import cloudwatch_queries as q


def test_window_parsing_and_period_choice():
    assert q.parse_window("7d") == 7 * 86400 and q.parse_window("24H") == 86400 and q.parse_window("30m") == 1800
    for bad in ("7", "d7", "1w", "", "7 days"):
        with pytest.raises(ValueError):
            q.parse_window(bad)
    assert q.period_for(q.parse_window("30m")) == 60
    assert q.period_for(q.parse_window("24h")) == 60
    assert q.period_for(q.parse_window("7d")) == 900        # 604800 / 900 = 672 ≤ 1440
    assert q.period_for(q.parse_window("90d")) == 21600


def test_queries_carry_the_right_namespace_dimensions_and_stats():
    lam = q.queries_for("lambda", "my-fn", 300)
    assert [x["Id"] for x in lam] == ["dur_p50", "dur_p99", "init_p50", "init_count", "invocations"]
    assert lam[0]["MetricStat"]["Metric"] == {"Namespace": "AWS/Lambda", "MetricName": "Duration",
                                              "Dimensions": [{"Name": "FunctionName", "Value": "my-fn"}]}
    assert lam[3]["MetricStat"]["Stat"] == "SampleCount" and lam[4]["MetricStat"]["Stat"] == "Sum"
    ddb = q.queries_for("dynamodb", "orders", 60)
    assert {x["MetricStat"]["Metric"]["Dimensions"][1]["Value"] for x in ddb} == {"GetItem", "Query", "PutItem", "UpdateItem"}
    assert q.queries_for("rds", "pg", 60)[0]["MetricStat"]["Metric"]["Namespace"] == "AWS/RDS"
    assert q.queries_for("alb", "web", 60)[0]["MetricStat"]["Metric"]["MetricName"] == "TargetResponseTime"
    assert [x["Id"] for x in q.queries_for("api_gateway", "api", 60)] == ["latency", "integration"]
    with pytest.raises(ValueError):
        q.queries_for("step_functions", "sm", 60)
    assert "step_functions" not in q.SUPPORTED


def test_lambda_measurement_arithmetic():
    m = q.to_measurement("lambda", {
        "dur_p50": [10, 12, 11], "dur_p99": [40, 48, 44],
        "init_p50": [400, 420], "init_count": [5, 5], "invocations": [100, 100],
    })
    assert m["warm"] == 11 and m["cold"] == 11 + 410 and m["cold_prob"] == 0.05
    assert m["sigma"] == round(math.log(44 / 11) / q.Z_P99, 3)
    assert q.to_measurement("lambda", {"dur_p50": [], "invocations": [3]}) is None
    assert q.to_measurement("lambda", {"dur_p50": [5], "invocations": [0, 0]}) is None   # idle in window
    partial = q.to_measurement("lambda", {"dur_p50": [5]})
    assert partial == {"warm": 5}                                 # no init data → defaults fill cold/cold_prob
    assert q.to_measurement("lambda", {"dur_p50": [5], "dur_p99": [5]})["sigma"] == q.SIGMA_MIN
    assert q.to_measurement("lambda", {"dur_p50": [1], "dur_p99": [1000]})["sigma"] == q.SIGMA_MAX


def test_datastore_and_traffic_measurements_with_unit_conversion():
    assert q.to_measurement("dynamodb", {"lat_getitem": [2, 4], "lat_query": [6], "lat_putitem": [8]}) == {"read": 4.5, "write": 8}
    assert q.to_measurement("dynamodb", {"lat_query": [3]}) == {"read": 3}
    assert q.to_measurement("dynamodb", {"lat_getitem": []}) is None
    assert q.to_measurement("rds", {"read_s": [0.004, 0.006], "write_s": [0.01]}) == {"read": 5, "write": 10}
    assert q.to_measurement("rds", {}) is None
    assert q.to_measurement("alb", {"target_s": [0.0021]}) == {"route": 2.1}
    assert q.to_measurement("alb", {"target_s": []}) is None
    assert q.to_measurement("api_gateway", {"latency": [30], "integration": [18]}) == {"route": 12}
    assert q.to_measurement("api_gateway", {"latency": [10], "integration": [15]}) == {"route": 0}
    assert q.to_measurement("api_gateway", {"integration": [15]}) is None
    assert q.to_measurement("step_functions", {"x": [1]}) is None


def test_alb_dimension_is_resolved_from_list_metrics():
    response = {"Metrics": [
        {"Dimensions": [{"Name": "LoadBalancer", "Value": "app/other/abc"}]},
        {"Dimensions": [{"Name": "TargetGroup", "Value": "targetgroup/web/1"},
                        {"Name": "LoadBalancer", "Value": "app/web/50dc6c495c0c9188"}]},
    ]}
    assert q.alb_dimension(response, "web") == "app/web/50dc6c495c0c9188"
    assert q.alb_dimension(response, "missing") is None
    assert q.alb_dimension({}, "web") is None
