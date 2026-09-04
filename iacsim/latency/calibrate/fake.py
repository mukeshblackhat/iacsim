"""In-memory / YAML-backed MetricSource for tests, demos and dry runs.

Fixture shape (keyed by *physical name*, so one file serves the Terraform and
the CloudFormation graph of the same stack):

    lambda:
      async-workflow-parser-staging: { warm: 40, cold: 900, cold_prob: 0.3, sigma: 0.4 }
    dynamodb:
      AsyncWorkflowsStaging: { read: 3.2, write: 6.1 }
    api_gateway:
      async-workflow-staging-api: { route: 12 }

A kind that is absent from the fixture is "not supported"; a name that is
absent returns None ("no data in window") — both paths the calibrator reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from iacsim.core.interfaces import METRIC_SOURCES, MetricSource


@METRIC_SOURCES.register("fake")
class FakeMetricSource(MetricSource):
    def __init__(self, fixture: str | None = None, data: dict[str, Any] | None = None,
                 **options: Any) -> None:
        super().__init__(fixture=fixture, **options)
        self._fixture = Path(fixture) if fixture else None
        self._data: dict[str, dict[str, dict[str, float]]] = dict(data or {})
        if self._fixture and self._fixture.is_absolute():
            self._data.update(_load_fixture(self._fixture))

    def prepare(self, root: Path) -> None:
        """A relative fixture path is taken relative to the iacsim.yaml that named it."""
        if self._fixture and not self._fixture.is_absolute():
            self._fixture = root / self._fixture
            self._data.update(_load_fixture(self._fixture))

    def describe(self) -> dict[str, Any]:
        return {"fixture": str(self._fixture)} if self._fixture else {}

    def supports(self, kind: str) -> bool:
        return kind in self._data

    def measure(self, kind: str, name: str, window: str,
                region: str | None = None) -> dict[str, float] | None:
        block = self._data.get(kind, {}).get(name)
        return dict(block) if block else None


def _load_fixture(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"calibrate fixture not found: {path}")
    return yaml.safe_load(path.read_text()) or {}
