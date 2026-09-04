"""In-memory MetricSource for tests and dry runs."""

from __future__ import annotations

from iacsim.core.interfaces import METRIC_SOURCES, MetricSource


@METRIC_SOURCES.register("fake")
class FakeMetricSource(MetricSource):
    def __init__(self, lambdas: dict[str, dict] | None = None, tables: dict[str, dict] | None = None):
        self._lambdas = lambdas or {}
        self._tables = tables or {}

    def lambda_durations(self, function_name: str, window: str) -> dict[str, float]:
        return self._lambdas.get(function_name, {})

    def table_latency(self, table_name: str, window: str) -> dict[str, float]:
        return self._tables.get(table_name, {})
