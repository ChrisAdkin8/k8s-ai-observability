# Prompt: The request phase breakdown, and a published simulator image

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.5.0**: the prefill/decode/inference phase breakdown and the published simulator image. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Three
pieces of work, in this order:

1. **W1** — emit `request_prefill_time_seconds`, `request_decode_time_seconds` and
   `request_inference_time_seconds`, and put the phase breakdown on the vLLM board.
2. **W2** — publish the simulator as a container image, so it is usable outside this repo.
3. **W3** — the Helm chart, which is **not specified here**. `prompt-chart.md` is the
   brief and now carries a STATUS box with everything this work changes underneath it.
   All W3 contributes below is the ordering constraint.

**Why.** The board answers "how long did the request take" and cannot answer "doing
what". TTFT and queue time are shipped, so the *waiting* half is covered and the *serving*
half is not — there is nothing to build a prefill-versus-decode panel against, which is
the first thing anyone working on disaggregated serving looks for. W2 turns
`scripts/llm-sim.py` from a file inside this repo into a tool someone can point at their
own dashboards, which is how a simulator actually gets used. W3 removes the last install
objection.

**⚠️ Run order matters, and only in one place.** W1 and W2 are independent. **W2 must
precede the chart**: the image changes what the chart has to carry, and doing the chart
first means solving W-C2 for a file that no longer needs to reach it. See W3.

### Effort, and where to stop

Estimates from reading the code, not from doing the work. Treat the ordering as firmer
than the numbers.

| | Estimate | Standalone? |
|--|--|--|
| W1.1–W1.4 the three histograms + selftest | ~half a day | yes |
| W1.5–W1.7 rules, panel, promtool tests | ~half a day | needs W1.1–W1.4 |
| W1.8 `verify.sh` L8 | ~1 hour | needs W1, and a cluster |
| W2 image + workflow + smoke test | ~half a day | yes |
| the chart (`prompt-chart.md`) | 1–2 days | needs W2 — not specified here |

**If you can only do one, do W1.** It is the largest gap between what the board claims and
what it can show, and it is pure extraction — every number already exists. **W2 is the
highest-leverage per hour**: it reversed a stated non-goal, but that reversal is already
decided and recorded (W2.1), so there is nothing left to agree.

Stopping after W1.4 is coherent: the metrics exist and are tested, nothing on the board
changes, and nothing needs re-publishing.

---

## Background / Facts — VERIFIED 2026-07-31

Every fact below was read in the file cited, or fetched from upstream on that date. Where
one turns out to be wrong anyway, correct it in your commit message.

### The phases already exist in the simulator — this is EXTRACTION, not modelling

`scripts/llm-sim.py` already computes every quantity W1 needs:

| Line | Code | Is |
|--|--|--|
| `:413-414` | `__slots__ = (…, "prefill", "itl", "finish_at", …)` | `prefill` is already a field — and this is `__slots__`, so W1.3's new fields must be declared here |
| `:561` | `prefill = p["base_ttft_seconds"] * self._jitter()` | the prefill duration |
| `:569` | `req.queue_time = self.now - req.arrived` | the WAITING phase |
| `:570-571` | `req.prefill = prefill` · `req.ttft = req.queue_time + prefill` | already stored, already an identity |
| `:596` | `req.finish_at = self.now + prefill + req.gen_tokens * req.itl` | **decode is `req.gen_tokens * req.itl`** |
| `:607-616` | `h_ttft` · `h_e2e` · `h_queue` observations | where the new observations belong |

So this is the same move W1.5 of `prompt-fidelity.md` made for queue time: **observe a
term that already exists, at the point the others are observed, and nowhere else.** No new
modelling, no capacity arithmetic to re-derive, no profile changes, no promtool
expectations to move.

### Upstream shares ONE bucket list across all five request histograms — VERIFIED

Fetched `vllm/v1/metrics/loggers.py` from `main`. `request_latency_buckets` is declared
once at `:889` and passed to **all five** request-scoped histograms:

| Upstream name | Declared | Buckets | Here? |
|--|--|--|--|
| `vllm:e2e_request_latency_seconds` | `:913` | `request_latency_buckets` | yes |
| `vllm:request_queue_time_seconds` | `:923` | same | yes |
| `vllm:request_inference_time_seconds` | `:933` | same | **no** |
| `vllm:request_prefill_time_seconds` | `:943` | same | **no** |
| `vllm:request_decode_time_seconds` | `:953` | same | **no** |

**⚠️ Add no bucket constant.** `E2E_BUCKETS` (`llm-sim.py:119-121`) *is* that list, and its
comment at `:114-118` already says upstream shares it "across several request-scoped
histograms" — that statement is now load-bearing for five metrics rather than two. Add
nothing to `OURS` in `check-vllm-buckets.py`: the existing entry already watches these
boundaries on behalf of all of them. Transcribing a second copy would duplicate a list the
repo already holds, in the repo that refuses second copies.

### The phase semantics, from upstream's own documentation strings

- queue = "time spent in WAITING phase"
- inference = "time spent in RUNNING phase"
- prefill = "time spent in PREFILL phase"
- decode = "time spent in DECODE phase"

Which gives two identities, and they are what W1 should be tested on:

```
inference = prefill + decode        exact in floating point  (see W1.4)
e2e       = queue    + inference    exact to ~1e-14, NOT bit-exact
```

⚠️ **They are not equally exact, and assuming they were is a mistake this brief already
made once.** Both hold algebraically; only the first survives an `==`. W1.4 has the
measurements and the reason.

### ⚠️ Upstream labels these `model_name` AND `engine`; we emit only `model_name`

VERIFIED at `loggers.py:468`: `labelnames = ["model_name", "engine"]`. Every `vllm:` series
this repo emits carries `model_name` alone.

**This is real drift and the weekly check cannot see it** — `check-vllm-buckets.py`
compares metric *names* and bucket *boundaries*, not label sets. Worth knowing before you
decide anything: that is a blind spot, not an oversight you are fixing here.

**Recommendation: do NOT add `engine` in this work, and record why.** It would change the
label set of every existing series at once, which moves every `by (model_name)`
aggregation's cardinality, every promtool `exp_labels`, and every dashboard legend — for
no panel anyone has asked for. If it is ever added it should be its own change, with its
own migration note, because it is a MAJOR-class change by this repo's own definition
(`CHANGELOG.md` — "metric or recording-rule names"). Note it in `docs/llm-simulation.md` as
known, deliberate divergence.

### What NOT to add, and why

The drift check currently reports 25 upstream metrics this simulator does not emit. Most
should stay unemitted, because **the simulator does not model the thing they measure and a
value would be invented**:

| | Why not |
|--|--|
| `vllm:iteration_tokens_total` | there is no iteration loop here; the scheduler is event-driven on completions, so a per-iteration token count has nothing behind it |
| the `kv_block_*` family | no eviction model, no block lifetime |
| `vllm:lora_requests_info`, `vllm:mm_cache_*` | no LoRA, no multimodal |
| `vllm:request_time_per_output_token_seconds` | deliberately excluded — see `llm-prometheusrule.yaml`, it is a per-request mean and inviting confusion with `tpot` |

The three in W1 are different in kind: they are **already computed**. That is the bar. An
invented number presented as a modelled one is the failure the profile comments in
`manifests/llm/10-profiles.yaml` exist to prevent.

### How the simulator reaches a pod today — relevant to W2

`scripts/install.sh` builds a ConfigMap from the file
(`create configmap llm-sim-script --from-file=llm_sim.py=scripts/llm-sim.py`), and
`manifests/llm/20-simulators.yaml` runs `python:3.12-slim` with it mounted. Since the
checksum-annotation fix, changing the file rolls the pods.

That path is **not** what W2 replaces. See W2.3.

### The chart brief already exists

`prompt-chart.md` is a complete, unimplemented brief — there is no `charts/` directory.
Its 15 `W-C*` requirements stand, and a **STATUS box was added to the top of it on
2026-07-31** recording what this work changes: its prerequisites are satisfied, and the
image alters W-C2. Those deltas live there rather than here, so that someone opening the
chart brief sees them without needing to know this file exists.

---

## W1 — The request phase breakdown

**W1.1 Emit the three histograms.** `vllm:request_prefill_time_seconds`,
`vllm:request_decode_time_seconds`, `vllm:request_inference_time_seconds`, labelled
`model_name` like everything else, **no `source` label** (`llm-sim.py:937` asserts its
absence across every `vllm:` series), reusing `E2E_BUCKETS`.

**Why emit `inference` when it is `prefill + decode`?** Because a dashboard or alert
written against a real deployment binds to the name upstream publishes, and the point of
this simulator is that such a query transfers unchanged. A derivable series is still a
series someone's PromQL references. It costs one histogram and it is the difference
between "your panel works here" and "rewrite your panel for the rig".

**W1.1a ⚠️ The v0 surface needs NO entry — VERIFIED, do not add one.** Checked against
`vllm/engine/metrics.py` at tag `v0.6.6`: all three names exist there **identically
spelled**, alongside `vllm:request_queue_time_seconds`. So there is nothing for
`METRIC_SURFACES` to map and nothing for `METRIC_RESHAPES` to reshape — the same
conclusion `prompt-fidelity.md` W1.6 reached for queue time, for the same reason.

This is stated because the question is mandatory, not because the answer is interesting:
every metric added to this simulator has to be asked "did v0 spell it differently, or
shape it differently", and a brief that stays silent invites a guess.

**W1.1b ⚠️ Write each metric name as its own STRING LITERAL. Do not factor the three into
a loop — the drift check goes blind, and then red.**

`check-vllm-buckets.py` discovers what this repo emits by AST-walking `llm-sim.py` for
**string literal constants** matching `vllm:[A-Za-z0-9_]+` (`metric_names`, `:125-140`).
That is deliberate — it is structure-blind and survives refactors — but it only sees names
that exist as literals.

Three metrics differing by one word is exactly the shape someone tidies into a loop:

```python
for phase in ("prefill", "decode", "inference"):                     # ⚠️ DO NOT
    out += hist[phase].render(f"vllm:request_{phase}_time_seconds", model, ...)
```

MEASURED against the real checker. The only literal left is the f-string's constant head:

| written as | drift check sees |
|--|--|
| one literal per metric | `vllm:request_decode_time_seconds`, `vllm:request_prefill_time_seconds` |
| factored into a loop | **`vllm:request_`** — and nothing else |

Both consequences are bad and neither points at the cause:

- the three names are **invisible**, so they stay in the gap list and acceptance criterion
  5's "25 → 22" quietly fails;
- `vllm:request_` is a name we appear to emit that upstream does not declare, which is the
  **drift** direction — `exit 1`, and the weekly CI job goes **red**.

The metrics themselves would be emitted perfectly and the board would work. Only the
checker misreads, so the obvious conclusion is that the checker is broken. Keep three
literals.

⚠️ **This does not contradict the "no second copies" rule invoked elsewhere in this brief**
(the `E2E_BUCKETS` reuse, and W2.2 on the image). That rule is about one *value* existing
in two places, where the two can drift apart. Three distinct metric names are three
distinct values — there is no duplicate to drift. What repeats is the `render(...)` call
shape, and repeating a call shape is not a second copy of anything.

**W1.2 Observe them where the others are observed** (`llm-sim.py:607-616`), and nowhere
else. `advance_to()` mutates, `render()` only reads — a scrape must never move a number,
and the selftest asserts it by rendering twice.

**W1.3 Store decode and inference as ASSIGNED fields, do not recompute them.** Decode is
`req.gen_tokens * req.itl`, which `:596` already computes to schedule `finish_at`.

⚠️ **`Request` uses `__slots__` (`llm-sim.py:413-414`) — declare the two new fields there
first, or the assignment raises `AttributeError: 'Request' object has no attribute 'decode'
and no __dict__ for setting new attributes`.** Verified by trying it. This is the first
thing that will stop you, and it is thirty seconds of confusion only if you were not told.

⚠️ **Assign them AFTER `req.itl`, not beside `req.prefill`.** The order in `_admit()` is
`queue_time` (`:569`) → `prefill` (`:570`) → `ttft` (`:571`) → **`itl` (`:572`)**, so
`req.gen_tokens * req.itl` is unavailable at `:570`. Put them with `finish_at` at `:596`,
which is where the same product is already formed:

```python
req.decode    = req.gen_tokens * req.itl
req.inference = req.prefill + req.decode      # assigned, not derived at observation time
req.finish_at = self.now + req.prefill + req.decode   # was: + prefill + gen_tokens * itl
```

Note the third line: rewriting `finish_at` in terms of the new fields is what keeps **one**
expression for the quantity. Leaving it as `prefill + req.gen_tokens * req.itl` beside a
separate `req.decode` recreates the two-expressions problem this requirement exists to
avoid, and the drift would be invisible — both spellings evaluate the same until one is
edited.

Two reasons, and the second is what W1.4 depends on. Two expressions for one quantity is
how they drift apart when `itl` next changes — and an *assigned* `inference` makes the
first identity below exact by construction rather than merely true in algebra.

**W1.4 ⚠️ Assert the identities — but only ONE of them is bit-exact, and the brief you are
reading got this wrong once already.**

```
inference == prefill + decode           BIT-EXACT. Assert with ==.
e2e       == queue_time + inference     NOT bit-exact. Assert with a tolerance.
```

**Why the first is exact:** with W1.3's assignment, `inference` *is* `prefill + decode`,
the same expression evaluated once. This is the same reason the existing
`ttft == queue_time + prefill` assertion holds — `req.ttft` is literally assigned
`req.queue_time + prefill` at `llm-sim.py:571`. Exactness there comes from **assignment**,
not from algebra, which is the distinction that matters here.

**Why the second is not.** MEASURED, 1538 completed requests across three seeds:

| | |
|--|--|
| `self.now == req.finish_at` at completion | **1538 / 1538** — the clock is exact |
| `e2e == queue + prefill + decode` bit-exact | **107 / 1538 (7%)** |
| within `abs_tol=1e-9` | **1538 / 1538** |
| worst absolute / relative error | `5.24e-14` / `2.96e-14` |

The algebra is right and the clock is exact; the **association order** differs. `e2e` is
`(admit + prefill + decode) - arrived` (from `finish_at`), while the identity computes
`(admit - arrived) + prefill + decode`. Those are not bit-identical in IEEE 754, so an
`==` here fails on **93% of correct requests**.

Use `math.isclose(e2e, queue + inference, rel_tol=0, abs_tol=1e-9)` — five orders of
margin over the measured worst case — and **comment that the residual is reassociation,
not a wiring fault**, or someone will later "fix" the simulator to chase it.

⚠️ **This is not the tolerance this repo warns about.** The objection elsewhere is to
comparing a *statistic* to a statistic with a tolerance, which can pass while the wiring is
wrong. This compares an *identity* whose only error term is float reassociation, bounded at
~1e-14: a real wiring fault moves it by milliseconds, twelve orders of magnitude clear.

Extend the existing identity block (`llm-sim.py:1016-1023`); do not write a second one. The
clock is a parameter (`sim.advance_to(step * 5.0)`), so this stays instant and
deterministic.

**W1.5 Recording rules.** Given W1.6, the breakdown needs **mean** rules, not quantile
ones:

```
llm:queue:mean5m       rate(vllm:request_queue_time_seconds_sum[5m])   / clamp_min(rate(..._count[5m]), 1e-9)
llm:prefill:mean5m     rate(vllm:request_prefill_time_seconds_sum[5m]) / clamp_min(rate(..._count[5m]), 1e-9)
llm:decode:mean5m      rate(vllm:request_decode_time_seconds_sum[5m])  / clamp_min(rate(..._count[5m]), 1e-9)
llm:e2e:mean5m         rate(vllm:e2e_request_latency_seconds_sum[5m])  / clamp_min(rate(..._count[5m]), 1e-9)
```

**⚠️ FOUR rules, not three — `llm:e2e:mean5m` is load-bearing and easy to leave out.** It
is the right-hand side of the "does the breakdown add up" test (W1.7, criterion 3b), and
without it that test **cannot be written over the recorded rules at all**. `e2e` is an
existing series that has never had a mean rule, so it does not announce its absence.

**⚠️ And it has to be a RULE, not an inlined expression, because of the `source` label.**
This repo's convention — stated two paragraphs down — is that recorded series carry
`source: simulated` and raw `vllm:` series do not. So combining a recorded mean with an
inlined `rate(vllm:e2e_..._sum)/rate(..._count)` matches **nothing**, and the obvious repair
(`on(model_name)`) then drops `source` from the result. VERIFIED with promtool — the test
fails like this, with the correct value:

```
exp: {model_name="m", source="simulated"} 0E+00
got: {model_name="m"}                     0E+00
```

Right arithmetic, wrong labels, and it reads as an arithmetic bug. Recording all four with
the same label set makes the comparison label-symmetric and needs no `on()` clause at all.

⚠️ **Clamp the denominator at an epsilon, not at 1** — `clamp_min(x, 1e-9)` — for exactly
the reason `llm:prefix_cache:hit_ratio5m` does: a low-traffic tenant can genuinely complete
fewer than one request per second, and flooring at 1 would silently under-report the mean
of precisely the deployments least likely to notice. An idle tenant then reads `0/1e-9 = 0`,
a flat line rather than a `NaN`. VERIFIED with promtool against a zero-traffic fixture.

A `llm:decode:p95_5m` in the existing `histogram_quantile` shape is fine to add alongside
if you want a tail view — decode is the one phase these buckets resolve tolerably on both
tenants. **Do not add a prefill p95 rule** (W1.6 measured it at ~3x) **and do not add an
e2e one** (1.71x under saturation). A recorded series is exactly how a wrong number
acquires an air of authority.

Aggregate `by (model_name)`, never globally. Carry `source: simulated` as the other
recorded series do; the raw `vllm:` series do not.

**W1.6 The panel — and ⚠️ build it from MEANS, not p95s. Two independent reasons, both
measured.**

The obvious design is a **request phase breakdown**: p95 queue, prefill and decode per
tenant, answering "where did the time go". That is the right panel and the wrong statistic.

**Reason 1 — quantiles are not additive, so a p95 breakdown does not add up.** Measured on
the steady tenant with *perfect* resolution, no bucket error involved:

```
p95:   queue 0.000 + prefill 0.094 + decode 7.379 = 7.473   vs p95(e2e) 7.468   DOES NOT ADD UP
mean:  queue 0.001 + prefill 0.080 + decode 5.020 = 5.101   vs mean(e2e) 5.101  ADDS UP EXACTLY
```

Expectation is linear; the 95th percentile is not. A stacked breakdown whose segments do
not sum to the total reads as a bug in the rig, forever, to everyone who looks at it.

**Reason 2 — `E2E_BUCKETS` cannot resolve prefill at this operating point, and a p95 there
is nearly meaningless.** `base_ttft_seconds` is `0.08` and the first bucket boundary is
`0.3`, so **every prefill observation lands in the first bucket** and
`histogram_quantile` interpolates from zero across it:

Measured on **both** shipped tenants (they differ only in `arrival_rate_rps`), over ~1000
completed requests each:

| | steady | saturated |
|--|--|--|
| prefill | **~3x** (0.095s → 0.285s) | **~3x** (0.095s → 0.285s) |
| decode | 1.26x | 1.12x |
| e2e | 1.25x | **1.71x** (67.97s → 116.26s) |

For scale, the inter-token-latency caveat already on the board — the one with its own
section in the catalog page — is a **1.08x** effect. Prefill is three times worse than
that, on both tenants.

⚠️ **Note the e2e column.** On the saturated tenant it is the *worst* of the three, and
that is the tenant the board exists to show. This is the same effect `verify.sh` L3b
already documents: the saturated queue wait is `160 / 2.74 ≈ 58s` by Little's Law, p95 sits
above that, and the `(80, 160]` bucket it lands in interpolates to 152. Coarse resolution
up there is known, and is why L3b's bound is 120s rather than 60s.

**So: `rate(..._sum[5m]) / rate(..._count[5m])` per phase.** A histogram mean carries **no
bucket dependence at all** — `_sum` and `_count` are exact — so it is immune to Reason 2,
and it is additive, so it is immune to Reason 1. It is the correct statistic for a
breakdown.

⚠️ **Do not "fix" this by adding a bucket constant with finer low-end resolution.** The
boundaries are transcribed from upstream and are what makes a query built here transfer
unchanged; changing them would trade a real property for a cosmetic one. **This caveat
transfers too** — real vLLM uses these same boundaries, so a real deployment with
sub-300ms prefill reads exactly as high. That makes it worth *documenting on the board*,
in the style of the ITL caveat, rather than engineering around.

Keep a p95 panel if you want one, but scope it to **decode** — the only phase these
buckets resolve tolerably (1.1–1.3x) across both tenants — and carry the caveat. **Not
e2e**: it is the worst of the three under saturation, per the table above.

The board has 10 panels and its lowest row ends at `y=50`; add below rather than
reflowing, so existing users' muscle memory survives.

The board has 10 panels and its lowest row ends at `y=50`; add below rather than
reflowing, so existing users' muscle memory survives. Keep the seconds unit and the
`by (model_name)` breakout every other panel uses.

**W1.7 promtool tests.** The primary output of W1.5 is now **mean** rules, so most of these
are mean tests, and they are the easy kind: `_sum / _count` is arithmetic with no
interpolation, so an expected value is a division you can do in your head and it is
identical on every architecture.

**⚠️ The test that matters most is that the breakdown ADDS UP.** W1.6 chose means precisely
because they are additive; assert that property rather than trusting it:

```promql
(llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m   ==  0
```

All four are recorded with `source: simulated`, so this needs **no `on()` clause** — see
W1.5 for the label mismatch you get if `llm:e2e:mean5m` is inlined instead. VERIFIED with
promtool: the rules check clean and this test returns SUCCESS against exact-division
fixtures (`20/10 + 5/10 + 40/10 == 65/10`).

Feed `_sum`/`_count` pairs whose division is exact — the same discipline the prefix-cache
tests use, where the counters sit in 2:1 and 4:1 ratios so the result is exact on any
architecture. Without this test, a future change that swaps a mean rule back to a quantile
produces a panel that silently stops summing, which is the entire failure W1.6 exists to
avoid.

Also cover the epsilon clamp on the zero-traffic case: an idle tenant must read `0`, not
`NaN` and not `+Inf`.

**⚠️ If you add the optional `llm:decode:p95_5m`, read
`tests/README.md#check-a-new-expected-value-on-amd64-before-committing-it` first.**
`histogram_quantile` is not bit-identical across architectures — this repo has already been
bitten by `2.4250000000000003` on arm64 versus `2.425` on amd64, and promtool compares
exactly. Put all of a tenant's observations inside ONE bucket so the expected quantile is a
two-term calculation, and either check it on real linux/amd64 or choose inputs whose
quantile lands on a boundary. **This paragraph applies to that one rule only** — the mean
rules above are immune.

**W1.8 A `verify.sh` check — house pattern.** The LLM checks in `verify.sh` are
`L1, L2, L3, L3b, L4, L4b, L5, L6, L7`. Add **L8**: all three histograms are receiving
observations on a real cluster. **Do not renumber anything** — the numbers are cited across
the repo, and new checks take the next free label. Single-shot is fine, in the style of L7:
by the time it runs, L6 has polled for minutes and the first scrape landed long ago.

⚠️ **`L8` already means something else in `prompt-llm-sim.md`, and this is the second time
that has happened. Taking it anyway, deliberately, and here is the reasoning so it is not
re-litigated.**

That brief states at `:861-862` that *"`L7` and `L8` are **not** `verify.sh` checks"* — its
`L7` is "every pre-existing check still passes" and its `L8` (`:939`) is "`teardown.sh`
removes the namespace…". Neither is a check `verify.sh` runs. But `verify.sh` has since
taken `L7` for the queue-time and prefix-cache assertion, so that label already denotes two
different things depending on which document you are reading.

The alternative — skipping to `L9` to dodge the clash — leaves a hole in `verify.sh`'s
sequence to protect a label in a brief that explicitly says it is not a `verify.sh` check.
That trades a real gap for a notional one. So: **`verify.sh`'s labels are authoritative for
`verify.sh`**, they are contiguous, and `prompt-llm-sim.md`'s `L7`/`L8` are acceptance
criteria for the run and the teardown rather than script checks.

Whoever does this should add one line to `prompt-llm-sim.md` next to `:861` recording that
divergence, so the third occurrence does not have to be rediscovered.

**W1.9 Documentation.** The metric tables in `docs/llm-simulation.md`, the panel table and
the "what it needs" tiers in `manifests/dashboards/llm-sim-overview.grafana-com.md`, and
`CHANGELOG.md` under `[Unreleased]` saying *why*.

⚠️ **The bucket caveat from W1.6 belongs on the catalog page, not just in a commit
message.** That page already carries a "Read the inter-token latency panel carefully"
section for a 1.08x effect; prefill is 3.03x and, like ITL, it **transfers to real vLLM**
because the boundaries are upstream's. Someone importing 25620 against their own
deployment will see the same thing, and the page is the only place that reaches them.
Say plainly: the breakdown is means because quantiles neither add up nor resolve prefill
here, and do not build a prefill SLO on a p95 from these buckets.

**W1.10 ⚠️ The board is published — 25620 needs a new revision.** `task dashboards` must
regenerate `dist/` cleanly; `scripts/dashboard-publish.py` fails and emits nothing if a
panel carries a hardcoded datasource uid, which is exactly what editing in the Grafana UI
and pasting the JSON back produces. Upload as a **revision of 25620, never as a new
dashboard** — a second upload mints a second id and everyone on the first silently stops
receiving fixes. See `manifests/dashboards/README.md`.

---

## W2 — The simulator as a container image

**W2.1 The non-goal was reversed on 2026-07-31 — this is DECIDED, not proposed.**

`CONTRIBUTING.md` and `docs/architecture.md` no longer list a simulator image as out of
scope, and the three briefs that restated it (`prompt-fidelity.md`,
`prompt-packaging.md`, `prompt-chart.md`) are annotated as superseded in place. There is
nothing left to agree; implement it.

The reasoning, recorded so it is not re-litigated: the old rule — *"stdlib-only Python
mounted into a stock image, so there is nothing to build, push or patch"* — was about **how
this rig runs the simulator**, and remains true of that path. It said nothing about **how
anyone else consumes it**, which is the case an image serves. The stdlib-only constraint is
what makes the image trivial (a `FROM python:3.12-slim` and a `COPY`), and it is unchanged.

⚠️ **`pip install` is a separate non-goal and was NOT reversed.** The image ships the same
stdlib-only file. The two are habitually stated in one breath; only one of them moved.

**W2.2 Build FROM `scripts/llm-sim.py`. Never vendor a copy.** The image is a derived
artefact on the same terms as `dist/` and the dashboard ConfigMaps: one source, several
forms. A `COPY scripts/llm-sim.py /app/llm_sim.py` from the repo root context is the whole
Dockerfile. **A committed second copy under `docker/` or `images/` is not acceptable** —
a drifted copy of the simulator would be undetectable from the outside, which is precisely
the property the DCGM surface contract exists to prevent elsewhere.

**W2.3 ⚠️ Do NOT switch the cluster path to the image in this work.** `install.sh` keeps
building the ConfigMap from the file. Three reasons, and the third is the one that bites:

- `task selftest` and `--print` run the file directly with no build step, which is what
  makes the simulator editable in seconds;
- the compose path mounts the same file, so an image would fork the two;
- an image-based Deployment pins a **tag**, so a local edit to `llm-sim.py` would stop
  reaching the cluster — silently, since the pod would still be Running. That is the exact
  failure the checksum annotation was just added to fix.

The image is for **external consumers**. Say so in its README, or someone will helpfully
"simplify" the Deployment onto it.

**W2.4 Publish to `ghcr.io` from a tag.** `GITHUB_TOKEN` is sufficient — **no new
secrets**, which matters because fork PRs never receive them.

⚠️ **`ci.yml:37-38` sets `permissions: contents: read` for the whole workflow, and a push
under that fails with a 403 that reads like a missing secret.** Job-level permissions
override the workflow default, so the publish job needs its own block:

```yaml
    permissions:
      contents: read
      packages: write
```

Without it someone spends an afternoon configuring credentials that were never the
problem — the misleading-failure class this repo documents everywhere else.

**Tag from the release tag, and make the link back machine-readable**, via OCI labels
rather than convention:

```
org.opencontainers.image.revision   = <commit sha>
org.opencontainers.image.version    = <release tag>
org.opencontainers.image.source     = <repo url>
```

`docker buildx build --label` sets them; `docker inspect` reads them back. An image whose
version cannot be tied to a commit is one nobody can debug.

**Multi-arch `linux/amd64,linux/arm64` via buildx** — it is a Python file, so the build
cost is negligible and an arm64 laptop is a first-class consumer.

**W2.5 A smoke test in CI, not a build-only job.** Building proves the Dockerfile parses.
Run the image, scrape `/metrics`, and assert one series and one HELP line. The job needs
docker, so it belongs with the `compose` job's gating rather than in `fast`.

⚠️ **You cannot natively smoke-test the arm64 image on a GitHub runner — `ubuntu-latest`
is amd64.** Pick one and say which; do not leave it ambiguous, because the ambiguous
outcome is "tested on both" reported after testing one:

- **`docker/setup-qemu-action` then run the arm64 image under emulation.** Genuinely
  exercises the arm64 layer. Slower, and QEMU has its own failure modes that are not your
  image's.
- **Build both, smoke-test only the runner's native arch.** Honest and cheap. Defensible
  here specifically because the two images differ only in base-image layers — the payload
  is one architecture-independent `.py` file — so an arm64-specific runtime failure would
  have to originate in `python:3.12-slim` itself.

The second is recommended. If you take it, **acceptance criterion 8 must be read as
written** — builds for both, smoke-tests one — rather than quietly satisfied by half.

**W2.6 Documentation.** A short section in the README under the simulator, and a note in
`docs/llm-simulation.md` covering: what the image is for, what it is **not** (the cluster
path), the tag scheme, and a one-liner someone can paste. VERIFIED that this works with no
`--profile` — the simulator falls back to `DEFAULT_PROFILE` and serves on 9401:

```sh
docker run --rm -p 9401:9401 ghcr.io/<org>/vllm-metrics-sim:<tag>
```

⚠️ **Document `LLM_SIM_LISTEN_PORT` as the port override, and say why it is not
`LLM_SIM_PORT`.** VERIFIED by running the built image: `-e LLM_SIM_LISTEN_PORT=9999`
serves on 9999, and `-e LLM_SIM_PORT=tcp://10.0.0.1:9401` is **ignored** — it does not
crash. The defence already travels with the file.

The naming is not arbitrary and the image docs are where someone will meet it. kubelet
injects a Docker-link-compatible `<SVCNAME>_PORT` for every Service in the namespace, so a
Service called `llm-sim` sets `LLM_SIM_PORT=tcp://<ip>:9401`; reading *that* name once meant
`int()` got a URL and every pod died at startup, visible only as a blank dashboard
(`default_port()`'s comment has the full account). So two things belong in the docs:

- the override is **`LLM_SIM_LISTEN_PORT`** — someone will try `LLM_SIM_PORT`, find it
  silently ignored, and conclude the image does not support a port override;
- **do not "simplify" it back to the obvious name.** The obvious name is the bug.

---

## W3 — The Helm chart

**Not specified here.** `prompt-chart.md` is the brief, and the deltas that this work
changes underneath it — its prerequisites being satisfied in 0.4.0, and the image altering
W-C2's calculus — are recorded in a STATUS box at the top of *that* file, where someone
opening it will actually see them.

Two documents describing one piece of work is the second-copy pattern this repo refuses
everywhere else; the first draft of this brief had it, and this is the correction.

**All that belongs here is the ordering:** ⚠️ **W2 must precede the chart.** Doing the
chart first means solving W-C2 for `scripts/llm-sim.py`, a file that stops needing to reach
the chart at all once the image exists.

## Non-goals

- The other 22 absent upstream metrics. W1's three are added because they are already
  computed; the rest would be invented. The drift check reports them as a visible gap and
  that is the correct state.
- The `engine` label. Real drift, deliberately deferred — see Background.
- Any change to the capacity model, the profiles, the 2s threshold, or existing recorded
  series names. `llm:tpot:p95_5m` keeps its name.
- Making prefill token-proportional. It is flat by construction and
  `prompt-fidelity.md` W1.4 documents what changing that would re-derive.
- Switching the cluster or compose paths onto the container image (W2.3).
- Publishing the chart to Artifact Hub, or a `helm repo` on GitHub Pages.
- Alertmanager receivers, SLO burn-rate rules, k3d/minikube support.

---

## Acceptance criteria

1. `python3 scripts/llm-sim.py --selftest` passes and covers all three new series: bucket
   monotonicity, `+Inf` consistency, HELP/TYPE, **no `source` label**, and two consecutive
   renders identical.
1b. **`--vllm-surface v0`, `v1` and `both` each emit all three**, and **no
   `METRIC_SURFACES` or `METRIC_RESHAPES` entry was added.** They exist in v0.6.6 under
   identical names (W1.1a), so an unmapped metric is rendered unconditionally and that is
   the correct behaviour — but it is currently unasserted, and the selftest already covers
   the surface flag for the two existing renames, so extend that. `prompt-fidelity.md`
   criterion 6 is the shape to copy.
2. **Both identities asserted per completed request**, in the existing identity block,
   with the right comparison for each: `inference == prefill + decode` under `==`, and
   `e2e` versus `queue + inference` under `abs_tol=1e-9`. Asserting the second with `==`
   fails on ~93% of correct requests — W1.4 has the measurements. Getting this backwards is
   the single most likely way to waste a day on this work.
3. `task rule-tests` passes **with no existing expected value re-derived**. Nothing in W1
   touches the capacity arithmetic.
3b. **The breakdown is asserted to add up**, as a permanent promtool test:
   `llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m` equals **`llm:e2e:mean5m`**
   — all four recorded, so the comparison is label-symmetric and needs no `on()` clause
   (W1.5 has the failure it otherwise produces). This is the single property W1.6 chose
   means for, and an untested design decision is one that gets reverted by someone who does
   not know why it was made.
4. Any **new percentile** expectation was checked on real linux/amd64, or chosen to land
   on a bucket boundary. State which, and how you checked — an arm64-only pass is not
   evidence. **The mean rules in W1.5 are exempt**: `_sum / _count` involves no
   interpolation, so it is not subject to the `histogram_quantile` ULP divergence that
   `tests/README.md` documents. Say so in the test file, or the next person adds a
   pointless caveat to it.
5. `python3 scripts/check-vllm-buckets.py` exits 0, and the reported gap falls from **25 to
   22**. The three names appear as matched, not as drift.
   ⚠️ **If the gap does not move, or a truncated name like `vllm:request_` shows up as
   drift, read W1.1b before touching the checker.** That is the loop-factoring trap, not a
   checker fault — the metrics will be emitting correctly and the board will work, which is
   what makes it convincing. The checker is right; the literals are missing.
6. `task dashboards` regenerates `dist/` without error, and 25620 is re-submitted **as a
   revision** — carrying W1.9's bucket caveat on the catalog page, not only in a commit
   message. A 3.03x overstatement that transfers to real vLLM is the reader's problem, and
   the page is the only thing that reaches them.
7. `./scripts/verify.sh local` passes **including the new L8**, on a real cluster. A
   selftest proving a series is emitted and Prometheus proving it arrives are different
   claims.
8. The image **builds for `linux/amd64` and `linux/arm64`**, and CI **smoke-tests** it
   rather than only building it: `docker run … && curl :9401/metrics` returns the surface.
   ⚠️ `ubuntu-latest` is amd64, so state plainly which arch was actually *executed* and
   which was only *built* — W2.5 has the two options and recommends one. "Tested on both"
   after testing one is the failure this criterion is worded to prevent.
   The OCI labels resolve: `docker inspect` shows a revision that maps to a real commit.
9. **No second copy of `scripts/llm-sim.py`, any dashboard JSON, or any rule file exists in
   the tree.** State the command you used to verify it.
10. `task local:up` and every CI leg still pass unchanged. All of this is addition.

---

## Process

- **One logical change per commit** (`CONTRIBUTING.md`). W1's metrics, W1's panel and
  rules, W2's image, and W3's chart are four commits at minimum.
- **`CHANGELOG.md` always**, under `[Unreleased]`, saying *why*. W1 changes what a cluster
  exposes after install and W2 adds a distributed artefact, so both belong in the notes a
  user reads before upgrading.
- **Work on a branch and open a PR.** `main` carries a ruleset requiring the CI checks and
  it has an admin bypass — a direct push *succeeds* while reporting
  `Bypassed rule violations`, and the work lands without ever being gated. Let CI gate it.
- Do not weaken an existing check to get green. A newly failing check is a finding to
  report, not a threshold to move.
- W2.1's reversal is already decided, so there is no "skip W2" branch to take. If it is
  ever un-decided, the STATUS box in `prompt-chart.md` is what has to change with it — its
  W-C2 conclusion depends on the image existing.
