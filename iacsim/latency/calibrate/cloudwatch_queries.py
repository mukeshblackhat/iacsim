"""Pure CloudWatch maths — no boto3, fully unit-tested with canned results.

`queries_for(kind, name, period)` builds the GetMetricData queries for one
resource; `to_measurement(kind, results)` turns the returned value lists
(keyed by query Id) into the profile keys the walkers read:

    lambda        Duration p50 → warm       InitDuration p50 → cold = warm + init
                  InitDuration SampleCount / Invocations Sum → cold_prob
                  ln(p99 / p50) / 2.326 → sigma, clamped to [0.05, 1.5]   (exact for a
                  lognormal; CloudWatch has no stddev statistic, so p99/p50 replaces std/mean)
                  Invocations Sum == 0 → None (idle in the window)
    dynamodb      SuccessfulRequestLatency p50: GetItem, Query → read (mean of those present)
                                               PutItem, UpdateItem → write
    rds           ReadLatency / WriteLatency Average (seconds) → read / write (ms)
    alb           TargetResponseTime p50 (seconds) → route (ms). The dimension is
                  `app/<name>/<id>`, not the name: `alb_dimension(list_metrics, name)`
                  resolves it from a ListMetrics response (hence that permission).
    api_gateway   Latency p50 − IntegrationLatency p50 → route (the gateway's own overhead)
    step_functions  not a metric — per-state timings need execution history; unsupported.

A value list is per period datapoints; we take the median of p50 datapoints
(robust to a quiet hour) and the sum of counts.
"""

from __future__ import annotations

import math
import statistics

Z_P99 = 2.326           # Φ⁻¹(0.99): lognormal p99 = p50 · exp(Z_P99 · σ)
SIGMA_MIN, SIGMA_MAX = 0.05, 1.5
PERIODS = (60, 300, 900, 3600, 21600, 86400)
MAX_DATAPOINTS = 1440

SUPPORTED = ("lambda", "dynamodb", "rds", "alb", "api_gateway")
_UNITS = {"d": 86400, "h": 3600, "m": 60}


def parse_window(window: str) -> int:
    """'7d' | '24h' | '30m' → seconds."""
    window = window.strip().lower()
    if len(window) < 2 or window[-1] not in _UNITS or not window[:-1].isdigit():
        raise ValueError(f"window must look like 7d, 24h or 30m, got {window!r}")
    return int(window[:-1]) * _UNITS[window[-1]]


def period_for(window_seconds: int) -> int:
    """Smallest standard period that keeps a window under MAX_DATAPOINTS points."""
    return next((p for p in PERIODS if window_seconds / p <= MAX_DATAPOINTS), PERIODS[-1])


def queries_for(kind: str, name: str, period: int) -> list[dict]:
    if kind == "lambda":
        dims = [("FunctionName", name)]
        return [_q("dur_p50", "AWS/Lambda", "Duration", dims, "p50", period),
                _q("dur_p99", "AWS/Lambda", "Duration", dims, "p99", period),
                _q("init_p50", "AWS/Lambda", "InitDuration", dims, "p50", period),
                _q("init_count", "AWS/Lambda", "InitDuration", dims, "SampleCount", period),
                _q("invocations", "AWS/Lambda", "Invocations", dims, "Sum", period)]
    if kind == "dynamodb":
        return [_q(f"lat_{op.lower()}", "AWS/DynamoDB", "SuccessfulRequestLatency",
                   [("TableName", name), ("Operation", op)], "p50", period)
                for op in ("GetItem", "Query", "PutItem", "UpdateItem")]
    if kind == "rds":
        dims = [("DBInstanceIdentifier", name)]
        return [_q("read_s", "AWS/RDS", "ReadLatency", dims, "Average", period),
                _q("write_s", "AWS/RDS", "WriteLatency", dims, "Average", period)]
    if kind == "alb":
        return [_q("target_s", "AWS/ApplicationELB", "TargetResponseTime",
                   [("LoadBalancer", name)], "p50", period)]
    if kind == "api_gateway":
        dims = [("ApiName", name)]
        return [_q("latency", "AWS/ApiGateway", "Latency", dims, "p50", period),
                _q("integration", "AWS/ApiGateway", "IntegrationLatency", dims, "p50", period)]
    raise ValueError(f"no CloudWatch queries for kind {kind!r}")


def to_measurement(kind: str, results: dict[str, list[float]]) -> dict[str, float] | None:
    """Value lists (by query Id) → profile keys, or None when the essential
    series are empty (resource idle in the window)."""
    med = {k: statistics.median(v) for k, v in results.items() if v}
    total = {k: sum(v) for k, v in results.items() if v}

    if kind == "lambda":
        if "dur_p50" not in med or ("invocations" in results and not total.get("invocations")):
            return None
        warm = med["dur_p50"]
        out = {"warm": round(warm, 2)}
        if "init_p50" in med:
            out["cold"] = round(warm + med["init_p50"], 2)
        if total.get("invocations"):
            out["cold_prob"] = round(total.get("init_count", 0.0) / total["invocations"], 4)
        if "dur_p99" in med and warm > 0:
            out["sigma"] = _sigma(med["dur_p99"], warm)
        return out

    if kind == "dynamodb":
        reads = [med[k] for k in ("lat_getitem", "lat_query") if k in med]
        writes = [med[k] for k in ("lat_putitem", "lat_updateitem") if k in med]
        if not reads and not writes:
            return None
        out = {}
        if reads:
            out["read"] = round(statistics.mean(reads), 2)
        if writes:
            out["write"] = round(statistics.mean(writes), 2)
        return out

    if kind == "rds":
        out = {k: round(med[s] * 1000, 2) for k, s in (("read", "read_s"), ("write", "write_s")) if s in med}
        return out or None

    if kind == "alb":
        return {"route": round(med["target_s"] * 1000, 2)} if "target_s" in med else None

    if kind == "api_gateway":
        if "latency" not in med:
            return None
        return {"route": round(max(med["latency"] - med.get("integration", 0.0), 0.0), 2)}

    return None


def _sigma(p99: float, p50: float) -> float:
    ratio = max(p99 / p50, 1.0)
    return round(min(max(math.log(ratio) / Z_P99, SIGMA_MIN), SIGMA_MAX), 3)


def alb_dimension(list_metrics_response: dict, name: str) -> str | None:
    """`app/<name>/<id>` for the load balancer called <name>, from a ListMetrics
    response (Namespace AWS/ApplicationELB); None when no metric matches."""
    for metric in list_metrics_response.get("Metrics", []):
        for dim in metric.get("Dimensions", []):
            value = dim.get("Value", "")
            if dim.get("Name") == "LoadBalancer" and value.startswith(f"app/{name}/"):
                return value
    return None


def _q(qid: str, namespace: str, metric: str, dims: list[tuple[str, str]], stat: str, period: int) -> dict:
    return {
        "Id": qid,
        "MetricStat": {
            "Metric": {"Namespace": namespace, "MetricName": metric,
                       "Dimensions": [{"Name": n, "Value": v} for n, v in dims]},
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": True,
    }
