# Contributing to iacsim

## Setup

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
make hooks          # installs the pre-commit hook: every commit runs `make check`
make check          # ruff (line length 120, E501 + bugbear) + pytest + coverage ≥ 85 %
make examples       # every example still parses, runs and diffs
```

`make ci` runs both — that is exactly what GitHub Actions runs on every push and pull request
(`.github/workflows/ci.yml`, Python 3.12 and 3.13).

## The one rule: plug-and-play

Every choice in iacsim is an extension point: an abstract base class in `iacsim/core/interfaces.py`,
a registry, and a key in `iacsim.yaml`. **New behaviour is a registration, never a special case in the
pipeline.** If your change adds a choice, it must add an interface (or reuse one), a registry entry,
and a config key — and `iacsim plugins` must list it. Files under `core/`, `simulator/`, `analyzer/`
and `reporter/` should only gain additive registrations when you add a source, cloud or rule.

`CODE_FLOW.md` §5 ("How to add …") has a row per extension point with the file to copy and the
test to copy. In short:

- **An inference rule** — subclass `InferenceRule`, decorate with `@INFERENCE_RULES.register("name")`,
  return `Edge`s that each carry an `evidence` sentence and a `confidence`; add the name to
  `inference.rules`; copy `graph/inference/vpc_peering.py` and its test.
- **A cost rule** — subclass `CostRule`, register in `COST_RULES`, return `{category: ms}`; add to
  `latency.rules`; copy `latency/rules/transition.py` and `tests/test_latency_rules.py`.
- **A walker** — subclass `Walker`, register in `WALKERS`, reuse `simulator/traversal.py`'s `Planner`
  and supply a `Backend`; select with `simulation.walker`; copy `walkers/expected_value.py`.
- **An analyzer** — subclass `Analyzer`, register in `ANALYZERS`, return `Finding`s (return `[]` when
  not applicable so it can stay on by default); add to `analysis.analyzers`.
- **A reporter** — subclass `Reporter`, register in `REPORTERS`, render the shared `Brief` from
  `reporter/_brief.py`; select with `report.outputs`.
- **A metric source** (calibration) — subclass `MetricSource`, implement `supports(kind)` and
  `measure(kind, name, window, region)`, register in `METRIC_SOURCES`; options come from
  `calibrate.sources.<name>` — never credentials; copy `latency/calibrate/fake.py`.
- **A parser** (new IaC format) — subclass `Parser`, register in `PARSERS`, emit `RawResources` in the
  canonical Terraform-shaped form so the normaliser and every rule work unchanged; copy
  `parsers/cloudformation/`.
- **A cloud provider** — a normaliser in `graph/normalisers/`, rules that carry the same evidence as
  the AWS ones, a defaults block in `latency/defaults.yaml`, one example stack, one test file.

Third-party additions go in `plugins/` (auto-imported) or the `iacsim.plugins` entry-point group —
no fork needed.

## Style

- Module docstring stating the approach; small functions; type hints; no new required dependencies.
- Every inferred edge and every hop must say *why* (evidence strings are the product, not decoration).
- Tests assert formulas over profile values (`2 * 2 * (cross_region - same_az)`), not magic numbers.
- Unresolvable input is a warning, never a crash.

## Commits

One work package per commit, through the hook (`make check` must be green). The commit message says
what changed for the user, with the before/after numbers when they moved. Nothing is force-pushed.

## Where decisions live

`DECISIONS.md` logs every design decision (question · options · choice · why · where in code).
Changing one means: edit the config key or register the new implementation, then add a row there.
`SPEC.md` is the design, `TIMELINE.md` the status, `CODE_FLOW.md` the call-by-call walkthrough.
