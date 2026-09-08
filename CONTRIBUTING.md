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
  `reporter/_brief.py` — or inline the JSON payload like `html` does (`report_payload()` from
  `reporter/json_.py` + `render_page()` from `reporter/html_.py`, one placeholder in a template);
  select with `report.outputs`.
- **A metric source** (calibration) — subclass `MetricSource`, implement `supports(kind)` and
  `measure(kind, name, window, region)`, register in `METRIC_SOURCES`; options come from
  `calibrate.sources.<name>` — never credentials; copy `latency/calibrate/fake.py`.
- **A parser** (new IaC format) — subclass `Parser`, register in `PARSERS`, emit `RawResources` in the
  canonical Terraform-shaped form so the normaliser and every rule work unchanged; copy
  `parsers/cloudformation/`.
- **A cloud provider** — a normaliser in `graph/normalisers/`, rules that carry the same evidence as
  the AWS ones, a defaults block in `latency/defaults.yaml`, one example stack, one test file.
  M11 (GCP) proved that list is necessary but not sufficient. Four more, each of which fails
  *silently* if you skip it — `CODE_FLOW.md` §5 "Adding a cloud provider" has the `file:line` for
  every one:
  - **Its own behaviour tables.** The subtype tables that decide what a hop costs are
    provider-owned from M11 (`DECISIONS.md` §10, G4) — declare them on your `Normaliser`; do not
    add your subtypes to the AWS tables.
  - **The `"internet"` node-id contract.** The normaliser must emit one EXTERNAL node whose id is
    literally `"internet"`, with an edge into every gateway / load balancer / CDN node.
    `scenarios/inferred.py`, `simulator/traversal.py` and `core/pipeline.py` all match that exact
    string; any other name gives a graph with no entry points and no inferred scenarios.
  - **Subtype names that cannot collide with AWS ones.** `Profile.processing_for` is keyed by the
    bare `Node.subtype` and `Node` carries no provider field, so a subtype called `lambda` or `s3`
    silently inherits AWS numbers. Name them `cloud_run`, `gcs`, `cloud_sql`.
  - **A test that asserts a non-zero cost for every new subtype.** A subtype missing from the
    profile, or from your normaliser's `INVOKE_KEYS` table (read by `latency/rules/processing.py`
    through `behaviour_tables()`), is charged
    **nothing** — no error, no warning, the hop just prices at 0. The same applies to cold starts:
    `latency/rules/cold_start.py` fires only for the subtypes its provider's table lists.
  There is no `distance.<cloud>` block: `distance` in `latency/defaults.yaml` is one flat, global
  table, so your inter-region pairs go straight into `distance.cross_region`.

Third-party additions go in `plugins/` (auto-imported) or the `iacsim.plugins` entry-point group —
no fork needed.

## Style

- Module docstring stating the approach; small functions; type hints; no new required dependencies.
- Every inferred edge and every hop must say *why* (evidence strings are the product, not decoration).
- Tests assert formulas over profile values (`2 * 2 * (cross_region - same_az)`), not magic numbers.
- Unresolvable input is a warning, never a crash.

## Browser smoke for the dashboard

The page's JavaScript is outside pytest and the coverage gate on purpose (`DECISIONS.md` D54);
what Python can decide — payload, section titles, escaping, substitution, `prepare`, the sample —
is tested in Python. So after any change to `iacsim/viewer/index.html` or `reporter/html_.py`,
open the page in a real browser once. It is run through the Playwright MCP (or by hand); it is
**not** in `make check` and adds no dependency.

1. `make dashboard-sample` (also run by `make examples`), then open
   `examples/dashboard/gcp-web/report.html` from `file://` — and `foosh-load/report.html` for the
   capacity band.
2. The map is the first thing on screen; `g.node` count equals the header's node count (30 for
   foosh-load).
3. Click a node on the path (API Gateway in foosh-load) → the drawer opens and shows the hop's
   evidence text ("is a public entry point"); Escape closes it and focus returns to the node.
4. Switch scenario tab → the hop badges on the map count exactly that scenario's hops and the
   total in the tab matches the KPI strip.
5. No console errors or warnings.
6. Once more with dark mode emulated (`prefers-color-scheme: dark`) — the map, the bars and the
   heatmap stay readable.

Then `make check`: `tests/test_dashboard_sample.py` fails if the committed sample no longer matches
what the tool produces.

## Commits

One work package per commit, through the hook (`make check` must be green). The commit message says
what changed for the user, with the before/after numbers when they moved. Nothing is force-pushed.

## Where decisions live

`DECISIONS.md` logs every design decision (question · options · choice · why · where in code).
Changing one means: edit the config key or register the new implementation, then add a row there.
`SPEC.md` is the design, `TIMELINE.md` the status, `CODE_FLOW.md` the call-by-call walkthrough.
