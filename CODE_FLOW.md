# Code flow — what calls what, and why

This document follows the code the way the tool follows a request: from the command you type
to the number it prints. Every step names the function and where it lives (`path:line`;
`~line` means approximate — that file is being edited in the current pass, so use the function
name). Section 2 has a worked example you can check against `iacsim run examples/classic-web-bad`.

Companion documents: `DECISIONS.md` (why things are this way), `SPEC.md` (the design),
`TIMELINE.md` (what was built when).

---

## 1. The map and the objects

One line (`iacsim/__init__.py:1-7`):

```
IaC files ─parser─► RawResources ─normaliser─► InfraGraph ─inference─► InfraGraph (+edges)
   ─cost rules─► InfraGraph (+Latency per edge) ─walker─► Result ─analyzers─► Findings ─reporter─► output
```

Each arrow is an **extension point**: an abstract class in `iacsim/core/interfaces.py`, a
registry that maps a name to a class, and a key in `iacsim.yaml` that picks the name. The
objects that travel between stages are plain dataclasses in `iacsim/core/models.py` — the IR.

| Object | Defined at | Produced by | Consumed by |
|---|---|---|---|
| `RawResource` / `RawResources` — one resource exactly as the IaC declared it, plus the batch | `iacsim/core/models.py:21`, `iacsim/core/models.py:41` | `iacsim/parsers/terraform/parser.py:38`, `iacsim/parsers/cloudformation/parser.py:59` | normaliser, all inference rules |
| `InfraGraph` (`Node`, `Edge`, `Placement`) — the neutral map | `iacsim/core/models.py:122`, `:101`, `:111`, `:81` | `iacsim/graph/normalisers/aws.py:112` + `iacsim/core/pipeline.py:53-58` | cost rules, scenario sources, walkers, analyzers, viewer |
| `Latency` on each edge | `iacsim/core/models.py:89` | `iacsim/core/pipeline.py:87` via `make_pricer` (`:72`) | the traversal's `_charge` |
| `Profile` — merged latency/capacity numbers | `iacsim/core/models.py:188` | `iacsim/core/pipeline.py:62` → `iacsim/latency/profile.py:35` | cost rules, Monte-Carlo backend, capacity |
| `Scenario` / `Step` — a request path | `iacsim/core/models.py:177`, `:157` | `iacsim/core/pipeline.py:94` from `iacsim/scenarios/yaml_file.py:41` and `iacsim/scenarios/inferred.py:59` | `Planner.plan` |
| `Result` / `HopResult` — one walked scenario | `iacsim/core/models.py:237`, `:221` | `iacsim/simulator/traversal.py:~372 build_result` | analyzers |
| `Findings` / `Finding` — report lines | `iacsim/core/models.py:277`, `:255` | `iacsim/core/pipeline.py:121` | reporters, differ |
| `DiffReport` and friends | `iacsim/core/models.py:437` (and `:319` onward) | `iacsim/differ.py:69` | `Reporter.render_diff` |
| `PipelineOutput` — everything from one run | `iacsim/core/pipeline.py:35` | `iacsim/core/pipeline.py:140` | CLI, viewer, differ |

---

## 2. What happens when you type …

Every command starts the same way:

1. **`_bootstrap(target)`** — `iacsim/cli.py:46`. Calls `load_builtin_plugins()`
   (`iacsim/core/registry.py:68`: imports every `iacsim.*` module so each `@REGISTRY.register`
   decorator runs) and `load_external_plugins()` twice (`iacsim/core/registry.py:75`: the
   `iacsim.plugins` entry-point group, then `<target>/plugins` and `<cwd>/plugins`).
2. **`_base_dir(target)`** — `iacsim/cli.py:41`. The target directory, or the parent when the
   target is a template *file*. Config, scenarios and outputs are relative to this.
3. **`load_config(base_dir, overrides)`** — `iacsim/core/config.py:95`. Precedence: built-in
   `DEFAULTS` (`iacsim/core/config.py:16-68`) ← `iacsim.yaml` in the base dir ← CLI flags
   (overrides whose value is `None` are skipped, so a flag you didn't pass changes nothing).

### 2.1 `iacsim run <target>` — `iacsim/cli.py:74`

| # | Call | Where | Decides / condition |
|---|---|---|---|
| 1 | `_bootstrap`, `load_config` | above | overrides: `latency.profiles` (`"defaults"` is always first, then each `-p`), `simulation.walker/samples/seed/load`, `format`, `parsers.cloudformation.region`, `report.outputs` |
| 2 | **branch:** `simulation.walker == "load"` | `iacsim/cli.py:~97` | resolves `load.yaml` relative to the base dir; missing → `BadParameter` (exit 2). Other walkers skip this. |
| 3 | `pipeline.run(target, cfg)` | `iacsim/core/pipeline.py:140` | the eight stages below |
| 4 | `_write_outputs` | `iacsim/cli.py:55` | for each name in `report.outputs`: `text` → stdout (colour only on a TTY); anything else → `<base>/.iacsim/report.<ext>` |

**`pipeline.run` in order** (`iacsim/core/pipeline.py:140-149`):

| stage | function | what it does | conditions |
|---|---|---|---|
| 1–3 | `build_graph` `iacsim/core/pipeline.py:43` | parse → normalise → infer edges | see 2.8 |
| 4a | `load_profile` `iacsim/core/pipeline.py:62` | merge profile layers | a spec ending `.yaml`/`.yml` → `yaml_file` source; `"defaults"` → built-in |
| 4b | `cost_graph` `iacsim/core/pipeline.py:87` | price every edge | runs every rule in `latency.rules` (default `distance, processing, cold_start`) |
| 5 | `load_scenarios` `iacsim/core/pipeline.py:94` | collect request paths | sources in `scenarios.sources` order (default `yaml_file`, then `inferred_from_entrypoints`); **first name wins** — a declared scenario beats an inferred one |
| 6 | `simulate` `iacsim/core/pipeline.py:107` | walk each scenario | one walker instance for all scenarios (`WALKERS.get(cfg["simulation.walker"])`) |
| 7 | `analyse` `iacsim/core/pipeline.py:121` | rank hops, nodes, categories | every analyzer in `analysis.analyzers`; several return nothing when not applicable (2.9) |
| 8 | return `PipelineOutput` | `iacsim/core/pipeline.py:149` | graph, scenarios, results, findings, profile |

### 2.2 `iacsim graph <target>` — `iacsim/cli.py:113`
`_bootstrap` → `load_config({format, region})` → **only** `pipeline.build_graph` → writes
`<base>/.iacsim/graph.json` (`InfraGraph.to_dict`) → prints node/edge counts → warnings to
stderr. No profile, no pricing, no scenarios.

### 2.3 `iacsim validate <target>` — `iacsim/cli.py:178`
`build_graph` → `load_scenarios` → `load_profile` (no pricing, no simulation) → problems =
graph warnings + steps that name a node no edge touches (`_unwired_steps`) → prints counts and
the profile rung → exit 1 if any problem, else 0. (Current pass: warnings alone will stop
failing unless `--strict`.)

### 2.4 `iacsim diff <before> <after>` — `iacsim/cli.py:134`
1. `_bootstrap(before)`; parse `--fail-on-regression` (`iacsim/differ.py:221`).
2. Two `load_config` calls (one per side) with the same overrides.
3. `run_diff` (`iacsim/differ.py:48`): forces the *after* side to use the *before* side's
   profiles so numbers differ only because the infra does → `pipeline.run` twice → optional
   `--scenario` filter → `diff_reports` (`iacsim/differ.py:69`) → `diff_graphs`
   (`iacsim/differ.py:84`: nodes by id, or by label with `--align-by label`; a "move" is a
   change of region/az/vpc) + per-scenario `diff_scenario` (`iacsim/differ.py:132`) → values,
   hops by label + occurrence (`iacsim/differ.py:174`), recommendations (`iacsim/differ.py:204`).
4. Render through each reporter's `render_diff`; text → stdout, others → `<after>/.iacsim/diff.<ext>`.
5. `summarise` (`iacsim/differ.py:230`) → threshold check → exit 2 on a regression.

### 2.5 `iacsim view <target>` — `iacsim/cli.py:216`
`load_config` with `report.outputs = ["json"]` → `viewer.prepare` (`iacsim/viewer/__init__.py:32`:
runs the pipeline **only if** `.iacsim/report.json` is missing, writes `report.json` +
`graph.json`, copies `index.html`) → `viewer.serve` (`iacsim/viewer/__init__.py:51`: local
HTTP server on `127.0.0.1`, `--port 0` picks a free port, `--duration` for tests). The HTML
reads only the two JSON files — it never imports Python.

### 2.6 `iacsim calibrate <target>` — `iacsim/cli.py:234`
`load_config({calibrate.source, calibrate.window, format, region})` → `make_metric_source`
(`iacsim/latency/calibrate/__init__.py:25`: registry lookup by `calibrate.source`, constructor
kwargs from `calibrate.sources.<name>`, then `prepare(root)`) → `pipeline.build_graph` (no
pricing) → `calibrate(graph, source, window)` (`iacsim/latency/calibrate/calibrator.py:46`:
for every Lambda/table/LB/API node with a physical name, ask the source; complete blocks are
written under `by_label` when the name is unique, else `per_resource`; skipped nodes get a
reason) → coverage table → exits: unknown source → 2; source not usable → 3; nothing covered
→ 1; `--dry-run` → stop; else `write_profile` (`iacsim/latency/calibrate/writer.py:19`).

### 2.7 `iacsim plugins` — `iacsim/cli.py:319`
`_bootstrap(cwd)` then prints `names()` of all ten registries (`iacsim/core/interfaces.py:151-160`).

### 2.8 Stages 1–3 in detail: `build_graph` — `iacsim/core/pipeline.py:43-59`

```
parser_cls = detect_parser(target, forced=cfg["format"])       iacsim/parsers/detect.py:13
    forced          → PARSERS.get(forced)
    else            → DETECTION_ORDER = ["terraform", "cloudformation"]   (detect.py:10)
        terraform      : path is a dir containing *.tf                    (parsers/terraform/parser.py:35)
        cloudformation : a *.template.json / template.yaml with Resources  (parsers/cloudformation/parser.py:56)
    neither         → ValueError naming --format
options = cfg["parsers.<registry_name>"]  (None values dropped)   pipeline.py:46-47
raw     = parser_cls(**options).parse(target)                     → RawResources
graph   = NORMALISERS.get(cfg["provider"])().normalise(raw)       aws.py:112
graph.source_format = raw.format; graph.warnings += raw.warnings
for rule_name in cfg["inference.rules"]:                          pipeline.py:53   ← ORDER FROM CONFIG
    for edge in INFERENCE_RULES.get(rule_name)().apply(graph, raw):
        edge.rule = rule_name
        if graph.find_edge(edge.src, edge.dst) is None:           pipeline.py:57   ← today: first rule wins
            graph.add_edge(edge)                                  (current pass: merge, kinds kept in Edge.ops)
```

Default rule order (`iacsim/core/config.py:22-31`) is a confidence order:
`step_functions` → `event_source_mapping` → `lambda_permission` → `api_gateway_integration` →
`target_group` → `env_var` → `iam_policy` → `vpc_peering`.

Inside the normaliser (`iacsim/graph/normalisers/aws.py:112`): look the type up in `TYPE_MAP`
(`:39`); if absent, skip glue prefixes (`:85`) silently, else keep as a `network` node with a
warning; copy only latency-relevant attrs; derive capacity attrs (`:148`); derive placement
(`:167`: region from the parser, AZ from `availability_zone` or the subnet, VPC from `vpc_id`);
pick a label = the physical name (`:198`); finally add the `internet` node and an edge into
every gateway / load balancer / CDN (`:137`).

### 2.9 Which analyzers speak, and when

| analyzer | where | speaks when |
|---|---|---|
| `per_hop` | `iacsim/analyzer/per_hop.py:12` | always |
| `per_node` | `iacsim/analyzer/per_node.py:14` | always (merges repeat calls) |
| `per_category` | `iacsim/analyzer/per_category.py:30` | always — A1 distance / A2 service / A3 shape lines |
| `critical_path` | `iacsim/analyzer/critical_path.py:19` | only if the scenario has a parallel group |
| `recommendations` | `iacsim/analyzer/recommendations.py:38` | always, at most 5 lines |
| `tail_risk` | `iacsim/analyzer/tail_risk.py:25` | only with Monte-Carlo samples |
| `saturation` | `iacsim/analyzer/saturation.py:32` | only with the load walker's sweep |

Reporters: `text` (`iacsim/reporter/text.py:25`, rich tables), `markdown`
(`iacsim/reporter/markdown.py:13`), `json` (`iacsim/reporter/json_.py:70`). Text and markdown
both render the same `Brief` built in `iacsim/reporter/_brief.py:62`, so terminal and PR
comment never disagree.

### 2.10 Worked example — `iacsim run examples/classic-web-bad` → `356.0 ms`

1. `detect_parser` finds `main.tf` → Terraform parser. It expands `module "network"` (twice:
   once in `us-east-1`, once as `db_network` in `eu-west-1` through the `aws.db` provider
   alias), `module "compute"` with `for_each` over two AZs, and `module "database"`.
2. Normaliser: 16 nodes — `internet`, the ALB, two EC2 (`us-east-1a`, `us-east-1b`), RDS placed
   in `eu-west-1a` (via `providers = { aws = aws.db }`), VPCs and subnets.
3. Inference: `target_group` draws ALB → each EC2 (evidence: the listener forwards to the
   target group, the attachment registers the instance); `env_var` draws EC2 → RDS (evidence:
   `user_data` `db_host` references `module.database.address`); `vpc_peering` draws VPC ↔ VPC.
4. Pricing (`defaults.yaml`): `internet_to_edge` 20, `same_region_unknown_az` 0.5 (the ALB
   spans AZs), `cross_region eu-west-1/us-east-1` 75, alb `route` 2, rds `read` 5, ec2 `handle` 3.
5. `scenarios.yaml` declares `page_load`: ALB → EC2-a → RDS → RDS → EC2-a.
6. The walker (`Planner.plan`) charges, with distance doubled on synchronous hops:

| # | hop | mode | ms | breakdown |
|---|---|---|---|---|
| 1 | internet → ALB | direct (entry) | 42.0 | distance 20×2, processing 2 |
| 2 | ALB → EC2-a | direct | 1.0 | distance 0.5×2 |
| 3 | EC2-a → RDS | direct | 155.0 | distance 75×2, processing 5 |
| 4 | EC2-a → RDS | repeat call | 155.0 | same |
| 5 | RDS → EC2-a | response leg | 3.0 | processing 3 (distance already paid) |

Total **356.0 ms**. The good twin (`classic-web`) differs only in hops 3–4: `0.3×2 + 5 = 5.6`
each, so the delta is `2 × 2 × (75 − 0.3) = 298.8 ms` — the number `iacsim diff` prints.
(Current pass: hop 5 becomes 0 and the `handle` 3 moves to hop 2; the total stays 356.0.)

---

## 3. Two deep traces

### 3.1 The Terraform parser — files → `RawResources`

```
TerraformParser.parse                       iacsim/parsers/terraform/parser.py:38
  warnings = Warnings()                     loader.py:51   (deduplicates by message)
  root = ModuleInstance(root_dir, ...)      loader.py:64
      _load_files()                         loader.py:104  hcl2.load every *.tf; collects variables,
                                                            locals, outputs, module blocks, providers,
                                                            resources; a bad file → warning, continue
      _resolve_regions(...)                 loader.py:131  provider "aws"[.alias] → region; child modules
                                                            inherit or map aliases via `providers = {...}`
  RawResources(root.all_resources(), format="terraform", warnings)
```

**`all_resources()`** (`iacsim/parsers/terraform/loader.py:278`) walks every resource block,
expands it into instances (`resource()` `:188` → `_expand` `:240`), binds `each.key`/`each.value`
or `count.index` (`_bindings_for` `:268`), and evaluates each instance into a `RawResource`
(`_raw_resource` `:291`). Then it recurses into every child module (`child()` `:181` →
`_build_child` `:210`).

**Address formation** (Terraform's own grammar):

| construct | address | where |
|---|---|---|
| root resource | `aws_lb.web` | `loader.py:240` |
| `module "x"` | `module.x.aws_lb.this` | `loader.py:210` |
| `module "x" { for_each }` | `module.x["key"].…` | `loader.py:210` |
| `for_each` resource | `module.x.aws_instance.this["us-east-1a"]` | `loader.py:240` |
| `count` resource | `aws_instance.web[0]` | `loader.py:240` |

`for_each` keys (`_for_each_keys` `:250`): evaluate the expression and iterate it
(`evaluator.py:192`); for a `toset(list)` the *value* becomes the key; if it cannot be
resolved → one instance keyed `"*"` plus a warning.

**Per attribute** (`_raw_resource` `:291`): `evaluate_raw` (`iacsim/parsers/terraform/evaluator.py:118`)
recurses dicts/lists, then `parse_attribute` (`iacsim/parsers/terraform/hcl_expr.py:342`) turns
`"literal"` or `${…}` into a small AST (tokenizer `:52`, parser `:334`), and `evaluate`
(`evaluator.py:135`) resolves identifiers through `Scope` (`evaluator.py:96`) — variables,
locals, module outputs, other resources — lazily and memoised, so cross-module references
resolve regardless of file order (a genuine cycle → `EvalError` → warning). `to_plain`
(`evaluator.py:325`) turns a `Ref` into the placeholder string `"${address.attr}"`
(`iacsim/core/refs.py:32`); anything that fails keeps its raw text and adds a warning.

**References**: `references_in` / `addresses_in` (`iacsim/core/refs.py:52`) scan attribute
values for `${…}` (`refs.py:23`), keep the ones that look like addresses (`refs.py:81`) and
split them into module path / type / name / attribute (`refs.py:36`). Inference rules use these.

**Warn-never-crash sites**: unparseable file `loader.py:104`; remote or missing module source
`loader.py:210`; unresolved `for_each` / `count` `loader.py:250`; unresolved provider region
`loader.py:131`; any attribute evaluation error `loader.py:291`. Known silent case being fixed:
`data.*` references (`evaluator.py:108`).

The CloudFormation path lands in the same place: `iacsim/parsers/cloudformation/parser.py:59`
→ `template.load_template` (`iacsim/parsers/cloudformation/template.py:44`) → `intrinsics.resolve`
(`iacsim/parsers/cloudformation/intrinsics.py:62`, emitting the *same* `${type.LogicalId.attr}`
placeholders) → `canonical_type` / `canonical_attrs` / `synthetic_resources`
(`iacsim/parsers/cloudformation/canonical.py:127`, `:138`, `:154`) → `resolve_physical_names`
(`canonical.py:190`).

### 3.2 The traversal — `Scenario` → `Result`

All three walkers share one traversal (`iacsim/simulator/traversal.py`): a `Planner` decides
the hop *structure* once; a `Backend` decides what one hop *costs*.

**`Planner.plan`** (`traversal.py:~115`): start at `scenario.entry`; if `internet → entry`
exists, charge that leg first; then `_walk_steps` (`:~130`), which has four branches per `Step`:

| step | action |
|---|---|
| `node` | `_hop` (below) |
| `fanout {node, count}` | look up the enclosing Map's concurrency (`_map_concurrency` `:~165`; today: first top-level Map / after WP3: innermost Map enclosing the target); waves = `ceil(count / concurrency)`; one hop stamped with `copies`/`waves` |
| `parallel [[…],[…]]` | `_parallel` (`:~152`): each branch walked from the same current node in a copy of the state; group = max of branches (after WP3 branch visits merge back) |
| `wait_ms` | a hop with `{"wait": ms}` |

**`_hop`** (`:~186`): if the destination equals the current node, it is a "repeat call" from
the same caller. Then **`_edge_for`** (`:~210`) — the four-way ladder, in this exact order:

| # | mode | condition | what is used |
|---|---|---|---|
| a | `direct` | an inferred edge `current → dst` exists | that edge |
| b | `response` | `dst` was already visited | a synthetic edge; evidence "response leg" |
| c | `via caller` | some earlier node in `visited` has an edge to `dst` | that edge, and the hop is charged **from that caller** |
| d | `estimated` | none of the above | a synthetic edge priced by the same rules + a warning in the report |

`op:` on the step re-prices the edge as that kind (`_reprice` `:~227`).

**`_charge`** (`:~236`) — the ×2 rule. Today: response legs drop `distance` only; every
other hop with a synchronous kind (invoke/read/write/route, `SYNCHRONOUS` `:~63`) doubles
`distance`; processing and cold start once. **After WP1:** a response leg charges only the
destination's `respond` key (default 0) — no distance, no cold start — and every hop *out of*
an orchestrator additionally charges `transition` (new cost rule).

**`evaluate(plan, backend)`** (`:~324`): hops → `backend.cost(hop)` scaled by `waves`
(`_scaled` `:~363`); groups → evaluate every branch, take `backend.maximum`, mark only the
slowest branch on the critical path, record the saving (`_group` `:~346`). **`build_result`**
(`:~372`) packs `HopResult`s, `shape` (hop count, parallel savings, fan-out copies/waves,
waits) and the total into a `Result`.

| backend | `cost(hop)` | `maximum` |
|---|---|---|
| `ExpectedBackend` `traversal.py:~294` | sum of the breakdown — a float | `max()` |
| `MonteCarloBackend` `iacsim/simulator/walkers/monte_carlo.py:57` | per key: distance/processing → lognormal with the expected value as mean; cold start → Bernoulli(`cold_prob`) × `cold`; waits exact — a vector of `samples` values | element-wise max |
| `_ContendedBackend` `iacsim/simulator/walkers/load.py:232` | expected cost + the destination resource's mean queue wait (Erlang-C, `iacsim/simulator/capacity.py:182`) | `max()` |

The load walker (`iacsim/simulator/walkers/load.py:101`) plans each scenario once, turns
`load.yaml` into arrivals per user (`_rates_per_user` `:195`), builds capacity resources
(`iacsim/simulator/capacity.py:75`), then for each user count offers load to every resource
(`_offer` `:210`), computes utilisation and queue waits, and re-evaluates the plan with the
contended backend to get latency and p99 at that user count.

---

## 4. Glossary

| term | one line | where |
|---|---|---|
| IR | the dataclasses every stage passes to the next | `iacsim/core/models.py:1-15` |
| RawResource / RawResources | a resource as the IaC declared it (provider type, attrs with placeholders, references); the batch with `format` and warnings | `iacsim/core/models.py:21`, `:41` |
| InfraGraph | nodes by id + edges + warnings + `schema_version` | `iacsim/core/models.py:122` |
| Node / kind / subtype | one component; `kind` is the neutral role (compute, datastore, lb, gateway, queue, orchestrator, cdn, network, external), `subtype` the service (lambda, dynamodb, alb…) | `iacsim/core/models.py:101`, `:50` |
| Placement | `{region, az, vpc, subnet}` — input to the distance rule | `iacsim/core/models.py:81` |
| Edge / EdgeKind | a believed call: invoke / read / write / route / publish / consume / peer, with confidence, evidence, rule, latency | `iacsim/core/models.py:111`, `:63` |
| evidence | the sentence on every edge and hop saying why we believe it | `iacsim/core/models.py:111` |
| confidence | declared > high > medium > low | `iacsim/core/models.py:73` |
| canonical type | the Terraform-shaped type every CloudFormation resource is rewritten to | `iacsim/parsers/cloudformation/canonical.py:127` |
| placeholder | `"${address.attr}"` — how a parser records "this mentions that" | `iacsim/core/refs.py:32` |
| Scenario | a named request path: entry + steps + source (declared / inferred) | `iacsim/core/models.py:177` |
| Step / op | one path element: `node`, `parallel`, `fanout`, or `wait_ms`; `op` re-prices as read/write/… | `iacsim/core/models.py:157` |
| parallel | branches run concurrently; cost = max | `iacsim/simulator/traversal.py:~152 _parallel` |
| fanout / wave | one node invoked `count` times; costed once per wave = `ceil(count / concurrency)` | `iacsim/simulator/traversal.py:~135 _walk_steps` |
| Profile | merged numbers (`meta`, `distance`, `processing`, `variance`, `capacity`) + the layers merged | `iacsim/core/models.py:188` |
| rung | which tier the numbers come from: 0 defaults, 1 team profile, 2 measured, 3 traces | `iacsim/latency/profile.py:49` |
| by_label / per_resource | profile blocks keyed by physical name / by node id | `iacsim/core/models.py:203` |
| cost rule | a pluggable component of an edge's latency (`{name: ms}`) | `iacsim/core/interfaces.py:73` |
| pricer | the one closure that prices any edge with the enabled rules | `iacsim/core/pipeline.py:72` |
| walker | runs one scenario across the priced graph | `iacsim/core/interfaces.py:81` |
| Planner / Plan / Backend | shared traversal: structure once, cost per backend | `iacsim/simulator/traversal.py:~111`, `:~96`, `:~287` |
| hop / HopResult | one charged step with breakdown, evidence, critical-path flag | `iacsim/core/models.py:221` |
| shape (A3) | latency facts needing no numbers: hop count, parallel savings, fan-out, waits | `iacsim/core/models.py:237` |
| Result | one walker run of one scenario | `iacsim/core/models.py:237` |
| analyzer / Finding / Findings | ranked report lines from a Result | `iacsim/core/interfaces.py:88`, `iacsim/core/models.py:255`, `:277` |
| additive | false for lines that must not be summed toward the total | `iacsim/core/models.py:255` |
| Layer A1 / A2 / A3 / B | distance / service cost / shape; B = application code (absorbed by calibration) | `iacsim/analyzer/_common.py:9-15` |
| Brief | the ordered section list text and markdown both render | `iacsim/reporter/_brief.py:51` |
| reporter | renders Findings or a DiffReport | `iacsim/core/interfaces.py:95` |
| extension point | an ABC + a registry + an `iacsim.yaml` key | `iacsim/core/interfaces.py:151-160` |
| registry | name → class; `@REG.register("x")`, `REG.get("x")` | `iacsim/core/registry.py:35` |
| plugin | a `.py` in `./plugins` (or an entry point) that registers an implementation | `plugins/README.md` |
| resource / slots / erlangs / ρ | under load: what a request occupies, how many concurrently, offered load, utilisation | `iacsim/simulator/capacity.py:41` |
| saturation / first to break | ρ ≥ 1; the resource with the lowest break-point users | `iacsim/analyzer/saturation.py:63` |
| metric source | where measured numbers come from for `calibrate` | `iacsim/core/interfaces.py:107` |

---

## 5. How to add …

Every row follows the same three moves: implement the ABC, register a name, refer to it in
config. Copy the named file and the named test.

| you want to add | ABC / where | registry | `iacsim.yaml` key | copy this | copy this test |
|---|---|---|---|---|---|
| a **resource type** (AWS) | one line in `TYPE_MAP` `iacsim/graph/normalisers/aws.py:39` (+ placement/capacity attrs at `:148`, `:167` if needed) | — | — | an existing row | `tests/test_example_classic_web.py` node assertions |
| an **inference rule** | `InferenceRule.apply(graph, raw) -> [Edge]` `iacsim/core/interfaces.py:54` | `INFERENCE_RULES` | `inference.rules: [...]` (order = priority) | `iacsim/graph/inference/vpc_peering.py` (27 lines) | `tests/test_example_classic_web_bad.py` edge assertions |
| a **cost rule** | `CostRule.cost(edge, graph, profile) -> {name: ms}` `iacsim/core/interfaces.py:73` | `COST_RULES` | `latency.rules: [...]` + a block in `defaults.yaml` | `iacsim/latency/rules/cold_start.py` | `tests/test_latency_rules.py` |
| a **walker** | `Walker.run(graph, scenario, **options) -> Result` `iacsim/core/interfaces.py:81` — supply a `Backend`, reuse the `Planner` | `WALKERS` | `simulation.walker` | `iacsim/simulator/walkers/expected_value.py` (25 lines) | `tests/test_walker.py` |
| an **analyzer** | `Analyzer.analyse(result, graph) -> [Finding]` `iacsim/core/interfaces.py:88`; return `[]` when not applicable | `ANALYZERS` | `analysis.analyzers: [...]` | `iacsim/analyzer/per_hop.py` | `tests/test_analyzers.py` |
| a **reporter** | `Reporter.render(findings, graph) -> str` (+ `render_diff`) `iacsim/core/interfaces.py:95`; build from the `Brief` | `REPORTERS` | `report.outputs: [...]` | `iacsim/reporter/markdown.py` | `tests/test_reporters.py` |
| a **metric source** (Datadog, Prometheus…) | `MetricSource.supports(kind)` / `.measure(kind, name, window, region)` `iacsim/core/interfaces.py:107` | `METRIC_SOURCES` | `calibrate.source` + `calibrate.sources.<name>: {…}` | `iacsim/latency/calibrate/fake.py` | `tests/test_calibrate.py` |
| a **parser** (Pulumi, ARM, Kubernetes…) | `Parser.detect(path)` / `.parse(path) -> RawResources` `iacsim/core/interfaces.py:26`; emit canonical Terraform-shaped types and `${addr.attr}` placeholders | `PARSERS` (+ `DETECTION_ORDER` `iacsim/parsers/detect.py:10`) | `format:` / `parsers.<name>: {…}` | `iacsim/parsers/cloudformation/` (parser + canonical + intrinsics) | `tests/test_cloudformation_parser.py`, `tests/test_example_foosh_cfn.py` |
| a **cloud provider** (Azure, GCP) | a normaliser `Normaliser.normalise(raw) -> InfraGraph` `iacsim/core/interfaces.py:44` with its own `TYPE_MAP`; rules for that provider's evidence; a `distance.<cloud>` block in `defaults.yaml` | `NORMALISERS` + `INFERENCE_RULES` | `provider:` | `iacsim/graph/normalisers/aws.py` + the 8 rules | one example stack + its `tests/test_example_*.py` |
| a **plugin** without forking | any of the above, in one `.py` under `./plugins` | auto-imported at startup (`iacsim/core/registry.py:75`) | name it in `iacsim.yaml` | `plugins/README.md` | run `iacsim plugins` |

Rule of thumb for any addition: if `git diff --stat` touches `iacsim/core/`,
`iacsim/simulator/`, `iacsim/analyzer/` or `iacsim/reporter/` for anything other than a
registration import, it is an engine change — open a row in `DECISIONS.md` first.
