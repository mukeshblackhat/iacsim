# GCP — change log

The execution record for M11. Append-only: one entry per work package, newest
last. Read this to see what was actually done, as opposed to what was planned
(`00-OVERVIEW.md`) or decided (`01-DECISIONS.md`).

Each entry names every file created or modified, and says what changed for the
user. `make check` must be green at the end of every entry — that is the rule in
`CONTRIBUTING.md`, one work package per commit.

---

## Round 1 — documentation (2026-09-08)

**No `iacsim/` source file was modified.** `iacsim run` behaviour is provably
unchanged; the full test suite passing is the proof.

### Created

| file | what it is |
|---|---|
| `docs/gcp/00-OVERVIEW.md` | the index — what M11 is, what changes where, reading order |
| `docs/gcp/01-DECISIONS.md` | every GCP choice in `DECISIONS.md`'s shape — question, options, chosen, why, where in code |
| `docs/gcp/02-TYPE-MAP.md` | the evidence-backed `google_*` → (NodeKind, subtype) table |
| `docs/gcp/03-TESTING.md` | what we test, with which public fixture, as a todo checklist |
| `docs/gcp/04-CHANGES.md` | this file |
| `docs/gcp/05-EXAMPLES.md` | the wider third-party GCP corpus — 14 verified repos, 22 recorded rejections |

### Modified

| file | entry |
|---|---|
| `TIMELINE.md` | M11 ⏳ → 🔨; dated session entry |
| `DECISIONS.md` | new section 10 (G1–G6); D8 amended to record that a new *cloud* needs more than a registration |
| `SPEC.md` | scope, latency model, repository layout |
| `CODE_FLOW.md` | §5 "add a cloud provider" row corrected — the `distance.<cloud>` block it promised does not exist |
| `README.md` | "GCP input" section |
| `CONTRIBUTING.md` | "A cloud provider" bullet extended with what M11 actually needed |

### Reconciliation after the corpus survey

`05-EXAMPLES.md` found 134 distinct `google_*` types against `02-TYPE-MAP.md`'s
94, so three edits were folded back into the type map:

- `google_cloud_run_v2_worker_pool` added — it carries a whole ingestion tier in
  real code, and omitting it would have dropped that tier from the graph silently
- `google_memorystore_instance` added — the successor API to the mapped
  `google_redis_instance`, same subtype
- `google_kms_*` added to the ignore list — `03-TESTING.md` §3.2 already asserted
  it was ignored, but the row did not exist
- IAM grants switched from an enumeration to a suffix pattern
  (`^google_.*_iam_(member|binding|policy)$`); an enumeration cannot stay complete

### Verification

- `pytest -q` → **290 passed**, identical to the pre-round baseline
- `ruff check iacsim tests` → clean
- **114 `path:line` citations** across `docs/gcp/` verified to resolve to a real
  file and an in-range line. `tests/test_docs_refs.py` only guards `DECISIONS.md`
  and `CODE_FLOW.md`, so the new docs were checked with the same rules separately.
  Two ambiguous bare `models.py` references were qualified to
  `iacsim/core/models.py` (the repo has two files of that name).
- `git diff --stat` shows markdown only — **no `iacsim/` file was modified**,
  which is the proof that `iacsim run` behaviour is unchanged.

---

## Round 2 — implementation

Sequenced as WP1 loader provider fix → WP2 provider-owned subtype tables →
WP3 the `gcp` normaliser + GCP profile (WP6 folded in: the type map and the
profile are one unit) → WP4 auto-detection → WP5 inference rules → WP7 examples
and vendored fixtures.

### WP1 — loader provider/region fix (2026-09-08)

**For the user:** a `provider "google" { region = … }` block is now read.
Before, every non-`aws` provider block was skipped and every GCP resource got
`region=None`, so cross-region cost silently vanished.

Modified: `iacsim/parsers/terraform/loader.py` — the `!= "aws"` filter is gone;
regions are keyed by provider (`aws`, `google`, `google-beta`, `aws.db`,
`google-beta.eu`); a resource with no `provider =` derives its provider from its
type prefix; `_PROVIDER_REF` accepts hyphens and the alias-less `${aws}` form;
new `_PROVIDER_BARE` handles the unwrapped `provider = google-beta` form that
`hcl2` produces; new `zones` map, with a resolved provider zone stashed as
`attrs["_provider_zone"]` (never overwriting a resource's own `zone`;
`RawResource` has no zone field and `models.py` was not this WP's to change).

Tests: `tests/fixtures/gcp/` (mixed `aws` + `google` + `google-beta` + aliased,
with a child module remapping hyphenated providers); 5 tests in
`tests/test_terraform_parser.py`.

AWS behaviour: parser output diffed across every `examples/**` directory with
and without `--region`. Only two differences, both `random_string` resources
(an ignored type, never a graph node) that previously inherited the AWS region
and now resolve through their own prefix.

Judgement call recorded: the "region not resolved" warning now fires for any
provider block whose region does not resolve, not just `aws`. A region-less
utility block (`provider "random" {}`) would warn spuriously; none exists in the
repo today and the phrase is already in `KNOWN_WARNINGS`.

### WP2 — provider-owned behaviour tables (2026-09-08)

**For the user:** every hop into an `aws_kinesis_stream` was priced at 0 ms.
It is now priced. And the engine no longer knows any cloud's subtype names.

Modified: `iacsim/core/interfaces.py` — `Normaliser` gains `INVOKE_KEYS` and
`COLD_START` as `ClassVar` tables with empty defaults (so `plugins/` normalisers
keep working), and a module-level `behaviour_tables()` that merges every
registered normaliser's tables once, keyed on the registry's names so a
late-registered plugin rebuilds it. `iacsim/graph/normalisers/aws.py` — the old
`INVOKE_KEY_FOR_SUBTYPE` dict moves here verbatim (15 entries) plus
`COLD_START = {"lambda"}`. `iacsim/latency/rules/processing.py` and
`cold_start.py` read the merged tables; `OPERATION_FOR_EDGE` stays, it is
provider-neutral. `iacsim/latency/defaults.yaml` — `processing.kinesis:
{ publish: 10, consume: 200 }`, estimates, commented.

Tests: 17 added to `tests/test_latency_rules.py`, including the guard-rail —
parametrised over every non-NETWORK subtype the AWS `TYPE_MAP` can produce,
asserting a `processing` block exists, `INVOKE_KEYS` names a key, and the block
contains it. It fails on `kinesis` without the new block (confirmed).

No expected total moved: no `aws_kinesis_stream` exists under `examples/` or
`tests/`, and `tests/fixtures/foosh_report_sha256.txt` is unchanged.

Verification after WP1 + WP2 together: `pytest -q` → **312 passed**; ruff clean.

### WP3 — the `gcp` normaliser and its profile (2026-09-08)

**For the user:** `google_*` resources now become real graph nodes with kind,
subtype and placement, and every one of them has a latency number. `iacsim
plugins` lists `normalisers: aws, gcp`.

Created: `iacsim/graph/normalisers/gcp.py` — `TYPE_MAP` (46 subtypes across
COMPUTE / LB / GATEWAY / CDN / DATASTORE / QUEUE / ORCHESTRATOR / NETWORK),
ignore list with IAM grants matched by suffix
(`^google_.*_iam_(member|binding|policy)$`), resource-first placement
(`region` / `zone` / `location`, provider fallback via `_provider_zone`),
`INVOKE_KEYS`, `COLD_START`, the `internet` node edged to GATEWAY, CDN and the
chain head only. `tests/test_normaliser_gcp.py`. Modified:
`iacsim/graph/normalisers/__init__.py` (one import), `iacsim/latency/defaults.yaml`
(a `processing` block per non-NETWORK subtype; 15 GCP `cross_region` pairs merged
into the flat map — G12), `tests/test_latency_rules.py` (guard-rail parametrised
over both normalisers; also asserts `INVOKE_KEYS == hop subtypes` exactly and no
AWS/GCP subtype-name collision outside NETWORK).

Estimates, all commented in the yaml: `cloud_run` cold 2000 ms / p 0.05,
`cloud_functions_v2` 1500, `cloud_functions` 1000, `app_engine` 1500;
`cloud_run_job` charges `start: 2000` (every execution is a fresh container).

Divergences from `02-TYPE-MAP.md` / `03-TESTING.md`, all deliberate:
- `internet` edges only to the chain **head** (`forwarding_rule`), not every LB
  node — otherwise each of `url_map` / `backend_service` / `neg` becomes an
  inferred-scenario entry point. `03-TESTING.md` §3.2's "every LB node" was
  written with a one-node ALB in mind.
- Multi-region `location` values (`US`, `nam5`) keep the provider region rather
  than `None`, which would trigger the "pass --region" warning on every
  multi-region bucket; the raw string is kept in `attrs["location"]`.
- Cloud CDN: `enable_cdn` kept as an attr on the `backend_service` node; no
  invented cache number.
- GKE node pools are nodes (per `02-TYPE-MAP.md`; `03-TESTING.md` §3.2 said
  cluster-only).
- Added beyond the doc: `google_cloud_run_service` (v1) → `cloud_run`,
  `google_endpoints_service` → `cloud_endpoints`, tcp/ssl proxies →
  `target_proxy`. `aws_*` is deliberately *not* ignored under `gcp` — it warns.

### WP3b — zero distance inside the chain (2026-09-08)

**For the user:** the load-balancer chain now costs what an ALB costs. Before
this, a global `backend_service → neg` hop was priced as cross-region — up to
~200 ms of phantom network on a multi-region stack.

WP3 stopped short here on purpose: it did not own `distance.py` and refused to
fake a shared AZ. Modified: `iacsim/core/interfaces.py` — third `ClassVar`
`CHAIN` on `Normaliser`, merged by `behaviour_tables()` like `COLD_START`;
`iacsim/graph/normalisers/gcp.py` — declares it; `iacsim/latency/rules/distance.py`
— `{"distance": 0}` when *both* ends are chain subtypes, hops into and out of
the chain priced normally; `iacsim/latency/defaults.yaml` — `forwarding_rule.route`
0 → 2, so the chain head charges the GFE's routing time, ALB parity.
Tests: chain graph spanning two regions, chain-internal hops 0, ends priced.

Verification: `pytest -q` → **381 passed**; ruff clean.
