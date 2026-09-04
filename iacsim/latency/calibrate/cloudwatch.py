"""CloudWatch-backed MetricSource.                                           [M7]

lambda_durations → {warm: p50 Duration, cold: p50 InitDuration + Duration, cold_prob: init count / invocations}
table_latency    → {read: p50 SuccessfulRequestLatency(GetItem/Query), write: p50 (PutItem/UpdateItem)}
"""

from __future__ import annotations

from iacsim.core.interfaces import METRIC_SOURCES, MetricSource


@METRIC_SOURCES.register("cloudwatch")
class CloudWatchMetricSource(MetricSource):
    def __init__(self, region: str | None = None, profile: str | None = None):
        # boto3 is optional (pip install iacsim[calibrate]); import lazily so the
        # rest of the tool never needs it.
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("`iacsim calibrate` needs boto3: pip install 'iacsim[calibrate]'") from e
        self._session = boto3.Session(region_name=region, profile_name=profile)

    def lambda_durations(self, function_name: str, window: str) -> dict[str, float]:
        raise NotImplementedError("M7: CloudWatch Lambda Duration / InitDuration")

    def table_latency(self, table_name: str, window: str) -> dict[str, float]:
        raise NotImplementedError("M7: CloudWatch DynamoDB SuccessfulRequestLatency")
