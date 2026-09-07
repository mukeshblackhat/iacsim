# GCP support — overview

The index for milestone **M11**. Start here, then read the files below in
the order given.

Nothing under `iacsim/` has been changed yet. This round produced documents only,
so that the shape of the work can be reviewed before any code moves.

---

## The problem, in one paragraph

iacsim reads two file formats but understands **only AWS**. Point it at a GCP
Terraform directory today and the HCL reader parses it correctly — modules,
`for_each`, locals, references, all of it — and then the AWS normaliser throws
the result away, because `TYPE_MAP` (`iacsim/graph/normalisers/aws.py:41`) has no
`google_*` keys. Every resource becomes *"unknown type … kept as a network node"*
and you get an empty picture full of warnings.

**The gap was never in the parsers.** `iacsim/parsers/terraform/` is 1,426 lines
of HCL evaluation that already works on `google_*` files. The work is in the
stages after parsing: the normaliser, the inference rules, the latency numbers —
plus one real bug in the loader (below).

**Outcome when M11 lands:** `iacsim run ./my-gcp-terraform` produces a real
graph, real timings and a real report with **no config file** — the same
zero-config experience AWS users get today.

---

## Reading order

| # | file | what it answers | size |
|---|---|---|---|
| 1 | **`01-DECISIONS.md`** | Every choice and why, in `DECISIONS.md`'s shape. G1–G6 settled with the user; G7–G21 forced by the code. Read this first — the other three are consequences of it. | 21 decisions |
| 2 | **`02-TYPE-MAP.md`** | The `google_*` → `(NodeKind, subtype)` table that becomes `normalisers/gcp.py`. Every row cites the public repo it was seen in, or is marked **unverified**. | 97 types |
| 3 | **`03-TESTING.md`** | What we test, at which layer, with which open-source project. A todo checklist you can watch go green. | 101 checkboxes |
| 4 | **`05-EXAMPLES.md`** | The wider third-party GCP corpus — messy real-world repos that stress the parser differently from Google's clean samples. | 14 repos, 22 rejections |
| — | **`04-CHANGES.md`** | The execution record. Append-only, one entry per work package. | — |

---

## The three things that would have gone wrong quietly

These matter more than any missing feature, because each produces a
plausible-looking number rather than an error. They are the reason this round
happened before any code was written.

**1. Regions vanish.** `iacsim/parsers/terraform/loader.py:167` reads
`if block["name"] != "aws": continue` — every non-`aws` provider block is skipped
outright. A `provider "google" { region = "us-central1" }` is ignored, so every
GCP node gets `region=None`, and `DistanceRule` then prices every hop as
`same_region_unknown_az` — **0.5 ms**. A cross-continent call reads as nearly
free. (`01-DECISIONS.md` G7.)

**2. A hop with no profile entry is charged nothing.** `INVOKE_KEY_FOR_SUBTYPE`
(`iacsim/latency/rules/processing.py:37-43`) maps a subtype to the profile key
charged on a hop. A subtype missing from that table — or present but with no
`processing.<subtype>` block — is charged **zero**, and the code returns `{}`
rather than raising.

This is precedent, not theory: `aws_kinesis_stream` maps to subtype `kinesis`,
`INVOKE_KEY_FOR_SUBTYPE["kinesis"] == "publish"`, and
`iacsim/latency/defaults.yaml` has **no `processing.kinesis` block at all**.
Every hop into a Kinesis stream is priced at zero **today, on the AWS side**. GCP
adds a dozen subtypes at once. (G8; the guard-rail test in `03-TESTING.md` §3.0
is written to fail on `kinesis` too, rather than exempt the bug it was modelled
on.)

**3. Cold starts never fire.** `iacsim/latency/rules/cold_start.py:21` is
`if dst.subtype != "lambda": return {}`. Cloud Run and Cloud Functions cold-start
is arguably GCP's single most important latency effect, and would be silently
unpriced. (G20.)

---

## Decisions taken with the user

| # | Choice |
|---|---|
| **G1** | **Auto-detect the provider** from resource-type prefixes. Explicit `provider:` still wins. No config file needed. |
| **G2** | **Broad first slice** — 97 types, including GKE, BigQuery, Composer, Eventarc. |
| **G3** | GCP's HTTP load-balancer chain stays as **4–5 separate nodes**, not collapsed into one. Internal chain hops priced at 0 ms. |
| **G4** | The engine's AWS-subtype tables become **provider-owned** — each normaliser declares its own behaviour tables. AWS tables move out unchanged, so AWS behaviour is byte-identical. |
| **G5** | Docs live in `docs/gcp/`, so `docs/azure/` and `docs/k8s/` have a pattern to copy. |
| **G6** | GCP **capacity modelling deferred** to a later work package. Round 1 is graph + timings + diff. |

The known cost of G3, recorded rather than hidden: a GCP request shows 5 hops
where the AWS equivalent shows 1, so `iacsim diff` across the two clouds is no
longer hop-comparable. Pricing the internal hops at 0 ms keeps the *totals*
honest. And per G9, that needs **distance suppressed as well as `route: 0`** —
`ROUTE` is in `SYNCHRONOUS` (`iacsim/simulator/traversal.py:75`) so distance
doubles at `traversal.py:283`, which with `az=None` on every chain node is ~4 ms
of phantom network per request.

---

## What changes, and where

### New files

| file | why |
|---|---|
| `iacsim/graph/normalisers/gcp.py` | the type map, placement, capacity attrs, and the `internet` entry edges |
| `iacsim/graph/inference/gcp_lb_chain.py` | forwarding rule → proxy → URL map → backend service → NEG → service |
| `iacsim/graph/inference/gcp_iam_binding.py` | GCP IAM is *bindings*, not resource-scoped action lists — a rewrite, not a table swap |
| `iacsim/graph/inference/gcp_eventarc.py` | trigger transport → destination |
| `iacsim/graph/inference/gcp_pubsub_push.py` | `push_config.push_endpoint` → the service that URL belongs to |
| `iacsim/graph/inference/gcp_workflows.py` | parse `source_contents` YAML for call steps |
| `examples/gcp-web/` + `examples/gcp-web-bad/` | the controlled diff pair, mirroring `classic-web` |
| `examples/real-world/gcp-*/` | seven vendored public fixtures |

### Modified

| file | why | trips the Phase 3 rule? |
|---|---|---|
| `iacsim/parsers/terraform/loader.py` | the provider-block filter, and `_PROVIDER_REF` (line 62) uses `\w+`, which cannot match the hyphen in `google-beta` | no |
| `iacsim/latency/rules/processing.py`, `cold_start.py` | tables move out to the normaliser (G4) | no |
| `iacsim/latency/defaults.yaml` | GCP `processing` blocks and `cross_region` pairs | no |
| `iacsim/core/pipeline.py` | provider auto-detection (G1) | **yes** |
| `iacsim/core/models.py:252` | `display_name` strips only `.aws_`, so GCP ids render unshortened | **yes** |
| `iacsim/core/interfaces.py:156` | `MetricSource.KINDS` enumerates AWS subtypes inside the ABC | **yes** |

`TIMELINE.md` promises Phase 3 milestones are "only new registrations", and
`CODE_FLOW.md` turns that into a procedure: touching `core/` for anything but a
registration import means *open a row in `DECISIONS.md` first*. GCP breaks it in
three places. **That row now exists** — `DECISIONS.md` §10, and G21 here. The
promise is amended rather than quietly falsified.

`iacsim/simulator/capacity.py` and `iacsim/analyzer/saturation.py` also hardcode
AWS subtypes, but are **out of scope this round** per G6.

---

## Reused, not rewritten

Worth knowing before anyone forks a file that did not need forking:

- **`env_var`** is genuinely provider-neutral in its executable code — its only
  `aws_` string is a docstring example. It couples to GCP through one constant,
  `CONFIG_ATTRS` (`iacsim/graph/inference/env_var.py:33`). (G14.)
- **`transition`** is gated on `NodeKind.ORCHESTRATOR`, not subtype, so a GCP
  `workflows` subtype works with **no code change**. The one already-neutral cost
  rule.
- **`refs.py`** needs nothing. Its one heuristic — a type name contains `_` —
  happens to be true of every `google_*` type.
- **`inferred.py`** works unchanged **if** the normaliser emits an EXTERNAL node
  with the literal id `"internet"` edged to every GATEWAY/LB/CDN node. That
  string is hardcoded in `iacsim/scenarios/inferred.py:69` and
  `iacsim/simulator/traversal.py:134-135`. (G13.)

---

## Testing

Seven vendored fixtures, all Apache-2.0, licence verified via the GitHub API and
commits pinned. The decisive finding: **16 of 16 sample directories checked in
`terraform-google-modules/terraform-docs-samples` contain zero `module` blocks** —
self-contained files that graph fully with no `terraform init`. The official
`terraform-google-modules/*` module repos fail that test; their examples pull
registry modules, reproducing the dead-graph problem of the existing
`eks-cluster` fixture.

`03-TESTING.md` has the corpus, the sparse-checkout commands and the checklist.
`05-EXAMPLES.md` adds the wider third-party corpus.

---

## Sequence

One work package per commit, `make check` green at each step, per
`CONTRIBUTING.md`:

**WP1** loader provider fix (including `google-beta`) → **WP2** provider-owned
subtype tables, AWS behaviour byte-identical → **WP3** the `gcp` normaliser and
type map → **WP4** auto-detection → **WP5** inference rules → **WP6** latency
numbers → **WP7** examples and vendored fixtures.

WP1 and WP2 come first deliberately: they are the two silent-wrong-answer traps,
and every later work package would otherwise be measured against wrong numbers.
