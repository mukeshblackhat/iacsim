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

### WP7b — the example pair, the fixture tests, repo hygiene (2026-09-08)

**For the user:** `examples/gcp-web` / `gcp-web-bad` are the GCP twins of
`classic-web` / `classic-web-bad`; `iacsim diff` between them reports
`page_load` 160 → 558 ms, all of it the database moving to `europe-west1`. And
four things the vendored fixtures proved wrong are fixed: a Cloud Run
`dynamic "env"` block nested inside `containers {}` is now read (before, the
Memorystore edge of the flagship fixture did not exist), Terraform's `$${`
escape is no longer evaluated as an interpolation, a Private Service Connect
forwarding rule (`load_balancing_scheme = ""`) is no longer a public entry
point, and `provider "kubernetes"` no longer warns that its region did not
resolve. `make check` is green on the whole tree for the first time since the
GCP fixtures landed.

Created: `examples/gcp-web/` (`main.tf`, `variables.tf`, `outputs.tf`,
`scenarios.yaml`) — `internet → global forwarding rule → HTTPS proxy → URL map
→ backend service → serverless NEG → Cloud Run → Cloud SQL (+ Memorystore)`,
one region, no local modules, no `iacsim.yaml` (auto-detection is the
documented choice). The Cloud Run → Cloud SQL edge has both kinds of evidence:
a `DB_INSTANCE` env var naming `connection_name` (plus the
`volumes.cloud_sql_instance` mount) and a `roles/cloudsql.client` grant on the
service account, so the edge's rule is `env_var+gcp_iam_binding`. Every link of
the LB is a step in `scenarios.yaml` — the traversal follows edges, it does not
path-find. `examples/gcp-web-bad/` — the same files; the whole diff is the
header comment, `provider "google" { alias = "db" }`, `provider = google.db` +
`region = var.db_region` on the instance, and the `db_region` variable. A GCP
VPC is global, so there is no peering to add and the graph diff is exactly one
`nodes_moved` entry. `tests/test_example_gcp_web.py` (16 tests: placement,
the internet entering at the chain head only, every chain link with the
attribute it follows in the evidence, exact edge count 8, timing recomputed
from the profile — `2·internet_to_edge + forwarding_rule.route`, four 0 ms
chain hops, `2·same_region_unknown_az + warm + cold·cold_prob`, two
`cloud_sql.read`, `respond` — and `total == sum(hops)` for every scenario).
`tests/test_example_gcp_diff.py` (13 tests: delta `= 2 × 2 reads ×
(cross_region[europe-west1/us-central1] − same_region_unknown_az)` from the
profile, the two Cloud SQL hops the only changed ones, the four chain hops 0 ms
on both sides (G9), one `nodes_moved`, no edges added, the co-locate
recommendation, `--fail-on-regression 50ms` exits 2, `--scenario cached_page`
exits 0).

Modified — tests: `tests/conftest.py` (`gcp_web` / `gcp_web_run` /
`gcp_web_bad` / `gcp_web_bad_run`, session-scoped). `tests/test_real_world.py`
— seven `MIN_NODES` entries at the exact non-network counts `iacsim graph`
reports (`ntier 10, glb-mig 5, multiregion 11, functions 13, gke 2, eventarc
3, lb-regional 5`), `KNOWN_WARNINGS` **unchanged**, `test_run_exits_zero` now
also asserts an inferred scenario exists exactly when the graph has an
`internet` entry, and eight per-fixture tests: the full chain sequence in
`gcp-glb-mig-backend` and the identical edge shape from the `region_*` twins in
`gcp-lb-regional`; both regions reachable from one forwarding rule and every
chain hop 0 ms into both regions in `gcp-cloudrun-multiregion-glb`;
`topic → subscription (PUBLISH) → function (CONSUME)` in
`gcp-functions-firestore-pubsub`; `attrs["workflow"]` and the `Choice → run_job`
edge in `gcp-eventarc-workflows-run`; `kubernetes_config_map` and the
`kubernetes` provider silent in `gcp-gke-multitenant`; `cloud_run → cloud_sql`
(`env_var+gcp_iam_binding`) and `cloud_run → memorystore` (`env_var`) behind the
full chain in `gcp-ntier-serverless-web`, with the PSC endpoint present as a
node but not an entry.

Modified — `iacsim/`, each forced by a fixture:

| file | change | fixture that proved it |
|---|---|---|
| `iacsim/parsers/terraform/loader.py` | new `_evaluate_block`: walks a static block's value and expands any `dynamic` list it meets (the top-level path already did this; a `dynamic "env"` under `template { containers { } }` was left verbatim in the attrs) | `gcp-ntier-serverless-web` — no `REDIS_HOST` edge |
| `iacsim/parsers/terraform/hcl_expr.py` | `_scan_string` treats `$${` as the literal escape it is, kept as written so `heredoc_body`'s existing `$${` → `${` step still applies | `gcp-eventarc-workflows-run` — `not evaluated (trailing tokens … sys.get_env(…))` |
| `iacsim/parsers/terraform/loader.py` | `_REGIONAL_PROVIDERS = {aws, google, google-beta}`: "region not resolved" fires only for those or for a block that declares `region`; `kubernetes` / `random` / `archive` blocks have no region to resolve | `gcp-gke-multitenant` — `provider kubernetes: region not resolved` |
| `iacsim/graph/normalisers/gcp.py` | `_public_chain_head`: a forwarding rule is an entry only when `load_balancing_scheme` is absent, unresolved, or `EXTERNAL*` — `INTERNAL*` and `""` (PSC) are not | `gcp-ntier-serverless-web` — `internet → cloudsql-psc-endpoint` and a phantom inferred scenario |

Modified — hygiene: `Makefile` `examples` target (+6 GCP lines);
`examples/real-world/README.md` (the two rows WP7a recorded with
`state machine definition could not be read` and `trailing tokens` corrected to
what the tool emits now; the "until auto-detection lands" sentence replaced);
`docs/gcp/03-TESTING.md` (checkboxes ticked with a `*WP7b:*` note where the
reality differs from the plan — `MIN_NODES` at exact counts, chain hops are
`{distance: 0, processing: 0}` not `{}`, no cross-region *hop* exists in the
multi-region fixture under G9, `gcp-functions-firestore-pubsub` emits `region
unknown` for its global Pub/Sub resources without `--region` because it has no
provider block).

Judgement calls: `basename()` stays an unknown function (a named `not
evaluated` warning already covered by `KNOWN_WARNINGS`; implementing Terraform
built-ins is not this package's job). Cloud SQL in the example is
`availability_type = "REGIONAL"`, so its `az` is `None` on purpose and the hop
prices as `same_region_unknown_az`. `MIN_NODES` are the exact counts rather
than loose lower bounds so a type dropping out of `TYPE_MAP` fails there first.

Verification: `pytest -q` → **463 passed**; coverage **94 %** (gate 85);
`make examples` green including the six new lines; `ruff` clean;
`tests/fixtures/foosh_report_sha256.txt` unmoved.

---

## Round 2 closed — final verification (2026-09-08)

Six commits on `main` since `237de91`, none pushed (the user's instruction:
commit, do not push):

| commit | work package |
|---|---|
| `1cf53b4` | M11 docs — the plan, G1–G21, type map, testing checklist, corpus |
| `7ba2bb2` | WP1 — loader reads every provider block, not only `aws` |
| `22886a8` | WP2+WP3 — provider-owned tables, the `gcp` normaliser, profile, zero-distance chain |
| `8dbb565` | WP4+WP5 — auto-detection, `--provider`, the five GCP rules |
| `dda9a23` | WP7a — seven public fixtures vendored |
| `c8f92d1` | WP7b — example pair, fixture tests, four parser bugs, audit fixes |

**AWS is byte-identical.** The pre-M11 tree (`237de91`) was checked out into a
throwaway worktree and every AWS example was run through both versions with the
same `.venv`, `--region us-east-1`, `graph` then `run -o json`. After scrubbing
absolute paths, `graph.json` and `report.json` were **identical for all ten**:
`classic-web`, `classic-web-bad`, `order-queue`, `foosh-serverless`, `foosh-cfn`,
and the five `real-world/` AWS fixtures. Stderr (the warnings) was identical
too. The two deliberate AWS changes — the `kinesis` profile block and Firestore
/ Spanner / Bigtable placement — touch no AWS example, which is why nothing
moved; the guard-rail test is what proves `kinesis` is now priced.

**GCP works on real code.** Seven unmodified public fixtures (three upstream
repos, Apache-2.0, commits pinned) graph, run, and — where they have a public
entry — infer a scenario. No node has `region=None`; no `google_*` type falls
through to "unknown". The controlled pair `gcp-web` / `gcp-web-bad` gives the
GCP diff story a known delta: **160 → 558 ms, +398 = 2 reads × 2 × (100 − 0.5)**,
the chain contributing 0 ms on both sides.

**Independent audit** (a reviewer agent, read-only) found two real placement
gaps by code reading — Firestore `location_id` and Spanner `config` — both
fixed in `c8f92d1` with a regression test; it confirmed the AWS/GCP `CHAIN`
isolation, `INVOKE_KEYS` coverage, chain pricing at both ends, `detect_provider`
edge cases and single-charged Pub/Sub delivery. It could not run the baseline
diff (no shell); that was run by the lead, above.

**Gate:** `make check` → ruff clean, **463 passed**, coverage **93.5 %**
(85 % required); `make examples` green.

### Left open, on purpose

- GCP capacity modelling (`--walker load`) — G6; `simulator/capacity.py` and
  `analyzer/saturation.py` are still AWS-only and return nothing for GCP nodes.
- A Cloud Monitoring `MetricSource` so `iacsim calibrate` works on GCP.
- `basename()` and other Terraform built-ins the evaluator lacks (named warning).
- `allUsers` + `roles/run.invoker` does not draw an `internet` edge, so a public
  Cloud Run with no load balancer infers no scenario (G13 open question).
- 58 unticked boxes in `03-TESTING.md`, each annotated — mostly parser-level
  unit tests that the fixture tests now cover end-to-end, and the CI matrix.

### Closing fixes (2026-09-08)

`iacsim run examples/gcp-web` described a Cloud Run cold start as "1 Lambda
invocation(s)" and recommended "provisioned concurrency … SnapStart". Three
analyzers (`per_category`, `recommendations`, `tail_risk`) hardcoded the word;
`analyzer/_common.py` now names the service from the node's subtype
(`cold_start_service`) and picks the remedy per platform (`cold_start_fix`:
Lambda → provisioned concurrency, unchanged; anything else → a minimum instance
count). Lambda wording is byte-identical, so `foosh_report_sha256.txt` is
unmoved. `README.md` "GCP input" and `TIMELINE.md` M11 (🔨 → ✅) updated to the
landed state.
