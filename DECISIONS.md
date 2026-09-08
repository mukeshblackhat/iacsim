# Decisions — every choice we made, and why

iacsim reads Infrastructure-as-Code (Terraform, CloudFormation), draws a map of what talks to
what, walks a request across that map, and says where the milliseconds go — before deploy.
It is a **base project**: others will add clouds, parsers, rules and reporters. So every choice
that shaped it is written down here in the same shape — *the question we faced · the options
we had · what we chose · why · where it shows in the code* — so a newcomer can see not just
what the code does, but why it is that way, and how to change it without breaking the rest.

Reading tips: `path:line` points into this repo. "IR" = the plain Python dataclasses every
stage hands to the next (`iacsim/core/models.py`). A "registry" is a name → class map; config
picks implementations by name. A "hop" is one step of a request (API Gateway → Lambda).
Line numbers marked `~` are approximate for files being edited in the current pass.

---

## 1. The spec — seven questions answered before any code (Q1–Q7)

| id | The question | Options we had | What we chose | Why | In code |
|---|---|---|---|---|---|
| **Q1** | Which language? | Python · TypeScript · Go | **Python ≥ 3.12** with `python-hcl2`, `typer`, `pyyaml`, `rich` | The engine is 90 % parsing + arithmetic; `python-hcl2` is the strongest Terraform (HCL) parser outside Go; TypeScript only wins if the engine must run in the browser, and our viewer just reads JSON. Known cost: no in-browser engine. | `pyproject.toml:5`, `iacsim/cli.py:21` |
| **Q2** | CLI or web UI first? | Web UI from day one · CLI + JSON, viewer later · CLI only | **CLI + JSON now, static viewer in M6** — the viewer became the dashboard in M15 (D54): `-o html` and `iacsim view` render one page from the same `report.json` | `graph.json`/`report.json` are the contract; a viewer bolts on without touching the engine — M15 proved it: the whole dashboard is a reporter, no engine file changed. | `iacsim/viewer/__init__.py:1-22`, `iacsim/reporter/json_.py:1-38`, `iacsim/reporter/html_.py:1` |
| **Q3** | How do we know the order a request flows? Terraform never says. | Require a scenario file · infer only · **infer + optional overrides** | **Infer paths from IaC evidence; `scenarios.yaml` wins when present** | Works on a fresh repo with zero config; every inferred hop carries an *evidence* sentence so a wrong guess is visible and fixable. | `iacsim/core/pipeline.py:94-104`, `iacsim/scenarios/yaml_file.py:41`, `iacsim/scenarios/inferred.py:59` |
| **Q4** | Where do latency numbers come from? | Hard-coded · measured only · **defaults + overrides, calibration designed in** | **Built-in defaults + `--profile`; the file schema has slots for measured numbers from day one** | Comparative answers ("moving the DB costs ~300 ms") are honest with public figures; calibration needs cloud credentials, so it stays optional. | `iacsim/latency/defaults.yaml:1-3`, `iacsim/latency/profile.py:35`, `iacsim/core/models.py:203` |
| **Q5** | Which input formats? | Terraform only · Terraform + CloudFormation · + read CDK/Pulumi source | **Terraform primary, CloudFormation adapter (M5)**; CDK source reading deferred | CloudFormation is what CDK, SAM and Serverless Framework all emit, so a real CDK project becomes a test case with no rewriting. | `iacsim/parsers/detect.py:10-22`, `iacsim/parsers/cloudformation/parser.py:56` |
| **Q6** | One number or a distribution? | Deterministic only · Monte-Carlo only · **both behind one interface** | **Deterministic in M2, Monte-Carlo `--samples N` in M6** | Explainable first; the profile already stores `cold_prob`, so sampling is additive — same graph, same profile, different walker. | `iacsim/simulator/walkers/expected_value.py:21`, `iacsim/simulator/walkers/monte_carlo.py:46` |
| **Q7** | Which example first? | The real startup stack · a trivial fixture · **both, small first** | **`classic-web` → `classic-web-bad` → `foosh-serverless`** | A trivial fixture gets the whole pipeline green in hours; the real (Foosh) stack exercises Step Functions inference and later doubles as the CloudFormation correctness check. | `examples/classic-web/`, `examples/foosh-serverless/`, `examples/foosh-cfn/` |

---

## 2. Architecture — how the pieces fit (D8–D11, D17–D22)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D8** | Should every choice be swappable? | Hard-code and refactor later · **interface + registry + config key for every choice** | **Plug-and-play everywhere — 10 extension points** (parser, normaliser, inference rule, scenario source, profile source, cost rule, walker, analyzer, reporter, metric source) | The user's rule: "everything plug-and-play so it can be changed later". Cost: a few hundred lines of scaffolding. Payoff: a Datadog source, an inference rule or a new reporter is one file, no fork. **Amended by M11 (§10, G4):** a new *cloud* is not. GCP also needs the engine's AWS-subtype tables to become provider-owned and the provider to be auto-detected — two edits inside `core/`, not registrations. | `iacsim/core/interfaces.py:151-160`, `iacsim/core/registry.py:35`, `plugins/README.md` |
| **D9** | How do stages talk? | Direct calls / shared state · **one fixed IR** | **IR-only contract, versioned (`schema_version`)** | A new parser or latency model never touches the simulator; old `graph.json` stays readable. | `iacsim/core/models.py:1-15`, `iacsim/core/models.py:122` |
| **D10** | Where do settings come from? | Flags only · file only · **layered** | **CLI flags > `iacsim.yaml` in the target dir > built-in `DEFAULTS`**; every key optional | Zero config works; a team pins choices per repo; a flag wins for one run. | `iacsim/core/config.py:16-68`, `iacsim/core/config.py:95` |
| **D11** | Bug: a `--profile` set in one run leaked into the next in-process `Config` | Keep the shallow copy · **real deep copy** | **`copy.deepcopy(DEFAULTS)`** | Found by the CLI tests in M6; the first-level dicts were shared with `DEFAULTS`. | `iacsim/core/config.py:111-114` |
| **D17** | Must an inferred edge justify itself? | Silent edges · **mandatory evidence** | **Every edge carries `evidence` + `confidence`** | So a wrong guess is visible in the hop table and correctable in `scenarios.yaml`. | `iacsim/core/models.py:111-119`, `iacsim/graph/inference/step_functions.py:175` |
| **D18** | Two rules propose the same edge — who wins? | Last wins · merge · **first wins** | **First rule in `inference.rules` order wins; dedupe on (src, dst)** — config order *is* priority | Simple and predictable. **Known cost:** `env_var` (READ) hides `iam_policy` (WRITE) for the same pair — 46 of the 48 twin-vs-CDK differences. Being replaced by an order-independent merge in the current pass (see §9). | `iacsim/core/pipeline.py:53-58`, `iacsim/core/config.py:22-31` |
| **D19** | One edge per pair, so read vs write per step? | Model multi-edges · **one edge + per-step `op`** | **One edge; a scenario step says `op: write` to re-price** | Keeps the graph small; the walker re-prices with the same rules. Cost: inferred scenarios can't know a write. | `iacsim/simulator/traversal.py:~197 _hop`, `iacsim/core/models.py:157-174` |
| **D20** | Unknown resource types | Crash · drop · **keep** | **Glue types (IAM, security groups) dropped silently; anything else kept as a `network` node + warning** | Nothing vanishes without a trace. | `iacsim/graph/normalisers/aws.py:85`, `iacsim/graph/normalisers/aws.py:112` |
| **D21** | Where does a request start? | Require the user to name a node · **synthesise one** | **The normaliser adds one `internet` node with an edge into every gateway / load balancer / CDN** | Entry points become discoverable; inferred scenarios start from `internet`. | `iacsim/graph/normalisers/aws.py:137` |
| **D22** | A Terraform reference we cannot evaluate (`aws_vpc.this.id`) | Fail · drop · **placeholder** | **Values render as `"${address.attr}"` placeholders — one convention for both parsers** | We are not Terraform; we never talk to a provider. The placeholder is enough for inference rules to find "this mentions that". | `iacsim/core/refs.py:32`, `iacsim/parsers/terraform/evaluator.py:42`, `iacsim/parsers/terraform/evaluator.py:325` |

---

## 3. Parsers — reading Terraform and CloudFormation (D23–D27)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D23** | HCL we cannot resolve (remote modules, `data`, `cidrsubnet()`, splats) | Crash · **warn** | **Warning, never a crash — the attribute keeps its raw text** | A real repo always has something we haven't seen; the report must still come out. Cost: `data.*` currently resolves silently (fixed in the current pass). | `iacsim/parsers/terraform/parser.py:16-19`, `iacsim/parsers/terraform/loader.py:291-309`, `iacsim/parsers/terraform/loader.py:250-266` |
| **D24** | `jsonencode()` / `templatefile()` results | Render to a string · **keep structured** | **`jsonencode` stays a dict; `templatefile` becomes `{"__templatefile__": {path, vars}}`** | Inference rules can read *inside* an IAM policy or a Step Functions definition instead of regex-ing a string. | `iacsim/parsers/terraform/evaluator.py:325-347`, `iacsim/graph/inference/step_functions.py:76`, `iacsim/graph/inference/env_var.py:33` |
| **D25** | CloudFormation without forking the normaliser and 8 rules | Second normaliser + CFN-aware rules · **canonicalise to Terraform shape** | **`AWS::Lambda::Function` → `aws_lambda_function`, `MemorySize` → `memory_size`, `Ref`/`GetAtt` → the same placeholders, address = `<tf type>.<LogicalId>`** | One file (`canonical.py`) and the normaliser plus all 8 rules run unchanged. Verified: the real CDK output and the hand-written twin agree on 146 edges. | `iacsim/parsers/cloudformation/canonical.py:127`, `iacsim/parsers/cloudformation/intrinsics.py:62`, `iacsim/parsers/cloudformation/parser.py:59` |
| **D26** | CloudFormation `Conditions` | Evaluate them · **take one branch** | **Not evaluated; `Fn::If` takes the true branch with a warning; `FindInMap`/`ImportValue`/`Cidr` kept as markers** | Conditions need parameters we don't have; a warning is honest. | `iacsim/parsers/cloudformation/intrinsics.py:114` |
| **D27** | A CFN template has no provider block — which region? | Always `us-east-1` · **guess, then flag** | **`--region` / config → first region literal in the template (CDK bakes it into ARNs) → `us-east-1`** | CDK templates almost always carry the region in an ARN. | `iacsim/parsers/cloudformation/parser.py:83`, `iacsim/parsers/cloudformation/template.py:51` |

---

## 4. Latency numbers and profiles (D28–D32, D51–D52)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D51** | What are we claiming? | Predict absolute latency · **compare designs** | **Comparative. Defaults are "approximate public figures, not truth"; every report header prints which profile rung produced the numbers** | Relative answers stay true even if base numbers are 30 % off; the rung line keeps everyone honest. | `iacsim/latency/defaults.yaml:1-3`, `iacsim/latency/profile.py:49` |
| **D52** | Application code (what runs *inside* a Lambda)? | Parse source · ignore · **absorb via measurement** | **Layer A (infra) modelled; Layer B (code) absorbed through calibration** because a measured Lambda duration includes the code and its third-party calls | Proof: the 18 s FAL call moves Foosh from 184 ms to 19.7 s with no code analysis. | `SPEC.md:11-47`, `iacsim/analyzer/_common.py:9-15` |
| **D28** | Key calibrated numbers by node id or physical name? | `per_resource` (node id) only · **`by_label` (physical name) preferred** | **`by_label` when the name is unique in the graph, else `per_resource`** | One calibrated file serves both the Terraform and the CloudFormation graph of the same stack. | `iacsim/latency/calibrate/calibrator.py:84`, `iacsim/latency/defaults.yaml:31-34` |
| **D29** | Override semantics | Replace the whole block · **merge per key** | **`defaults[subtype] ← by_label[label] ← per_resource[id]`** | An override can set only `warm` and inherit `cold`/`cold_prob`; before M7 a partial block silently dropped the cold start. | `iacsim/core/models.py:203-215`, `iacsim/latency/profile.py:35` |
| **D30** | M7: build against a live AWS account or a fake? | (a) live CloudWatch · **(b) fake source, CloudWatch shipped unrun** | **(b)** — everything tested through `fake`; the live run is written up as a 4-command recipe | Account, region, credentials and even the monitoring system are company-specific; they belong in the user's config, not in our tests. | `iacsim/latency/calibrate/fake.py:27`, `iacsim/latency/calibrate/cloudwatch.py:1-13`, `TIMELINE.md:388-401` |
| **D31** | Where is the monitoring system chosen? | Hard-code CloudWatch · **registry + config** | **`calibrate.source` + `calibrate.sources.<name>` ctor options; a Datadog source is a 15-line plugin; credentials never in the file, only env-var *names*** | Same plug-and-play rule as everything else. | `iacsim/core/interfaces.py:107-146`, `iacsim/latency/calibrate/__init__.py:25`, `iacsim/core/config.py:57-67` |
| **D32** | boto3 dependency | Hard dependency · **optional + lazy** | **`iacsim[calibrate]` extra, imported lazily; missing SDK/creds → one-line hint, exit 3** | The engine must run offline with four libraries. | `pyproject.toml:14`, `iacsim/latency/calibrate/cloudwatch.py:71`, `iacsim/core/interfaces.py:144` |

---

## 5. Simulation — how a request is walked and priced (D12–D16, D33–D37, D43)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D12** | Two walkers = two traversals? | Duplicate the walk in each · **one planner + pluggable pricing** | **One traversal (`Planner`), three backends (expected, Monte-Carlo, contended)** | The walkers cannot drift apart; M6 landed without changing one M2 number. | `iacsim/simulator/traversal.py:~111 Planner`, `iacsim/simulator/traversal.py:~287 Backend` |
| **D13** | A scenario step has no direct edge — what do we charge? | Fail · always synthesise · **a fallback ladder** | **direct → response leg → via caller → synthetic + warning** | "Via caller" is how *api reads table A then B* and every Step Functions worker get charged to the real caller, not the previous node. | `iacsim/simulator/traversal.py:~210 _edge_for` |
| **D14** | Network cost one-way or round-trip? | Rules emit round-trip · **rules one-way, walker doubles** | **Distance ×2 for synchronous kinds (invoke/read/write/route), ×1 for publish/consume/peer; response legs drop distance** | One rule table, and fire-and-forget hops stay honest. **Known cost:** response legs still re-charge processing and cold start — fixed in the current pass. | `iacsim/simulator/traversal.py:~63 SYNCHRONOUS`, `iacsim/simulator/traversal.py:~236 _charge` |
| **D15** | Cost of a parallel group | Sum · **max** | **max(branches); only the slowest branch is "on the critical path"; the saving is reported as a shape line** | That is what concurrency buys you. | `iacsim/simulator/traversal.py:~346 _group`, `iacsim/analyzer/per_category.py:103` |
| **D16** | Cost of a fan-out (Step Functions `Map`, SQS batch) | count × cost · 1 × cost · **waves** | **M2: one copy. M8: `ceil(count / MaxConcurrency)` sequential waves** | 10 items with concurrency 5 is two rounds, not one. Since the deep-look pass the *innermost* Map enclosing the target sets the concurrency (P6). | `iacsim/simulator/traversal.py:~135 _walk_steps`, `iacsim/simulator/traversal.py:~165 _map_concurrency` |
| **D33** | Contention under load | Discrete-event simulation · **analytic queueing** | **M/M/c (Erlang-C) per resource, no new dependencies** | Fast, deterministic, every number traces to a formula. Not modelled: burstiness, warm-up. | `iacsim/simulator/capacity.py:158`, `iacsim/simulator/walkers/load.py:101` |
| **D34** | How long does a Lambda hold its concurrency slot? | Its own service time · **the whole invocation** | **Own time + everything it waits on downstream** — printed as an assumption in every capacity report | That is how Lambda concurrency actually works. | `iacsim/simulator/walkers/load.py:136`, `iacsim/simulator/walkers/load.py:170` |
| **D35** | Lambdas without reserved concurrency | One resource each · **one shared pool** | **`account_concurrency − Σ reserved`, shared by all unreserved functions** | That is how AWS allocates them. | `iacsim/simulator/capacity.py:34`, `iacsim/simulator/capacity.py:105` |
| **D36** | p99 under load without sampling | Monte-Carlo per user count · **a stated factor** | **`p99 ≈ tail_factor (1.3) × expected + Σ p99 queue waits`, configurable and printed** | Cheap and visible. Known cost: it ignores per-node sigma; a magic number, at least an honest one. | `iacsim/core/config.py:46`, `iacsim/simulator/walkers/load.py:253` |
| **D37** | Shape of the load walker's output | One Result per (scenario, users) · **one per scenario** | **`total_ms`/`hops` stay the no-contention numbers; the sweep lives in `Result.load`** | Every other analyzer keeps working unchanged. | `iacsim/core/models.py:237-251`, `iacsim/reporter/json_.py:94` |
| **D43** | Monte-Carlo speed | Require numpy · **optional** | **numpy optional (`iacsim[montecarlo]`); pure-Python sampler otherwise** | Four required libraries, always. Cost: the numpy path is untested until numpy becomes a dev dependency (current pass). | `iacsim/simulator/walkers/monte_carlo.py:130`, `iacsim/simulator/walkers/monte_carlo.py:169` |

---

## 6. Analysis and reports (D38, D44–D47, D54)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D38** | Recommendation savings | Re-simulate each · **first-order estimates** | **Estimates, max 5 lines, each citing the hops it summed** | Honest and cheap; validated within 1 % against the measured 298.8 ms delta. | `iacsim/analyzer/recommendations.py:31-34`, `iacsim/analyzer/recommendations.py:38` |
| **D44** | Analyzers that only apply sometimes | Conditional config · **on by default, silent when N/A** | **All on; each returns `[]` when not applicable** (critical path needs parallelism, tail risk needs samples, saturation needs a load sweep) | No config to forget. | `iacsim/core/config.py:49-50`, `iacsim/analyzer/tail_risk.py:25`, `iacsim/analyzer/saturation.py:32` |
| **D45** | How does `diff` line things up? | By index · by name · **label + occurrence** | **Nodes by id (`--align-by label` for Terraform-vs-CFN); scenarios by name; hops by label + occurrence; `|Δ| < 0.05 ms` = unchanged** | Works across the two input formats. Cost: a renamed resource shows as removed + added. | `iacsim/diff/differ.py:74`, `iacsim/diff/differ.py:164`, `iacsim/diff/models.py:11` |
| **D46** | CI signal for a latency regression | Report only · **non-zero exit** | **`--fail-on-regression 50ms|10%` → exit 2** | That is what "test infrastructure changes" means in a PR check. | `iacsim/cli.py:134`, `iacsim/diff/differ.py:211` |
| **D47** | Where does each reporter's output go? | All to stdout · **text to terminal, others to files** | **`text` → stdout (colour only on a TTY); everything else → `<out_dir>/report.<ext>`** | Terminal stays readable; JSON/markdown are for tools and PR comments. | `iacsim/cli.py:55`, `iacsim/reporter/text.py:25` |
| **D54** | How should the visual output ship? | A console with history (server, accounts, a database of runs) · upgrade the bare graph viewer only · **one page per run, data inlined, no server** | **`iacsim run … -o html` → a self-contained `report.html` (`diff.html` for `iacsim diff`); `iacsim view` renders the same template from `report.json` and serves it. The schema-2 payload (`report_payload()`, shared with the `json` reporter) is inlined into `viewer/index.html` at one placeholder; no fetch, no library, no build step. The map is the first screen with time drawn on it (arrow width = hop ms, colour = dominant category, slowest hop marked); the page explores a run and never edits one — what-if stays `iacsim diff`. `html` is not in the default `report.outputs` (~400 KB per run). The sample under `examples/dashboard/` is real output held equal by a test.** | `report.json` already carried three times what the old viewer drew (shares, bottlenecks, recommendations, the capacity sweep), so this is a rendering job — every decision that *can* be Python (payload, section titles, escaping, substitution, `prepare`) is Python and tested; the JS is invisible to the coverage gate by design. One file opens from `file://` and can be sent to anyone; a console would need a server and a place to keep runs, which is not the problem statement. Section titles are held to `BriefBuilder` by a drift test so page and terminal never disagree; `_embed` escapes `<` so a `</script>` in evidence text cannot break the page. | `iacsim/reporter/html_.py:1` `render_page()`, `_embed()`, `SECTION_TITLES`; `iacsim/reporter/json_.py:101` `report_payload()`; `iacsim/viewer/__init__.py:43` `prepare()`; `iacsim/reporter/writer.py:13`; `iacsim/viewer/index.html:1` |

---

## 7. Inference and scenarios (D48–D50)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D48** | Step Functions `Choice` when replaying a workflow | Follow `Default` · **pick a representative branch** | **The branch with the most Task states (worst case); a Choice of only Waits takes the empty branch** | The ASL default is often the error path; the busiest branch is what users pay for. Cost: no per-user override yet. | `iacsim/scenarios/inferred.py:133` |
| **D49** | How many scenarios to infer | Every path · **a capped representative set** | **One path per (entry, leaf subtype), orchestrator paths first, max 5 per entry, depth ≤ 8** | Foosh yields 3 scenarios, not 176. | `iacsim/scenarios/inferred.py:39`, `iacsim/scenarios/inferred.py:145` |
| **D50** | Declared vs inferred scenarios | Merge · **declared wins** | **Sources run in `scenarios.sources` order; first name wins** | Inference only fills gaps. | `iacsim/core/pipeline.py:94-104` |

---

## 8. Process and hygiene (D39–D42, D53)

| id | The question | Options | Chose | Why | In code |
|---|---|---|---|---|---|
| **D39** | Shipping a real company template as a fixture | Don't ship · **redact** | **11 secret values → `REDACTED`, account id faked, asserted by a test; nothing under the source repo modified.** Separately flagged: plaintext API keys in that repo's config. | The real `cdk synth` output is the only ground truth we have. | `examples/foosh-cfn/README.md`, `tests/test_cloudformation_parser.py:186` |
| **D40** | Generic registry typing | `TypeVar` + `Generic[T]` · **PEP 695** | **`class Registry[T]`** (Python 3.12 syntax) | Matches the 3.12 floor from Q1. | `iacsim/core/registry.py:35` |
| **D41** | How is "nothing is broken" proved? | Manual · **CI gate** | **`make check` = ruff + pytest + coverage ≥ 85 %, installed as a pre-commit hook** | A red commit is physically rejected. | `Makefile:17-29` |
| **D42** | `networkx` (named in Q1) | Keep · **drop** | **Dropped** — the graph is dataclasses + a list of edges | Never imported. (SPEC still lists it — being fixed.) | `pyproject.toml:6-11`, `iacsim/core/models.py:122` |
| **D53** | Parked, deliberately | — | CDK Python source reading; non-AWS providers; X-Ray as an inference validator; live CloudWatch run; discrete-event capacity; calibrating capacity numbers | Each is additive behind a registry; none blocks the problem statement. | `TIMELINE.md:444-450` |

---

## 9. Decisions we are making in the current pass (the "deep look")

These come from three review passes and are being implemented now; numbers users see **will change on purpose** (Foosh `poll_status` 106 → 81 ms, `run_workflow_3_nodes` 184 → 309 ms; classic-web totals unchanged).

| id | The question | Options | What we chose | Why | Where |
|---|---|---|---|---|---|
| **P1** | A response leg (the reply back to the caller) — what does it cost? | Re-charge everything (before this pass) · re-charge processing only · **charge a named `respond` key, default 0, no cold start** | **`respond` (default 0) + no distance + no cold start** | The forward hop's `warm` is the *whole* invocation as CloudWatch measures it — composing the reply is inside it. Before this pass 42 % of Foosh `poll_status` is this duplicate, and a Lambda that is provably warm was being charged a cold start. | `iacsim/simulator/traversal.py:~236 _charge`, `iacsim/latency/defaults.yaml` |
| **P2** | Step Functions transition cost — once or per state? | Once on entry (before this pass) · **on every hop out of an orchestrator** | **New `transition` cost rule, on by default** | A 12-task workflow was missing ~275 ms. | `iacsim/latency/rules/` (new `transition.py`), `iacsim/core/config.py:39` |
| **P3** | A synthetic INVOKE into a table costs zero processing | Leave it · **fall back to the destination's own key** | **If the op key is absent, use the subtype's INVOKE key (dynamodb → `read`, sqs → `publish`)** | The case with the least information was the cheapest hop. | `iacsim/latency/rules/processing.py:24` |
| **P4** | Unknown region silently priced as same-region (0.5 ms not 120) | Leave it · **warn** | **Either region `None` → one warning per node naming the fallback** | This silently destroys the tool's headline claim. | `iacsim/latency/rules/distance.py:~18` |
| **P5** | Two rules, same pair, different kind (`env_var` READ vs `iam_policy` WRITE) | First wins (before this pass) · **merge** | **`Edge.ops` = union; `kind` by priority `[INVOKE, ROUTE, CONSUME, PUBLISH, READ, WRITE, PEER]` — READ over WRITE** | A request path *reads* by default; `op: write` in a scenario overrides. Order-independent by construction; the hop table says "(also may write)". | `iacsim/core/pipeline.py:53-58`, `iacsim/core/models.py:111` |
| **P6** | Which Map sets the fan-out concurrency? | First top-level Map (before this pass) · **innermost enclosing Map** | **Recurse into Choice/Parallel bodies; use the innermost Map enclosing the target; disagreement → largest + warning; `fanout.concurrency` override in YAML** | On the real CDK template every Map sits inside a Choice, so waves never fired. | `iacsim/simulator/traversal.py:~165 _map_concurrency` |
| **P7** | Fan-out offered load | Per wave (today; 5× low for Lambdas, 2× high otherwise) · **per copy** | **erlangs = copies × one-copy hold** | The two code paths disagreed with each other. | `iacsim/simulator/walkers/load.py:170` |
| **P8** | Errors reach the user as tracebacks | Leave · **one boundary** | **One CLI error boundary; exit codes 0 ok · 1 problems found · 2 input error / regression · 3 metric source unusable** | The messages were already good (including "did you mean"); they were buried. | `iacsim/cli.py` |
| **P9** | `--profile` is cwd-relative, `--load` is target-relative | Leave · **one rule** | **Resolve relative to the target dir first, then cwd, else name both** | `iacsim calibrate <t>` then `--profile calibrated.yaml` failed — the documented flow. | `iacsim/cli.py`, `iacsim/latency/profile.py:26` |
| **P10** | Reading a live cloud account (no IaC at all) | Build now · **build last, behind a registry** | **`INVENTORY_SOURCES` registry configured like `calibrate.sources`; AWS/Azure/GCP readers are plugins; read-only permissions; tested with a fake inventory** | "All should be plug and play" — and it is company-specific by nature. | plan Phase 3, M14 |
| **P11** | Every new cloud / source | Engine edits · **registrations only** | **Parsers, normalisers, rules, defaults, inventory sources, second inputs are all registered implementations; acceptance = `iacsim plugins` lists them and `core/`, `simulator/`, `analyzer/`, `reporter/` are untouched** | Base project. | `iacsim/core/interfaces.py:151-160` |

---

## 10. GCP — M11 (G1–G6)

The first non-AWS cloud. The long form — the options weighed, the evidence behind every
`google_*` type, the fixtures — is in `docs/gcp/01-DECISIONS.md`; these are the rows that
belong in this index. Status and the three traps found are in `TIMELINE.md` (2026-09-08).

| id | The question | Options we had | What we chose | Why | In code |
|---|---|---|---|---|---|
| **G1** | How does iacsim know which cloud a directory is? | Require `provider:` in `iacsim.yaml` · a `--provider` flag · **read it off the resource types** | **Auto-detect from resource-type prefixes (`google_*` → `gcp`); an explicit `provider:` still wins** | Zero config is the promise (D10), and the type prefix is evidence every IaC format already carries. Cost: `build_graph` gains a detection step, so this is an engine edit inside `core/`, not a registration. | `iacsim/core/pipeline.py:51`, `iacsim/core/config.py:19` |
| **G2** | How wide is the first GCP slice? | One vertical (Cloud Run only) · **40+ types at once** | **Broad: compute, serverless, the HTTP LB chain, data stores, messaging, orchestration, networking** | A narrow slice turns most of a real GCP repo into `network` placeholder nodes (D20), which reads as "it does not understand GCP". Cost: 40+ type-map rows, each needing its own evidence. | `iacsim/graph/normalisers/aws.py:41`, `iacsim/graph/normalisers/aws.py:74` |
| **G3** | GCP's HTTP load balancer is 4–5 Terraform resources — one node or many? | Collapse to a single `lb` node · **keep every resource as its own node** | **Forwarding rule → target proxy → URL map → backend service → NEG stay separate; the internal chain hops are priced at 0 ms** | The graph has to be recognisable to whoever wrote the Terraform, and collapsing hides the URL map, which is where routing mistakes live. 0 ms is the honest number — Google publishes no per-stage figure — and the extra hops still show in the A3 shape line. | `iacsim/analyzer/per_category.py:30`, `iacsim/latency/defaults.yaml:11` |
| **G4** | The engine holds AWS-only subtype tables. Who owns them? | Add GCP keys to the existing tables · **each `Normaliser` declares its own** | **Behaviour tables become provider-owned; `AwsNormaliser` receives today's tables unchanged, `GcpNormaliser` declares its own** | Three tables decide answers *silently*: a subtype missing from the invoke-key table makes the hop cost nothing, the cold-start rule fired only for `subtype == "lambda"`, and `MetricSource.KINDS` enumerates AWS subtypes inside the ABC. Done in WP2: `INVOKE_KEYS` / `COLD_START` live on each `Normaliser`, merged by `behaviour_tables()`; the AWS tables moved verbatim. Adding GCP keys to AWS tables works once and rots at Azure. Cost: the first real engine change of Phase 3 (amends D8, P11). | `iacsim/core/interfaces.py:67-68`, `iacsim/core/interfaces.py:217`, `iacsim/latency/rules/processing.py:47`, `iacsim/latency/rules/cold_start.py:21`, `iacsim/core/interfaces.py:156` |
| **G5** | Where do GCP's own notes live? | Inline in these files · a separate repo · **`docs/gcp/`** | **`docs/gcp/` — overview, decisions, type map, testing, changes; the root documents keep one row each and link down** | This file stays a one-page index of every choice; 40+ rows of per-type evidence would drown it. | `docs/gcp/01-DECISIONS.md` |
| **G6** | GCP capacity (`--walker load`) now or later? | With the first slice · **later** | **Deferred to a later work package; the load walker stays AWS-shaped until then** | Capacity needs per-service concurrency semantics — Cloud Run's per-instance concurrency is not Lambda's one-request-per-slot (D34, D35) — and a second set of numbers to justify. Latency first; the walker stays selectable and simply finds no GCP capacity attrs. | `iacsim/simulator/walkers/load.py:101`, `iacsim/simulator/capacity.py:34` |

Known cost of G1 and G4 together: Phase 3's "only new registrations" rule (`TIMELINE.md`, the
rule under the Phase 3 roadmap) does not hold for a cloud. Two further engine spots follow the
same pattern and are fixed with the GCP slice: `display_name` strips `.aws_` only, so GCP node
ids render unshortened (`iacsim/core/models.py:252`), and `MetricSource.KINDS` enumerates AWS
subtypes inside the ABC (`iacsim/core/interfaces.py:156`).

---

## 11. Parked on purpose

- Reading CDK / Pulumi *source* directly — `cdk synth` / `pulumi stack export` output is enough.
- X-Ray / OpenTelemetry traces as an edge *validator* — rung 3 of the profile ladder.
- Discrete-event capacity simulation (burstiness, warm-up) — the analytic model answers "how many users" first.
- Calibrating capacity numbers (not just latency) from metrics.
- A live CloudWatch run against a real account — `aws configure` + one command, when credentials exist.

## 12. How to change a decision

1. **A number** (latency, capacity, sigma): put it in a profile file and pass `--profile`; never edit `defaults.yaml` for one team.
2. **A behaviour with a config key** (which rules run, which walker, which analyzers, which reporters): edit `iacsim.yaml` next to the Terraform; every key has a default.
3. **A behaviour without a config key**: it is an extension point — write a class against the ABC in `iacsim/core/interfaces.py`, decorate it with the matching registry, drop it in `plugins/`, name it in `iacsim.yaml`.
4. **The engine itself** (traversal, IR, pipeline order): open a row here first — question, options, choice, why — then change the code and the tests that encode the old choice.
