"""CloudWatch-backed MetricSource — the thin boto3 wrapper.

All the metric→profile maths lives in cloudwatch_queries.py (pure, tested);
this file only talks to AWS: one GetMetricData call per resource, region taken
from the node's placement (or the `region` option), credentials from the
standard AWS chain (`aws configure`, AWS_PROFILE, instance role). Read-only:
needs cloudwatch:GetMetricData (and ListMetrics for discovery), nothing else.

    calibrate:
      source: cloudwatch
      sources:
        cloudwatch: { region: us-east-1, aws_profile: my-readonly }

Not exercised against a live account in this repo (see TIMELINE.md, option a);
excluded from coverage for that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from iacsim.core.interfaces import METRIC_SOURCES, MetricSource, MetricSourceError
from iacsim.latency.calibrate import cloudwatch_queries as q


@METRIC_SOURCES.register("cloudwatch")
class CloudWatchMetricSource(MetricSource):
    # The AWS calibration targets — the subtypes CloudWatch has a namespace for.
    # `supports()` is the subset with queries written (cloudwatch_queries.SUPPORTED);
    # step_functions is a target a fixture or another source fills until its exist.
    KINDS: ClassVar[tuple[str, ...]] = ("lambda", "dynamodb", "rds", "alb", "api_gateway", "step_functions")

    def __init__(self, region: str | None = None, aws_profile: str | None = None,
                 **options: Any) -> None:
        super().__init__(region=region, aws_profile=aws_profile, **options)
        self._region = region
        self._boto3 = _import_boto3()
        self._session = self._boto3.Session(region_name=region, profile_name=aws_profile)
        self._clients: dict[str, Any] = {}

    def describe(self) -> dict[str, Any]:
        return {k: v for k, v in (("region", self._region), ("aws_profile", self.options.get("aws_profile"))) if v}

    def supports(self, kind: str) -> bool:
        return kind in q.SUPPORTED

    def measure(self, kind: str, name: str, window: str,
                region: str | None = None) -> dict[str, float] | None:
        seconds = q.parse_window(window)
        period = q.period_for(seconds)
        end = datetime.now(UTC)
        client = self._client(region or self._region)
        try:
            if kind == "alb":   # dimension is app/<name>/<id>, discovered via ListMetrics
                name = q.alb_dimension(client.list_metrics(
                    Namespace="AWS/ApplicationELB", MetricName="TargetResponseTime"), name)
                if name is None:
                    return None
            response = client.get_metric_data(
                MetricDataQueries=q.queries_for(kind, name, period),
                StartTime=end - timedelta(seconds=seconds), EndTime=end,
            )
        except Exception as e:  # botocore's exception classes are only importable with boto3
            raise MetricSourceError(_hint(e)) from e
        results = {r["Id"]: list(r.get("Values", [])) for r in response.get("MetricDataResults", [])}
        return q.to_measurement(kind, results)

    def _client(self, region: str | None):
        key = region or "default"
        if key not in self._clients:
            self._clients[key] = self._session.client("cloudwatch", region_name=region)
        return self._clients[key]


def _import_boto3():
    try:
        import boto3
    except ImportError as e:
        raise MetricSourceError("boto3 missing — pip install 'iacsim[calibrate]'") from e
    return boto3


def _hint(error: Exception) -> str:
    name = type(error).__name__
    if "Credentials" in name or "NoCredentials" in name:
        return "no AWS credentials found — run `aws configure` or set AWS_PROFILE (read-only is enough)"
    if "Region" in name:
        return "no AWS region — pass --region or set calibrate.sources.cloudwatch.region"
    return f"CloudWatch call failed ({name}): {error}"
