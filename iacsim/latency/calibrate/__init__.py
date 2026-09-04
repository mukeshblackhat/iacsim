"""`iacsim calibrate` — real measurements → profile YAML.                   [M7]

Only this package imports boto3. Everything else is unit-testable with
FakeMetricSource. Output uses exactly the defaults.yaml schema, filling
processing.<subtype>.per_resource[<node id>] and stamping meta.source=cloudwatch.
"""
from iacsim.latency.calibrate import fake, cloudwatch  # noqa: F401
