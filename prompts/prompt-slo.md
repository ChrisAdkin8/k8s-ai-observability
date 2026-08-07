# Prompt: Ship a latency SLO, as a ratio at a bucket boundary

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.6.0**: the TTFT SLO, its burn-rate alerts and verify.sh L9. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Three
pieces of work:

1. **W1** — a TTFT service-level objective expressed as a **ratio at a histogram bucket
   boundary**, with multi-window burn-rate alerts over it.
2. **W2** — prove it in `promtool`, including the windows this rig is too short-lived to
   reach.
3. **W3** — surface it: a panel, the catalog page, and a `verify.sh` check.

**Why.** This repo tells you three separate times not to build an SLO on a percentile —
off prefill p95, off ITL p95, off the 2s threshold — and never supplies the other half.
The analysis behind those warnings is the best content here, and it currently terminates
in a prohibition.

It resolves cleanly, and the resolution is the point of this work: **the bucket-resolution
problem is a property of `histogram_quantile` interpolating *inside* a bucket.** That is
where the measured 3.03x on prefill and 1.71x on e2e come from. A ratio evaluated *at* a
boundary — `rate(bucket{le="X"}) / rate(count)` — does no interpolation and carries no
bucket-width dependence at all. So the caveat is not a reason to avoid SLOs. It is the
design constraint that tells you how to build one: **your threshold must be a boundary.**

That argument is not free of conditions, and it does not survive being shipped as bare
rules. **W3.3 is where it goes, and W3.3 is not optional** — it carries the case and the five
limits a reader needs in order to use this SLO rather than cargo-cult it.

The LLM board is **published** ([25620](https://grafana.com/grafana/dashboards/25620)), so
W3 has an audience and a re-publish obligation. See W3.2.

### Effort, and where to stop

Estimates, derived from reading the code rather than from doing the work. Treat the
ordering as firmer than the numbers.

| | Estimate | Standalone? |
|--|--|--|
| W1 rules + alerts | ~half a day | yes — but two commits, see Process |
| W2 promtool tests | ~half a day | needs W1 |
| W3.1 panel | ~1 hour | needs W1 |
| W3.2 catalog page + re-publish | ~1 hour | needs W3.1 |
| W3.3 docs — **the caveats, not just a table** | ~1 hour | needs W1 |
| W3.4 `verify.sh` L9 | ~1 hour | needs W1, and a cluster |

**W1, W2 and W3.3 are the deliverable; the rest of W3 is optional.** Rules nobody can see
still transfer, and they are what a reader copies — so the panel, the re-publish and the
cluster check can all wait. **W3.3 cannot** — it is where the design argument and its five
limits live, and rules shipped without them are the thing this prompt exists to avoid.
Stopping after W2 + W3.3 is coherent.

## Background / Facts

Every fact below was read or measured directly in the file cited, on 2026-08-01. None of
it is inferred from a comment. Where it turns out to be wrong anyway, correct it in your
commit message.

### ⚠️ There is no 2.0 boundary in TTFT — VERIFIED (`scripts/llm-sim.py:99-101`)

```python
TTFT_BUCKETS = [0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1,
                0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0,
                20.0, 40.0, 80.0, 160.0, 640.0, 2560.0]
```

The list steps `1.0 → 2.5`. The existing `LLMHighTTFT` alert
(`manifests/alerts/llm-prometheusrule.yaml:216-218`) is `llm:ttft:p95_5m > 2`, comparing
against a point **inside** the (1.0, 2.5] bucket, reached by linear interpolation. That
alert is fine and stays — see W1.5 — but it is not a thing an exact SLO can be built on.

### The `le` label strings — VERIFIED by rendering the simulator

`fmt()` (`llm-sim.py:345-355`) is `repr(round(float(value), 6))`, so the exposition carries
`le="1.0"` and `le="2.5"` — not `le="1"`, not `le="2"`. Matches what the Python client
produces against real vLLM.

### Measured good-event ratios — MEASURED, 1h simulated, both shipped profiles

Cumulative, as Prometheus exposes them. `Histogram.buckets` holds **per-bucket** counts and
`render()` accumulates (`llm-sim.py:392-402`) — accumulate before comparing, or you will
read these wrong.

| boundary | steady | saturated |
|--|--|--|
| `le="1.0"` | 99.368% | 0.182% |
| `le="2.5"` | **100.0000%** | **0.3232%** |

### ⚠️ TTFT lands at a different moment here than upstream — VERIFIED 2026-08-01

vLLM appends `first_token_latency` during prefill (`vllm/v1/metrics/stats.py:393-395`) and
observes it from `IterationStats` (`loggers.py:1216-1217`) — **at first-token time**. This
simulator calls `h_ttft.observe()` inside `_complete_one()`, so nothing is recorded until
the request finishes. On the saturated tenant, whose requests take ~60s, the rig's SLI
therefore lags a real one by roughly a request lifetime and a 5m window sees only what
completed inside it.

A genuine fidelity divergence, and one `check-vllm-buckets.py` cannot catch: it compares
names and bucket boundaries, not observation timing. Do not describe the rig's timing as
vLLM's anywhere. Closing it is separate work — note it in the changelog, do not fix it here.

### `LLMKVCacheSaturated` is the precedent for a threshold the rig never reaches

Documented as not exercised in four places — `llm-prometheusrule.yaml:237-238`,
`docs/llm-simulation.md:378`, `tests/README.md:26-27`, `tests/rules/llm-rules_test.yaml:500`
— each naming the workaround. Follow that pattern for the 6h window rather than inventing a
new one.

### Identifiers you will need

- Rule groups: `llm.simulation.derived` (recording) and `llm.simulation.rules` (alerts),
  `llm-prometheusrule.yaml:27` and `:206`.
- Recorded-name convention: `llm:<subject>:<statistic><window>` — `llm:ttft:p95_5m`,
  `llm:prefix_cache:hit_ratio5m`, `llm:queue:mean5m`.
- `verify.sh` checks run L1…L8 (plus L3b/L4b); the next is **L9**. ⚠️ A raw `grep L9`
  hits a *comment* at `verify.sh:514` explaining why an earlier check did not skip to L9 —
  it is not a check. Grep `pass "L` / `fail "L` for the authoritative list.
- Recording rules carry `labels: { source: simulated }`; raw `vllm:` series never do.

---

## W1 — The SLI and the burn-rate rules

**W1.1 The SLI is TTFT ≤ 2.5s; the SLO is 99%.** Use `le="2.5"`, not `le="1.0"`. At 1.0
the steady tenant measures 99.368% — 0.37 percentage points of headroom against a 99%
target, so ordinary jitter flips it and the healthy tenant intermittently reports a blown
budget. That teaches the opposite of the intended lesson. At 2.5 the separation is 100% vs
0.32% and is not close.

**W1.2 ⚠️ `le="2"` matches nothing, and fails silently.** There is no 2.0 bucket. A matcher
that misses returns an empty vector, the ratio evaluates to nothing, and the burn-rate
alert never fires — green forever, on a rule that looks correct. This is the same genre as
`> 90` versus `> 0.9` on the KV-cache fraction, the `on (UUID)` join, and the two BYO
labels. Write the boundary as a named constant in the comment above the rules, state that
it must be a member of `TTFT_BUCKETS`, and cover it in W2.4.

**W1.3 Record the ratio at four windows**, into `llm.simulation.derived`:

```
llm:ttft:slo_ratio5m   llm:ttft:slo_ratio30m   llm:ttft:slo_ratio1h   llm:ttft:slo_ratio6h
```

each of the shape below, where **`$WINDOW` is that rule's own window and appears in both
range vectors** — `[5m]` in `slo_ratio5m`, `[6h]` in `slo_ratio6h`:

```promql
sum by (model_name) (rate(vllm:time_to_first_token_seconds_bucket{le="2.5"}[$WINDOW]))
/ clamp_min(sum by (model_name) (rate(vllm:time_to_first_token_seconds_count[$WINDOW])), 1e-9)
```

⚠️ Spelled out because the copy-paste failure is real and untestable by expected value: a
`slo_ratio6h` built from `rate(...[5m])` returns a plausible number that is simply the 5m
ratio under a 6h name, so every threshold test still passes and the slow-burn alert
silently watches the wrong window. Mismatched windows between numerator and denominator are
worse again. Assert the windows in W2.

`sum by (model_name)`, never globally — a global ratio merges the tenants into a number
describing neither and hides the degraded one. `clamp_min` at an **epsilon, not 1**, for
the reason already written up for the prefix-cache denominator: flooring a rate at 1
under-reports exactly the low-traffic deployments least likely to notice.

Record the ratio and let the alerts compute burn from it. Do not also record a burn-rate
series: burn is `(1 - ratio) / (1 - SLO)`, so a recorded copy is a second spelling of one
quantity, which is how the two drift apart.

**W1.4 ⚠️ An idle tenant reads as a total outage — guard for it.** No traffic means the
clamp gives `0/1e-9 = 0`, an error ratio of `1.0`, and burn pinned at 100x, so a burn-rate
alert **fires hardest on a tenant serving nothing at all**. Gate every alert on traffic:

```promql
and on (model_name) (sum by (model_name) (rate(vllm:time_to_first_token_seconds_count[$SHORT])) > 0)
```

Two things about that line are load-bearing, and both fail silently:

- **`$SHORT` is that alert's own short window** — 5m on the fast alert, 30m on the slow one.
  Using 5m for both means a tenant that burned budget for five hours and went idle ten
  minutes ago gets no slow-burn page. A guard narrower than the alert it protects disables
  the alarm.
- **`on (model_name)` is required.** The recording rules carry `source: simulated` and the
  raw counter does not, so a plain `and` matches on all labels, finds nothing, and
  suppresses every alert. Same mismatch `llm-prometheusrule.yaml:99` documents for
  `llm:e2e:mean5m` — but note `and` takes result labels from the left, so unlike the
  division case there `source` survives and nothing needs recovering.

The guard is a trade, not a free win: it also stops a burned budget alerting once traffic
stops. W3.3 carries that, and the reason someone reading it as redundant will be wrong.

**W1.5 ⚠️ Do not touch `LLMHighTTFT`, and do not "reconcile" the two thresholds.** They are
different instruments and the difference is the teaching point: 2s is an interpolated
percentile that separates the two shipped tenants and whose provenance is documented in
`10-profiles.yaml`; 2.5s is a bucket boundary, so a ratio taken there needs no interpolation
(exact under the condition in W3.3 limit 2, which is not automatic). Changing the alert
to 2.5 would re-derive the profile arithmetic, `verify.sh` L3b's real headroom, and every
expected value in `llm-rules_test.yaml`. Add a comment beside each pointing at the other.

**W1.6 The alerts.** Two multi-window burn-rate alerts against the 99% / 30d budget, in
`llm.simulation.rules`:

| alert | condition | budget consumed |
|--|--|--|
| `LLMTTFTErrorBudgetFastBurn` | burn > 14.4 over **1h** and over **5m** | 2% in 1h |
| `LLMTTFTErrorBudgetSlowBurn` | burn > 6 over **6h** and over **30m** | 5% in 6h |

Both windows must breach, which is what suppresses the single-spike false positive. Against
the shipped profiles: steady burns at 0 and fires neither; saturated burns at ~99.7x and
fires both. Severity `warning` on slow, `critical` on fast, matching the existing labels.

**Assembled, the fast alert is the expression below.** Given in full because the three
pieces above are individually simple and the label matching in W1.4 lives entirely in how
they join — which is where an implementer working from fragments gets it wrong:

```promql
(1 - llm:ttft:slo_ratio1h) / 0.01 > 14.4
  and
(1 - llm:ttft:slo_ratio5m) / 0.01 > 14.4
  and on (model_name)
(sum by (model_name) (rate(vllm:time_to_first_token_seconds_count[5m])) > 0)
```

The first `and` needs no `on()` — both sides are recorded rules carrying the same
`{model_name, source}`. The second does, for the reason in W1.4. The slow alert is the same
shape with `6h`/`30m`, `> 6`, and `[30m]` in the guard.

The 30d budget is nominal and appears in no query. It fixes what 14.4 and 6 *mean* — 14.4x
for 1h consumes 2.00% of a 30d budget, 6x for 6h consumes 5.00%, both checked — but no rule
here computes over 30 days and the rig has minutes of data. Say so where you write the
multipliers, or the next reader goes looking for a 30d window that does not exist.

⚠️ **The target is spelled in three places, and W1.3's own rule says that is how things
drift.** 99% lives in the prose, in the `0.01` divisor (four times across the two alerts),
and inside `14.4`/`6`, which mean what they mean only against a 1% budget. PromQL has no
constants, so this cannot be factored out — and note the division is removable, since
`(1 - ratio) / 0.01 > 14.4` is algebraically `(1 - ratio) > 0.144`.

**Keep the division anyway.** Burn rate is the quantity operators reason about and the term
the SRE workbook uses; `> 0.144` is one fewer constant and a much worse alert to read at
3am. Take the coupling and **pin it with a test instead**: feed the error ratio either side
of 0.144 and assert the fast alert flips exactly there. That pins the *effective* threshold
however it is spelled, catches a divisor and a multiplier drifting apart, and is the same
both-sides-of-the-boundary convention the existing threshold tests already use. Say in a
comment that the test is what holds the three spellings together.

**W1.7 ⚠️ `for:` — state the choice explicitly, whichever way you go.** All seven existing
alerts across both rule files carry one (1m–5m). The multi-window pattern is the argument
for omitting it: the long window already provides the smoothing a `for:` would add, and
stacking both delays a genuine fast burn by the `for:` duration on top of an hour of
averaging. Omitting it is defensible and probably right — but it is a visible break from
house convention, so it needs a comment saying it was a decision. A silent absence reads as
an oversight and will be "fixed".

## W2 — Prove it, including the windows the rig cannot reach

**W2.1 Both sides of both alerts**, in `tests/rules/llm-rules_test.yaml`, following the
existing cases. The rig reaches the fast-burn condition on the saturated tenant within
minutes; the slow burn needs six hours it will never have. promtool does not care — it
synthesises arbitrary series over arbitrary durations in about a second, with no cluster.
That is precisely why the file already covers `LLMKVCacheSaturated`, which nothing on the
rig ever drives.

**W2.2 ⚠️ Add a third, synthetic tenant that burns *partially* — this is the test that
matters.** The two shipped profiles are 0x and ~99.7x: both alerts fire on one and neither
on the other, so **a test using only those two passes even if the fast and slow alerts are
wired to the same expression.** Nothing distinguishes them.

Use the pattern already in this file — `llm-rules_test.yaml:512,520` carries a synthetic
`sim-llama-3-8b-edge` for exactly this job. Give it a good ratio of **0.90**, i.e. burn
10x: above the slow threshold of 6, below the fast 14.4. It must fire
`LLMTTFTErrorBudgetSlowBurn` and **not** `LLMTTFTErrorBudgetFastBurn`. That single case is
the only input in the suite that proves the two alerts are different rules.

**W2.3 Assert the idle case explicitly.** A tenant with zero traffic must fire **neither**
alert. Without W1.4's guard this test fails, which is the point of writing it. Add the
converse for the slow alert too: a tenant idle for ten minutes but with a bad 6h ratio must
**still** fire the slow burn, which is what catches a 5m guard on a 30m window.

**W2.4 Assert the boundary is a real bucket.** A test that feeds `le="2.5"` series and
expects a ratio, plus a check that the recorded rule returns something at all. A typo'd
`le="2"` produces an empty result rather than a wrong number, so an expected-value test
catches it only if something asserts non-emptiness. Prefer a unit assertion over a comment.

**W2.5 Assert each rule's window.** Feed a series that changes over time such that the 5m
and 6h ratios must differ, and assert both. A `slo_ratio6h` accidentally built on `[5m]`
passes every other test in this suite.

**W2.6 Document the 6h window as not exercised**, in the four-place pattern the KV-cache
threshold already uses. State the workaround: shorten the windows in a fork of the rule to
watch it move on the rig.

## W3 — Surface it

**W3.1 One panel**, budget consumed over the SLO window, per tenant. Not a percentile
panel — the whole argument is that this quantity is not one.

Expect it to look boring, and do not fix that by inventing a number. Steady measures zero
bad events in 6,646 requests and saturated is pinned, so the panel reads flat-zero on one
tenant and full on the other, with nothing in between — an accurate picture of these two
profiles, and a poor advertisement for multi-window alerting, whose value lies in the
partial-burn region neither tenant occupies. Say so on the panel or in the catalog page. Do
not reach for a third shipped tenant tuned to burn partially: that is a new Deployment, a
new profile and a third series on every panel on the board, to illustrate one point. The
synthetic `edge` tenant in W2.2 makes it where it is free.

**W3.2 ⚠️ The board is published — three consequences.** Re-run `task dashboards` and
confirm `dist/` regenerates; `dashboard-publish.py` **fails and emits nothing** if a panel
carries a hardcoded datasource uid, which is what editing in the Grafana UI and pasting the
JSON Model back produces. Update `manifests/dashboards/llm-sim-overview.grafana-com.md` —
the panel table, the recording-rule tier and the paste block all change. Upload as a **new
revision of 25620**, never as a new dashboard.

**W3.3 Documentation — not optional, and where the argument lives.**
`docs/llm-simulation.md`'s alert table and `CHANGELOG.md` under `[Unreleased]` are the
mechanical half. The load-bearing half is the catalog page, which today says "do not set an
SLO" three times without ever saying what to do instead. It must now carry the case *and*
its five limits. Rules shipped without these are the cargo-cult outcome this work exists to
prevent — which is why this item is a deliverable and the rest of W3 is not.

**The case.** `histogram_quantile` interpolates *inside* a bucket, which is where the
measured 3.03x on prefill and 1.71x on e2e come from. A ratio evaluated *at* a boundary
does no interpolation and carries no bucket-width dependence. The caveat is therefore not a
reason to avoid SLOs; it is the constraint that tells you how to build one.

**The five limits, each of which must appear:**

1. **The threshold is constrained to the boundaries you have.** If the business wants 2s,
   this method cannot express it — `TTFT_BUCKETS` steps `1.0 → 2.5`. The honest options are
   to move the objective to a real boundary, to accept an interpolated percentile and its
   error bar, or to change the bucket list, which forfeits transferability. This belongs to
   the *approach*, not to this rig, so every reader inherits it.
2. **"Exact" has a condition.** It holds because numerator and denominator are the same
   histogram, on the same target, at the same scrape timestamps, so `rate()`'s extrapolation
   factor is common to both and cancels. Sum across replicas whose scrapes are out of phase
   and the cancellation stops being exact. One pod per model here; a reader running four
   needs to know why it might not hold.
3. **The objective covers requests that reached a first token, so a stall is silent on it.**
   A request that never gets there contributes no observation — not a slow one, not a failed
   one. Say that `LLMQueueBacklog` is what fires when requests arrive and never complete,
   and `LLMMetricsAbsent` when the series stop entirely. An SLO whose scope is unstated gets
   read as covering availability, which this one does not. The W1.4 traffic guard is the
   same trade seen from the other side: it buys silence on idle tenants at the price of
   silence on a budget already burned.
4. **The 6h window is not exercised on the rig** (W2.6), in the four-place pattern
   `LLMKVCacheSaturated` already uses, naming the workaround.
5. **Native histograms would dissolve limit 1 entirely** — one sentence, so the boundary
   constraint reads as a property of classic histograms rather than a law of nature. The
   reasoning for not adopting them here is in Non-goals; the catalog page needs only the
   fact that the constraint has an expiry date.

**W3.4 A `verify.sh` L9**, in the style of L3: assert the four recorded ratios **exist and
are non-empty** for both tenants. The selftest proving a rule is well-formed and Prometheus
proving it evaluates are different claims, and existence is the claim worth making here.

⚠️ **Do not assert the steady tenant is above 0.99.** Five minutes after install the 5m
`rate()` is still under-reading through the documented warm-up, so a 99% threshold on a
fresh cluster is a check that passes most days and fails occasionally for reasons that have
nothing to do with the rules. L3b's bounds are deliberately loose for exactly this reason.
A flaky check gets weakened rather than understood, which the Process section forbids — so
do not write one. If you want a value assertion, bound it far from the threshold (the
steady ratio is 1.0, so `> 0.5` catches a genuinely broken rule and never flakes).

## Non-goals

- **An availability SLO** from `vllm:request_success_total` by `finished_reason`. It would
  work and would exercise the same machinery, but latency is where the existing analysis
  lives and where the boundary insight pays off. Separate work.
- **Changing any bucket list.** The boundaries are transcribed from upstream and are what
  make a query built here run unchanged against a real engine.
- Prometheus **native histograms**, which would dissolve the boundary constraint entirely —
  exponential buckets with configurable resolution, so any threshold is expressible and
  none of W1.1's choosing-between-1.0-and-2.5 arises. They are the right long-term answer
  and the wrong one here: real vLLM emits classic histograms, `check-vllm-buckets.py` pins
  those boundaries weekly, and a native-histogram SLO would not transfer to the deployments
  this repo exists to prepare people for. The catalog page states the fact, not this
  reasoning — see W3.3 limit 5.
- **Changing `LLMHighTTFT`, the profiles, or the capacity arithmetic.** See W1.5.
- **Recorded `p50`/`p99` SLO variants.** One SLI, one target.
- Anything in `scripts/install.sh` or the Helm chart beyond what W1's rules require.

## Acceptance criteria

1. `task rule-tests` passes, with **no existing expected value re-derived**. This work adds
   rules; it changes none.
2. Both burn-rate alerts fire on the saturated tenant and neither fires on the steady one,
   asserted in `llm-rules_test.yaml`.
3. **The two alerts are proven distinct**: a synthetic tenant at 0.90 good (burn 10x) fires
   the slow alert and not the fast one. Without this, criterion 2 passes with both alerts
   wired to the same expression.
4. **An idle tenant fires neither alert** — the W1.4 guard, asserted rather than commented.
   And a tenant idle for ten minutes with a bad 6h ratio **still** fires the slow burn,
   which is what catches a 5m guard placed on a 30m window.
5. **Each rule uses its own window in both range vectors.** A `slo_ratio6h` built on `[5m]`
   must fail a test, not just a review.
6. The recorded ratios are non-empty against a `le="2.5"` series, and the tests would fail
   if the matcher were `le="2"`.
7. **The effective threshold is pinned either side of the boundary** — the fast alert fires
   at an error ratio of 0.1441 and not at 0.1439. This is what holds the target's three
   spellings together (W1.6), and it fails if the divisor and the multiplier drift apart
   however either is written.
8. `python3 scripts/llm-sim.py --selftest` still passes, unchanged. Nothing in this work
   touches the simulator.
9. `task dashboards` regenerates `dist/` without error (if W3.1 is done).
10. `./scripts/verify.sh local` passes including the new L9, on a real cluster (if W3.4 is
   done). L9 asserts existence, not a 99% threshold — see W3.4.
11. The 6h window's non-exercisability is documented in the same four places the KV-cache
    threshold uses.
12. **The case and all five limits are on the catalog page** — boundary constraint, the
    condition on "exact", stall blindness and scope, unexercised 6h window (W3.3). Not
    conditional on the rest of W3: rules can ship without a panel, but not without these.

## Process

- **One logical change per commit** (`CONTRIBUTING.md`). W1 and W2 are separate commits at
  minimum; the panel is reasonably a third.
- **`CHANGELOG.md` always**, under `[Unreleased]`, saying *why*. New alerts change what a
  cluster pages on after upgrade.
- **Work on a branch and open a PR.** `main` carries a ruleset requiring the CI checks and
  it has an admin bypass, so a direct push *succeeds* while reporting
  `Bypassed rule violations` and lands ungated. Let CI gate it.
- Do not weaken an existing check to get green. A newly failing check is a finding to
  report, not a threshold to move.
