# Prompt: Close the vLLM metric-surface gap, and keep it closed

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.3.0 and 0.4.0**: the V1 metric-surface resync and the drift check over the metric SET. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Two
pieces of work:

1. **W1** — emit `prefix_cache_*` and `request_queue_time_seconds`, the two metric families
   whose absence most limits this repo's central claim.
2. **W2** — extend the weekly upstream drift check from bucket boundaries to the metric
   **set**, so the gap W1 closes cannot silently reopen.

**Why.** The repo's argument is "build the pipeline here, tune the numbers on real
hardware". A real vLLM operator's dashboard has a prefix-cache hit-rate panel, and there
is nothing here to build it against: the simulator emits 10 of upstream's ~37 V1 metrics.
W2 is the mechanism that makes the remaining gap visible instead of silent — the existing
checker catches bucket changes only, which is narrower than the risk that created it.

Both boards are **published** ([25618](https://grafana.com/grafana/dashboards/25618),
[25620](https://grafana.com/grafana/dashboards/25620)), so W1's dashboard change has an
audience. See W1.8.

**Run order across the three prompts:** `prompt-fidelity.md` → `prompt-packaging.md` →
`prompt-chart.md`. Each is independently runnable; the order matters only where a later one
packages or tests what an earlier one produces.

### Effort, and where to stop

Estimates, derived from reading the code rather than from doing the work. Treat the
ordering as firmer than the numbers.

| | Estimate | Standalone? |
|--|--|--|
| W1 metrics + selftest | ~1 day | yes |
| W1.7 rule, panel, promtool tests | ~half a day | needs W1 |
| W1.8 dashboard re-publish | ~1 hour | needs W1.7 |
| W1.9 `verify.sh` L6 check | ~1 hour | needs W1, and a cluster |
| W2 metric-set drift | ~2 hours | **yes — independent of W1** |

**If you can only do one thing, do W2.** It is two hours, it touches one file, and it is
what makes every future gap visible. W1 without W2 closes today's gap and lets tomorrow's
open silently. Stopping after W1 but before W1.7 is also coherent: the metrics exist and
are tested, nothing on the board changes, and nothing needs re-publishing.

## Background / Facts

Every fact below was read directly in the file cited, on 2026-07-31. None of it is inferred
from a comment. Where it turns out to be wrong anyway, correct it in your commit message.

### What the simulator emits today — VERIFIED (`scripts/llm-sim.py`)

```
vllm:num_requests_running          vllm:time_to_first_token_seconds
vllm:num_requests_waiting          vllm:inter_token_latency_seconds
vllm:kv_cache_usage_perc           vllm:e2e_request_latency_seconds
vllm:prompt_tokens_total           vllm:request_success_total
vllm:generation_tokens_total       vllm:num_preemptions_total
```

Plus two v0 aliases behind `--vllm-surface`, mapped in `METRIC_SURFACES` (`llm-sim.py:126-130`):
`vllm:gpu_cache_usage_perc` and `vllm:time_per_output_token_seconds`.

### What upstream declares — VERIFIED (fetched `vllm/v1/metrics/loggers.py`, `main`, 2026-07-31)

Relevant subset:

| Upstream name | Type | Here? |
|--|--|--|
| `vllm:prefix_cache_queries` | Counter | **no** |
| `vllm:prefix_cache_hits` | Counter | **no** |
| `vllm:request_queue_time_seconds` | Histogram | **no** |
| `vllm:iteration_tokens_total` | Histogram | no — note the suffix, it matters in W2.3 |

⚠️ **Upstream's naming is not uniform.** Counters are declared *without* `_total` and the
Prometheus client appends it at exposition time — which is why we emit
`vllm:prompt_tokens_total` against upstream's `vllm:prompt_tokens`, and both are correct.
But `vllm:iteration_tokens_total` is declared *with* the suffix, as a histogram. Any
name comparison must handle both, or it will report drift on a correct metric.

### Simulator internals — VERIFIED

- **`advance_to()` mutates, `render()` only reads** (docstring at `llm-sim.py:319`,
  `def render` at `:474`). New counters and histograms must be updated in the worker. A
  scrape must never move a number.
- **`req.arrived` exists**, used for e2e latency at `:465`. Queue time needs one more
  timestamp on the request, not a new clock.
- **⚠️ No `source` label on any `vllm:` series, ever.** Real vLLM emits none; adding one
  breaks exact-match joins and `group_left` against a real deployment. The selftest greps
  for it at `:747`.
- Bucket lists are transcribed verbatim from upstream (`TTFT_BUCKETS`, `TPOT_BUCKETS`,
  `E2E_BUCKETS`) and asserted still-present by `scripts/check-vllm-buckets.py`, whose
  `OURS` tuple names them.

### Two facts worth having up front

- The selftest does render twice and assert nothing moved: `before = sim.observations`,
  two `sim.render()` calls, then `check(sim.observations == before, ...)`. Extend that
  assertion rather than rewriting it.
- `verify.sh:286-288` is
  `count(llm:ttft:p95_5m < 120) == count(llmsim_profile_generation)`. The 120s bound is
  real — but **the headroom is not what the number suggests, and that is load-bearing for
  W1.4**. The comment above it explains why: the next bucket up is (80, 160], which
  interpolates to 152, so the check flips the moment true p95 crosses **80s**, not 120s.
  Saturated sits at ~58s. Read it as "the queue wait has not escaped the (40, 80] band".
  Anything nudging TTFT upward has ~22s of real headroom, not 62s — a second, independent
  reason W1.4 leaves latency alone.

### The profile arithmetic is load-bearing — VERIFIED (`manifests/llm/10-profiles.yaml:16-38`)

```
itl_full = 0.015 × 1.5 = 0.0225      capacity = 16 / (0.08 + 256 × 0.0225) = 2.74 rps
steady    1.8 rps = 0.66× capacity   → queue ~0,   p95 TTFT well under 1s
saturated 6.0 rps = 2.19× capacity   → queue 160,  TTFT ≈ 160/2.74 ≈ 58s
```

Relied on by the `LLMHighTTFT` 2s threshold, by `verify.sh`, and by the expected values in
`tests/rules/llm-rules_test.yaml`. A previous revision computed capacity from *uncongested*
latency and the two tenants became indistinguishable on the board.

---

## W1 — The two missing metric families

**W1.1 Emit them.** `vllm:prefix_cache_queries_total`, `vllm:prefix_cache_hits_total`
(counters) and `vllm:request_queue_time_seconds` (histogram), labelled `model_name` like
every other series, no `source` label.

**W1.2 ⚠️ Do not add a bucket constant — reuse `E2E_BUCKETS`.** VERIFIED against upstream
on 2026-07-31: `loggers.py` declares a single `request_latency_buckets` list and passes it
to **both** `vllm:e2e_request_latency_seconds` and `vllm:request_queue_time_seconds`.
Diffed against this repo, `E2E_BUCKETS` is identical to it — all 21 values.

The correct boundaries are therefore already in the file. Reuse the constant, comment that
upstream shares one list across the two histograms, and add **nothing** to `OURS` in
`check-vllm-buckets.py`: the existing entry already watches these boundaries on behalf of
both. Transcribing a second copy would duplicate a list the repo already holds, in the one
repo that refuses second copies.

**W1.3 Count prefix-cache in blocks, not requests.** Real vLLM counts KV **blocks**: a
query is a block looked up, a hit is a block found. A per-request counter would give a
ratio that does not respond to prompt length, so a panel built here would behave
differently against a real deployment — which defeats the purpose of adding it.

Add two profile fields: `prefix_cache_hit_rate` (0.0-1.0) and `kv_block_tokens` (default
16). Per admitted request:

```
blocks   = ceil(prompt_tokens / kv_block_tokens)
queries += blocks
hits    += floor(blocks × prefix_cache_hit_rate)
```

Deterministic rather than per-block Bernoulli, so the selftest is reproducible without
depending on the profile's `seed`. If you prefer stochastic, it must honour `seed` and the
selftest must still be deterministic.

**W1.4 ⚠️ Counters only. A cache hit must NOT change latency in this pass.** The obvious
next thought is that a hit skips recomputation and so shortens TTFT. It does on real
hardware. It cannot here, and the reason is worth writing down so nobody re-opens it:
prefill in this simulator is **flat**, not token-proportional —

```python
prefill = p["base_ttft_seconds"] * self._jitter()      # llm-sim.py:442
```

so there is no per-token work for a cached block to remove. Making prefill
token-proportional is a genuine modelling change that re-derives
`service = base_ttft + gen_mean × itl` (`:241`), and with it the capacity figure, both
profiles and the promtool expected values — exactly what the rest of this prompt forbids.

So: emit the counters, and record in the file header that the hit rate has **no latency
effect by construction**, naming what a future change would have to re-derive. An honest
zero beats a fabricated speedup, and a panel built against these counters still transfers —
the ratio is what a real deployment plots.

**Ship non-zero rates on the tenants — 0.35 steady, 0.15 saturated.** Zero everywhere would
put a panel on a published board pinned at exactly zero for every user of the rig, which is
a worse first impression than no panel. Non-zero is safe here precisely because the hit rate
is decoupled from latency: **no existing series changes**, only the two new counters take a
value, so the capacity arithmetic, the 2s threshold, L3b's real headroom and every promtool
expectation stay untouched.

⚠️ **Unlike `capacity_rps`, these two numbers are derived from nothing.** They are chosen so
the board draws two distinguishable lines, the lower one on the saturated tenant because a
server under eviction pressure reuses less. Say exactly that where you set them — an
invented number presented as a modelled one is the failure the profile comments in
`10-profiles.yaml` exist to prevent. Give the driven extra a value too, so
`drive-llm-load.sh` shows movement.

**W1.5 Queue time already exists in the code — extract it, do not re-derive it.**
`llm-sim.py:446` is:

```python
req.ttft = (self.now - req.arrived) + prefill
```

The queue wait is that first term, so `TTFT = queue_time + prefill` is an **identity by
construction**, not an approximation. Observe the first term into the new histogram at the
same point TTFT is observed (`:461`), and nowhere else. Acceptance criterion 3 asserts the
identity directly.

**W1.6 The v0 surface — resolved, and it is the sharpest upgrade-rehearsal case this repo
has.** VERIFIED against `vllm/engine/metrics.py` at tag `v0.6.6`: v0 exposed prefix caching
as **gauges of a ratio** — `vllm:gpu_prefix_cache_hit_rate` and
`vllm:cpu_prefix_cache_hit_rate`. V1 replaced them with **two counters**,
`vllm:prefix_cache_queries` and `vllm:prefix_cache_hits`.

That is not a rename, it is a change of metric *shape*. A panel bound to the v0 gauge
cannot be repaired by substituting a name: the replacement has to be
`rate(hits)/rate(queries)`. Neither of the two renames this repo already ships makes that
point, so `--vllm-surface both` should now demonstrate it.

⚠️ `METRIC_SURFACES` (`llm-sim.py:126-130`) is a 1:1 logical→(v0, v1) **name** map and
cannot express one-gauge→two-counters. Extend the mechanism rather than special-casing it
in the render path, which is where the no-observation-on-render rule is easiest to break.

Emit `vllm:gpu_prefix_cache_hit_rate` under `v0`, the two counters under `v1`, and all
three under `both`. Skip the `cpu_` variant: nothing here models CPU KV offload, and V1
dropped it entirely — which is precisely why `gpu_` stopped distinguishing anything, the
same reasoning already recorded for `gpu_cache_usage_perc` in
`manifests/alerts/llm-prometheusrule.yaml`.

Also VERIFIED at that tag: **`vllm:request_queue_time_seconds` exists in v0 under the same
name**, so queue time needs no surface entry at all.

**W1.7 Make them usable.** A recording rule `llm:prefix_cache:hit_ratio5m`
(`rate(hits)/rate(queries)`, zero denominator guarded with `clamp_min` as
`gpu-prometheusrule.yaml` does), a panel on the LLM board, and promtool tests covering both
sides of the ratio plus the no-traffic case. Recording rules carry `source: simulated`; the
raw `vllm:` series do not. With W1.4's shipped rates the panel has two distinct lines to
draw, which is what earns it a place on a published board.

**W1.8 ⚠️ The board is published — three consequences.**

- Re-run `task dashboards` and confirm `dist/` regenerates. `scripts/dashboard-publish.py`
  **fails and emits nothing** if a panel carries a hardcoded datasource uid, which is
  exactly what editing in the Grafana UI and pasting the JSON Model back produces. That is
  the likeliest way this panel gets added, so expect it.
- Update `manifests/dashboards/llm-sim-overview.grafana-com.md` — the panel table and the
  "what it needs" tiers both change.
- **Upload as a new revision of 25620, never as a new dashboard.** A second upload mints a
  second id and everyone who already imported the first stops receiving fixes. Say in the
  changelog entry that a re-submission is required.

**W1.9 Add a `verify.sh` check — house pattern, not an extra.** The LLM checks there are
numbered L1, L2, L3, L3b, L4, L4b, L5, and the prompt that produced them
(`prompt-llm-sim.md`) carries a section titled "Acceptance Criteria (add these to
`verify.sh`)". Every other criterion in this prompt is a selftest or promtool assertion, so
without this **nothing proves the new series survive a real scrape into a real
Prometheus**. Add an L6 in the style of L3: the queue-time histogram is receiving
observations, and both prefix-cache counters are present.

**W1.10 Documentation.** The metric tables in `docs/llm-simulation.md`, `CONTRIBUTING.md` if
the profile schema gains required fields, and `CHANGELOG.md` under `[Unreleased]`.

## W2 — Drift on the metric set, not just the buckets

**W2.1** Extend `scripts/check-vllm-buckets.py`. Keep the bucket check; this is an addition.
Rename the file only if you also update every reference to it (`Taskfile.yml`, `ci.yml`,
`tests/README.md`, `CONTRIBUTING.md`, `docs/versions.md`).

**W2.2** Use the philosophy that already makes the bucket check robust — do not model
upstream's file structure. AST-walk both files for string literals beginning with `vllm:`
and compare sets. Instant, and survives upstream refactors.

**W2.3 ⚠️ Get the `_total` rule right.** A blanket strip is wrong, because
`vllm:iteration_tokens_total` is declared upstream *with* the suffix. Match in this order:

```
exact match against upstream                                  -> matched
else, name ends "_total" and the unsuffixed form is upstream
     and the suffixed form is NOT upstream                    -> matched (client suffix)
else                                                          -> ours-only
```

Exclude the v0 aliases in `METRIC_SURFACES` from the ours-only set; they are deliberately
not upstream's current surface.

**W2.4 Classify, and keep the exit codes meaningful.** The docstring documents
`0 in sync · 1 drift · 2 could not check`.

- **We emit something upstream no longer declares** → drift, **exit 1**. This is the rename
  case that cost two releases.
- **Upstream declares something we do not emit** → a gap, listed clearly, **exit 0**.
  Upstream adds metrics regularly; reddening the weekly run for each one trains people to
  ignore it.
- Cannot fetch or parse → **exit 2**, unchanged.

**W2.5** Rename the CI job at `ci.yml:123` ("vLLM bucket drift") to reflect the wider
remit, and update the script's docstring, which is its specification.

## Non-goals

- The other absent upstream metrics (`request_prefill_time_seconds`,
  `iteration_tokens_total`, the `kv_block_*` family, LoRA, multimodal). W2 will report them
  as a visible gap; closing them is separate work.
- Renaming any existing recorded series. `llm:tpot:p95_5m` keeps its name.
- ~~A container image for the simulator~~ — **SUPERSEDED 2026-07-31: the image is now IN
  SCOPE** (`CONTRIBUTING.md`, `docs/architecture.md`). Struck in place rather than deleted,
  because the reasoning still governs *this* prompt: nothing in W1 or W2 here builds,
  pushes or patches an image, and the simulator stays stdlib-only Python that
  `--selftest` runs directly. Any `pip install` remains out of scope, unchanged.
- Anything in `scripts/install.sh`, the Helm chart, or CI beyond W2.5 — see
  `prompt-packaging.md`.

## Acceptance criteria

1. `python3 scripts/llm-sim.py --selftest` passes and covers the new series: bucket
   monotonicity, `+Inf` consistency, HELP/TYPE, **no `source` label**, and two consecutive
   renders identical.
2. With the shipped profiles unchanged, `task rule-tests` passes **without any expected
   value in `tests/rules/llm-rules_test.yaml` being re-derived**. Prefix caching defaults to
   0.0 and the capacity arithmetic is untouched.
3. **Queue time is asserted as an identity, not as a statistic.** For every completed
   request the selftest drives, `ttft == queue_time + prefill` exactly, floating point
   aside. That relation is available at `llm-sim.py:446`, and it is stronger than any
   quantile comparison: a p95-versus-p95 check with a tolerance can pass while the wiring
   is wrong. The selftest already drives the clock as a parameter
   (`sim.advance_to(step * 5.0)`), so this is instant and deterministic.
4. The shipped profiles produce the rates W1.4 sets — steady ≈0.35, saturated ≈0.15 — and
   **TTFT is unchanged** against the same profiles at 0.0. Unchanged is the correct result
   per W1.4, and asserting it stops someone "fixing" the flat-prefill model without
   re-deriving the capacity arithmetic. A profile at 0.0 still emits both counters, flat at
   zero — present, because an absent series and a zero one are different things to a panel.
   Keep the 0.0 case as a committed fixture under `tests/fixtures/`.
5. `python3 scripts/check-vllm-buckets.py` exits 0 against current upstream and prints the
   gap list; exits 1 if a name we emit disappears upstream; exits 2 with no network.
   Verify the `_total` rule specifically: `vllm:prompt_tokens_total` matches, and a stubbed
   `vllm:iteration_tokens_total` also matches rather than reporting drift. Both cases must
   be **permanent unit tests over a fixture**, not a manual experiment — the checker's
   matching logic has to be callable against a stubbed upstream set that lives in the repo.
6. `--vllm-surface v0` emits `vllm:gpu_prefix_cache_hit_rate` and neither counter;
   `v1` emits the two counters and no gauge; `both` emits all three. Asserted by the
   selftest, which already covers the surface flag for the two existing renames.
7. `task local:up` and both CI `stack` legs still pass unchanged.
8. `task dashboards` regenerates `dist/` without error.
9. `./scripts/verify.sh local` passes **including the new L6**, on a real cluster. The
   selftest proving a series exists and Prometheus proving it arrives are different claims.

## Process

- **One logical change per commit** (`CONTRIBUTING.md`). W1 and W2 are separate commits at
  minimum; the dashboard panel is reasonably a third.
- **`CHANGELOG.md` always**, under `[Unreleased]`, saying *why*. W1 changes what a cluster
  exposes after install, so it belongs in the notes a user reads before upgrading.
- **Work on a branch and open a PR.** `main` carries a ruleset requiring the CI checks, and
  it has an admin bypass — so a direct push *succeeds* while reporting
  `Bypassed rule violations`, and the work lands without ever being gated. Let CI gate it.
- Do not weaken an existing check to get green. A newly failing check is a finding to
  report, not a threshold to move.
