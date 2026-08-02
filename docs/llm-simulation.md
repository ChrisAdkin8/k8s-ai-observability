# LLM serving simulation

This rig runs two **simulated vLLM inference servers** alongside the simulated GPUs. They
emit the real vLLM metric names, types and histogram buckets — but no model is loaded and
no inference happens. The point is to build and validate LLM dashboards, SLOs and alerts
cheaply, then point them at a real vLLM deployment unchanged.

> **Fidelity caveat.** The *shapes* are real: metric names, metric types, label sets and
> bucket boundaries all match vLLM, so queries transfer. The *values* are synthetic. This
> validates your observability pipeline and alert wiring — it tells you nothing about how a
> real model behaves under load, and a threshold tuned here is not a threshold you should
> ship.

## Quick look

```sh
./scripts/grafana.sh eks        # or gke — opens the GPU and LLM boards together
```

→ **<http://localhost:3000/d/llm-sim-overview>** — *LLM Simulation — vLLM Serving Overview*

The first panel is the whole story: two tenants, identical code, one healthy and one
saturated, with the alert threshold drawn across them.

| | |
|--|--|
| `llm-steady` | Healthy. ~1.8 requests/sec, queue at zero, p95 first-token latency ~0.1s |
| `llm-saturated` | Overloaded on purpose. ~6 requests/sec against ~2.7 rps of capacity, so the queue fills to 160, the true wait reaches ~58s, and p95 **reports** ~78s |

`llm-saturated` keeps the `LLMHighTTFT` alert permanently firing. That is deliberate: it
means `scripts/verify.sh` can prove the alert path works without changing anything on the
cluster, exactly as `gpu-busy` keeps `GPUHighUtilization` firing on the GPU side.

## What gets deployed

| Object | Namespace | What it is |
|---|---|---|
| `llm-steady` | `llm-sim` | Healthy simulator. Holds one simulated GPU, purely so it can report which one |
| `llm-saturated` | `llm-sim` | Overloaded simulator. Runs with **no** GPU request |
| `llm-driven` | `llm-sim` | **Opt-in.** Target for `drive-llm-load.sh`; not installed by default |
| `llm-sim` Service | `llm-sim` | One ClusterIP Service in front of all simulator pods |
| `llm-sim` ServiceMonitor | `monitoring` | Tells Prometheus to scrape them every 15s |
| `llm-simulation-alerts` | `monitoring` | Recording rules + six alerts |
| `llm-sim-overview-dashboard` | `monitoring` | The Grafana board, generated from `manifests/dashboards/llm-sim-overview.json` and loaded by the sidecar |

The simulator itself is [`scripts/llm-sim.py`](../scripts/llm-sim.py) — one standard-library
Python file, no dependencies. `install.sh` loads it into ConfigMap `llm-sim-script` and runs
it on a stock `python:3.12-slim` image, so **the cluster path builds nothing and needs no
registry**, and there is no `pip install` anywhere in it.

There is also a [published image](#the-container-image) for people consuming the simulator
from *outside* this repo. It changes nothing above: the rig deliberately keeps mounting the
file, because an image-based Deployment would pin a tag and a local edit would stop reaching
the cluster silently.

## Metrics

Everything under `vllm:` is named, typed and bucketed exactly as real vLLM emits it.

A background worker advances simulated requests through arrival → queue → prefill → decode →
completion in wall-clock time, observing into counters and histograms exactly as an instrumented
server would. Serving `/metrics` is a **pure read**: a scrape never advances a counter. So
`curl`-ing the endpoint by hand and the readiness probe hitting it cannot perturb what
Prometheus sees.

| Metric | Type | Meaning |
|---|---|---|
| `vllm:num_requests_running` | gauge | Requests currently being served |
| `vllm:num_requests_waiting` | gauge | Requests queued — **this is the one to watch** |
| `vllm:kv_cache_usage_perc` | gauge | KV-cache utilisation, 0–1 |
| `vllm:prompt_tokens_total` | counter | Prefill tokens |
| `vllm:generation_tokens_total` | counter | Decode tokens |
| `vllm:prefix_cache_queries_total` | counter | Prompt tokens looked up in the prefix cache |
| `vllm:prefix_cache_hits_total` | counter | Prompt tokens found there — **plot the ratio, not either counter** |
| `vllm:time_to_first_token_seconds` | histogram | TTFT |
| `vllm:inter_token_latency_seconds` | histogram | Inter-token latency |
| `vllm:e2e_request_latency_seconds` | histogram | Whole-request latency |
| `vllm:request_queue_time_seconds` | histogram | Time spent waiting, before prefill starts |
| `vllm:request_prefill_time_seconds` | histogram | Time spent in the PREFILL phase |
| `vllm:request_decode_time_seconds` | histogram | Time spent in the DECODE phase |
| `vllm:request_inference_time_seconds` | histogram | Time spent in the RUNNING phase — prefill + decode |
| `vllm:request_success_total` | counter | Completions, by `finished_reason` |

The five **request-scoped** histograms — `e2e_request_latency`, `request_queue_time`,
`request_prefill_time`, `request_decode_time` and `request_inference_time` — share **one**
bucket list. Upstream declares a single `request_latency_buckets` and passes it to all five,
so this repo holds one `E2E_BUCKETS` constant and the weekly drift check watches it on
behalf of every one. (`time_to_first_token_seconds` and `inter_token_latency_seconds` have
their own lists, `TTFT_BUCKETS` and `TPOT_BUCKETS`.)

### The phase decomposition

The four phase histograms are the answer to "the request took 6 seconds — doing what":

```
e2e       = queue    + inference
inference = prefill  + decode
```

Both hold, and the simulator asserts both per completed request in `--selftest`, but
**they are not equally exact and that is worth knowing before you write a test over
them.** `inference` is *assigned* as `prefill + decode` — one expression, evaluated once —
so it survives an `==`. `e2e` is read off the clock as `(admit + prefill + decode) -
arrived` while the identity reassociates it as `(admit - arrived) + prefill + decode`;
those are not bit-identical in IEEE 754, so an `==` there fails on ~94% of perfectly
correct requests. Measured worst error: 5.3e-14. The selftest uses `abs_tol=1e-9`, which
is five orders of margin over that and twelve orders below what a real wiring fault moves.

**TTFT = queue time + prefill, exactly**, for the same assignment reason. On a saturated
tenant almost all of TTFT is the queue term: prefill is 0.08s against a queue wait of ~58s.

⚠️ **Plot the breakdown as MEANS, not percentiles.** The board does, and there are two
independent measured reasons:

| | |
|--|--|
| **quantiles are not additive** | steady tenant, perfect resolution: p95 queue+prefill+decode = 7.473s against p95 e2e 7.468s. The means: 5.101s against 5.101s, exactly. |
| **these buckets cannot resolve prefill** | `base_ttft_seconds` is 0.08s and the first boundary is 0.3s, so every prefill observation lands in the first bucket and `histogram_quantile` interpolates from zero across it — **3.03x** overstated on both tenants. |

A histogram mean has no bucket dependence at all (`_sum` and `_count` are exact), so it is
immune to the second, and it is additive, so it is immune to the first.

⚠️ **The second effect transfers to real vLLM.** These boundaries are upstream's, so a
real deployment with sub-300ms prefill reads exactly as high — it is not an artefact of
this rig. Do not build a prefill SLO on a p95 from these buckets, and do not "fix" it by
adding a finer low-end bucket list: the boundaries are what make a query built here
transfer unchanged. The recorded percentile is scoped to **decode** for that reason —
1.12x–1.26x across both tenants, against 3.03x for prefill and 1.71x for e2e under
saturation.

### One divergence from upstream, deliberately

⚠️ Upstream labels every `vllm:` series `model_name` **and** `engine`
(`loggers.py:468`); this repo emits `model_name` alone. That is real drift, and the weekly
check cannot see it — `check-vllm-buckets.py` compares metric *names* and bucket
*boundaries*, not label sets, so this is a known blind spot rather than an oversight.

It is left alone on purpose. Adding `engine` would change the label set of every existing
series at once, moving every `by (model_name)` aggregation's cardinality, every promtool
`exp_labels` and every dashboard legend, for no panel anyone has asked for. By this repo's
own definition it is a MAJOR-class change (`CHANGELOG.md` — "metric or recording-rule
names"), so if it is ever added it should be its own change with its own migration note.

### Which engine's names

The table above is the **V1** surface. Two of those series were spelled differently
before V1, and this repo emitted the old spellings up to and including `0.2.0`:

| v0 | V1 | Why it moved |
|--|--|--|
| `vllm:gpu_cache_usage_perc` | `vllm:kv_cache_usage_perc` | V1 dropped CPU KV-cache offload, so `gpu_` no longer distinguished anything |
| `vllm:time_per_output_token_seconds` | `vllm:inter_token_latency_seconds` | Same measurement, clearer name |

And one that is **not a rename at all**, which is the more interesting case:

| v0 | V1 | Why it is different |
|--|--|--|
| `vllm:gpu_prefix_cache_hit_rate` (gauge of a ratio) | `vllm:prefix_cache_queries_total` + `vllm:prefix_cache_hits_total` (two counters) | the *shape* changed, not the spelling |

**A panel bound to the v0 gauge cannot be repaired by substituting a name.** The
replacement is `rate(hits) / rate(queries)` — a different query, over two series that did
not exist before. That is a class of upgrade breakage the two renames above cannot
demonstrate, and it is why this one is worth having on the rig. (The `cpu_` variant is
deliberately not simulated: nothing here models CPU KV offload, and V1 dropped it
entirely.)

Nothing broke when they moved — that is the problem. A renamed metric fails
*silently*: panels go blank and alerts stop firing against a real deployment while
every test here stays green. So the simulator can emit either, or both:

```sh
python3 scripts/llm-sim.py --vllm-surface v1     # default — what a current engine emits
python3 scripts/llm-sim.py --vllm-surface v0     # the superseded spellings
python3 scripts/llm-sim.py --vllm-surface both   # both at once
```

`both` is what makes this rig useful for an **upgrade rehearsal**: point your existing
dashboard at it and every panel still bound to a v0 name is the set of panels your
engine upgrade will break. Real vLLM only ever emits one surface — `both` is a rig
affordance, not a fidelity claim. Set it per pod with `LLM_SIM_VLLM_SURFACE`.

**The bucket boundaries moved too**, and that mattered more than the names. V1 replaced
TTFT's entire tail above 10s (`15/20/30/45/60/90/120` became `20/40/80/160/640/2560`),
and the saturated tenant sits at ~58s — inside it. Same simulated latency, different
reported p95, purely from the resolution it is measured at. All three lists are now
transcribed from `vllm/v1/metrics/loggers.py` and
[drift-checked weekly](versions.md#keeping-them-honest):

```sh
python3 scripts/check-vllm-buckets.py
```

That check watches the metric **set** as well, in both directions: a `vllm:` name this
repo emits that upstream has dropped is drift and fails, while an upstream metric this
simulator does not emit is printed as a gap and passes. The gap list is long on purpose —
upstream declares around 40 series and this file emits 15 — and keeping it printed is what
stops that distance from growing back silently. See
[versions.md](versions.md#keeping-them-honest).

> **No `source` label on `vllm:*` series.** The GPU side tags its synthesised metrics
> `source="derived"`, but doing that here would break the transfer property: real vLLM emits
> no such label, so an extra one breaks exact-match joins and `group_left` against a real
> deployment. Provenance comes from the `job="llm-sim"` label Prometheus adds at scrape time.

This rig's **own** metrics are prefixed `llmsim_` and are safe to label freely:

| Metric | Why it exists |
|---|---|
| `llmsim_profile_generation` | Ticks up each time a simulator reloads its load profile. **This is how you confirm a load change actually landed** |
| `llmsim_requests_rejected_total` | Arrivals refused because the in-flight cap was hit. Without this, a saturated server's queue metric just silently plateaus |
| `llmsim_gpu_binding_info` | Which simulated GPU a pod holds, as the device plugin's `device_id` — not a DCGM UUID, see below. Only present for pods that requested one |
| `llmsim_capacity_rps` | Sustainable throughput implied by the current profile |

Recording rules add `llm:ttft:p50_5m` / `p95_5m` / `p99_5m`, `llm:tpot:p95_5m`,
`llm:decode:p95_5m`, `llm:tokens:generation_rate5m`, `llm:tokens:prompt_rate5m` and
`llm:prefix_cache:hit_ratio5m` — all aggregated **`by (model_name)`** so the two tenants
never merge into one meaningless number.

The phase breakdown adds four **means**: `llm:queue:mean5m`, `llm:prefill:mean5m`,
`llm:decode:mean5m` and `llm:e2e:mean5m`. Four rather than three — `llm:e2e:mean5m` is the
right-hand side of *does the breakdown add up*, which is asserted as a permanent promtool
test and again on a live cluster by `verify.sh` L8. It has to be a **rule** and not an
inlined `rate(_sum)/rate(_count)`: recorded series here carry `source: simulated` and raw
`vllm:` series do not, so an inlined right-hand side matches nothing and the obvious repair
(`on(model_name)`) drops `source` from the result — right arithmetic, wrong labels, reading
as an arithmetic bug.

Their denominators clamp at `1e-9` rather than at `1`, exactly as
`llm:prefix_cache:hit_ratio5m` does. A low-traffic tenant can genuinely complete fewer than
one request per second, and flooring at 1 would silently under-report the mean of precisely
the deployments least likely to notice. An idle tenant reads `0`, not `NaN`.

Four more record the TTFT **service-level indicator**, `llm:ttft:slo_ratio5m` / `30m` /
`1h` / `6h`, and clamp the same way for the same reason. They are a ratio at a bucket
boundary rather than a percentile, which is the whole of their design — see
[The TTFT error budget](#the-ttft-error-budget).

`llm:tokens_per_watt:5m` is the exception on both counts. It is **cluster-aggregate** — a bare
`sum()` over every tenant, with no `by (model_name)` — and it is *derived from derived*: the
wattage in its denominator does not exist at source either, being synthesised from GPU
utilisation by the rules in
[observability.md](observability.md#derived-temperature--power). It also correlates two
independently driven signals, so treat it as a demonstration of the cross-domain query pattern
rather than as evidence of causation (see
[GPU and LLM load are independent](#gpu-and-llm-load-are-independent)).

Check any of it yourself:

```sh
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=llm:ttft:p95_5m' | jq .
```

## Driving load

The **load profile** is a small JSON document in a ConfigMap. Each simulator polls its own
profile every 10 seconds and applies changes **without restarting**, so counters and
histograms stay continuous — no artificial `rate()` gap every time you change load.

```sh
kubectl apply -f manifests/llm/extras/    # once — adds the llm-driven simulator
./scripts/drive-llm-load.sh ramp          # 0.4 -> 6.0 -> 0.4 rps staircase
./scripts/drive-llm-load.sh saturation    # hold above capacity until the alert trips
./scripts/drive-llm-load.sh burst         # quiet/spike train
./scripts/drive-llm-load.sh ramp --with-gpu   # move the GPU metrics on a matching curve
```

Or with Task: `task eks:llm-load -- ramp`.

> **Give it a minute.** Kubernetes takes up to ~60s to propagate a ConfigMap change into a
> running pod. Watch the **Profile generation** panel tick up to confirm it landed. If it
> never moves, the profile is mounted with `subPath` — kubelet never updates those.

The driver deliberately refuses to touch `llm-steady` and `llm-saturated`, whose fixed
states `verify.sh` asserts against.

### The profile, and why the numbers are what they are

```json
{
  "model_name": "sim-llama-3-8b-steady",
  "arrival_rate_rps": 1.8,
  "max_concurrency": 16,
  "max_in_flight": 176,
  "prompt_tokens":     {"mean": 512, "stddev": 128},
  "generation_tokens": {"mean": 256, "stddev": 64},
  "base_ttft_seconds": 0.08,
  "base_itl_seconds": 0.015,
  "kv_cache_tokens_capacity": 32768,
  "prefix_cache_hit_rate": 0.35,
  "finish_reasons": {"stop": 0.90, "length": 0.09, "abort": 0.01},
  "seed": null
}
```

One number decides whether a tenant is healthy or overloaded: **`arrival_rate_rps` relative
to capacity.**

Capacity is computed from the **congested** inter-token latency. A server at capacity is by
definition running a full batch, and decode slows as the batch fills
(`itl = base_itl × (1 + CONGESTION_AT_FULL_LOAD × load)`), so the base figure overstates
throughput by 1.5×:

```
itl at full batch = base_itl x (1 + 0.5)
                  = 0.015 x 1.5                        = 0.0225 s
service time      = base_ttft + generation_tokens.mean x itl_full
                  = 0.08 + 256 x 0.0225                = 5.84 s
capacity          = max_concurrency / service time
                  = 16 / 5.84                          = 2.74 requests/sec
```

- **`llm-steady` at 1.8 rps** is 0.66× capacity. The queue stays at zero, so TTFT stays
  near `base_ttft_seconds`.
- **`llm-saturated` at 6.0 rps** is 2.19× capacity. The queue grows until it hits
  `max_in_flight`, then plateaus at `176 − 16 = 160` waiting requests. TTFT plateaus with
  it at roughly `160 / 2.74 ≈ 58s` — reported TTFT is the *measured* queue wait plus
  prefill, so Little's Law sets it. Arrivals beyond the cap become
  `llmsim_requests_rejected_total` rather than a queue that grows without bound.

> **Tune against `llmsim_capacity_rps`, not against the base-latency figure.** An earlier
> revision computed capacity from the uncongested `base_itl` and reported 4.08 rps. Steady
> was set to 2.4 rps believing that was 0.59× capacity, when it was really 0.88×. Worse, the
> queue-wait term was also added to `finish_at`, so queued requests held a concurrency slot
> for the duration of their own wait — positive feedback that drove *both* tenants to the
> 160 plateau and made them indistinguishable on the dashboard. Queue wait is now measured
> from the clock and charged only to the reported TTFT, never to slot occupancy.

Three numbers interlock, and changing one means re-checking the others:

| Quantity | Value | Why |
|---|---|---|
| `LLMHighTTFT` threshold | p95 > 2s for 2m | Above steady (~0.1s), below saturated (~78s as reported) |
| Steady p95 | ~0.1s | Must stay **below** the threshold or the healthy tenant alerts |
| Saturated p95 | ~78s | Must stay **above** it, and under `verify.sh`'s 120s sanity bound. The underlying wait is `160 / 2.74 rps` ≈ 58s by Little's Law, so it moves if you change `max_in_flight` or capacity — but the *reported* figure is quantised by V1's `(40, 80]` bucket to `40 + 40×0.95 = 78`, and will read 78 for any true latency inside that band |

### Why an observed steady p95 runs higher than ~0.1s

`~0.1s` is what the arithmetic above **models**: with the queue empty, TTFT is
`base_ttft_seconds` plus jitter. A live capture routinely reads several times that — the
screenshot in the README shows **~480 ms** — and the reason is in the same arithmetic
rather than in a fault.

By Little's Law the steady tenant's mean concurrency is `arrival_rate_rps × service time`
= `1.8 × 5.84` = **10.5, against a `max_concurrency` of 16**. That is a batch two-thirds
full *on average*, and arrivals are Poisson (`_interarrival()` draws from
`rng.expovariate`), so it reaches 16 regularly. Every arrival that lands while it is full
waits, and reported TTFT is measured queue wait plus prefill.

⚠️ **The `waiting` gauge can read flat zero throughout while that happens, and the two are
not in contradiction.** `vllm:num_requests_waiting` is a **gauge**, sampled once per 15s
scrape; `vllm:time_to_first_token_seconds` is a **histogram**, which observes *every*
request. A queue that forms and drains between two scrapes is invisible to the first and
fully recorded by the second. The gauge answers "is there a backlog right now"; the
histogram answers "what did requests actually experience". When they disagree, the
histogram is the one describing your users.

None of this moves the demonstration, which is the point of the two tenants rather than the
absolute figures: ~480 ms is still more than an order of magnitude below the 2s threshold,
and the saturated tenant sits two orders above it.

A malformed profile is never fatal: the simulator logs the problem, keeps the last good
profile, and increments `llmsim_profile_reload_errors_total`.

### Prefix caching, and why it changes no latency here

Two more profile fields, both optional:

| Field | Default | |
|---|---|---|
| `prefix_cache_hit_rate` | `0.0` | fraction of *cacheable prompt tokens* served from the prefix cache |
| `kv_block_tokens` | `16` | vLLM's KV block size — hits are quantised to whole blocks, so a partial trailing block is never a hit |

Counted in **tokens, not requests**, because that is what upstream counts and because a
per-request counter would give a ratio that does not respond to prompt length. The panel
you build here would then behave differently against a real deployment, which defeats the
purpose of having the metric at all.

> **⚠️ A cache hit does not shorten TTFT in this simulator, and that is deliberate.**
> Prefill here is *flat* — `base_ttft_seconds × jitter` — not token-proportional, so there
> is no per-token work a cached block could remove. Any speedup would have to be invented.
> Making prefill token-proportional is a real modelling change: it re-derives the service
> time, the 2.74 rps capacity figure, both shipped profiles, the 2s alert threshold,
> `verify.sh`'s L3b bound and every expected value in `tests/rules/llm-rules_test.yaml`.
> `--selftest` asserts the TTFT histogram is **identical** across hit rates, so nobody can
> change one without noticing the other.
>
> An honest zero beats a fabricated speedup, and nothing is lost: what a real deployment
> plots is the **ratio**, and the ratio transfers whether or not the latency here responds
> to it.

**⚠️ The shipped rates are chosen, not derived.** 0.35 on `llm-steady` and 0.15 on
`llm-saturated` exist so the panel draws two distinguishable lines, with the lower one on
the saturated tenant because a server under eviction pressure reuses less. Unlike
`capacity_rps`, no arithmetic produces those numbers — which is exactly why they are
labelled as invented in `manifests/llm/10-profiles.yaml` rather than left to look modelled.

`0.0` means a cache that is **consulted and always misses**, not one that is switched off:
queries still advance, hits stay at zero, and both series are still emitted. An absent
series and a zero one are different things to a panel.

## Alerts

| Alert | Fires when | Exercised by default? |
|---|---|---|
| `LLMHighTTFT` | p95 TTFT > 2s for 2m | **Yes** — `llm-saturated` keeps it firing |
| `LLMQueueBacklog` | > 50 requests queued for 5m | **Yes** — same |
| `LLMKVCacheSaturated` | KV cache > 90% for 5m | No — lower `kv_cache_tokens_capacity` in a profile to test |
| `LLMMetricsAbsent` | No serving metrics for 5m | No — `kubectl -n llm-sim scale deploy/llm-steady --replicas=0` to test |
| `LLMTTFTErrorBudgetFastBurn` | > 14.4x burn on the 1h **and** 5m windows | **Yes** — `llm-saturated` burns at ~100x |
| `LLMTTFTErrorBudgetSlowBurn` | > 6x burn on the 6h **and** 30m windows | No — the 6h window never fills on a rig that lives minutes |

### The TTFT error budget

The two burn alerts sit over an objective — **99% of requests reach a first token within
2.5s** — recorded as `llm:ttft:slo_ratio5m` / `30m` / `1h` / `6h`. It is deliberately a
**ratio at a bucket boundary rather than a percentile**, which is why it carries none of
the bucket caveats the rest of this page is full of: `histogram_quantile` interpolates
*inside* a bucket, and a ratio taken *at* a boundary does not interpolate at all.

⚠️ **2.5 is a real member of `TTFT_BUCKETS`, and 2.0 is not** — the list steps
`… 1.0, 2.5, 5.0 …`. `le="2"` matches nothing and both alerts then stay green forever. The
same reason the objective is not set at the 2s `LLMHighTTFT` threshold, which is an
interpolated percentile and a different instrument.

The full argument, and the four limits that come with it — what to do when the threshold
you want is not a boundary, the condition under which "exact" holds, what a stall does to
a latency objective, and the unexercised 6h window — is on the
[catalog page](../manifests/dashboards/llm-sim-overview.grafana-com.md).

Watch one reach `firing`:

```sh
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=ALERTS{alertname="LLMHighTTFT",alertstate="firing"}' | jq .
```

## GPU and LLM load are independent

The dashboard has a *tokens per GPU watt* panel, and it is honest about what it is:

**Nothing in this rig makes GPU utilisation follow LLM load.** GPU metrics come from the
`run.ai/simulated-gpu-utilization` annotations on the `gpu-*` workloads; LLM metrics come
from the simulator profiles. They are driven separately and cannot be coupled without
restarting a pod, which would reset the counters the LLM dashboard depends on.

So that panel demonstrates the *query pattern* — joining two metric families, one of which
(`DCGM_FI_DEV_POWER_USAGE`) is itself synthesised by recording rules — not a causal
relationship. Use `drive-llm-load.sh ramp --with-gpu` if you want both to move at once for
a demo; that just runs both drivers together.

`llmsim_gpu_binding_info` is the exception worth knowing about. `llm-steady` requests one
simulated GPU, and the fake GPU operator's device plugin injects
`MOCK_NVIDIA_VISIBLE_DEVICES` into the container, which the simulator republishes as
`device_id`. That is the same attribution technique that becomes genuinely useful against a
real `dcgm-exporter`. Nothing on the dashboard depends on it, so if no simulated GPU is
free, the simulator just runs unbound.

**It is deliberately not called `UUID`.** Chart 0.0.59 injects the device plugin's own
per-allocation id — a bare random v4, minted fresh every time the pod is rescheduled —
while the exporter labels that same GPU with a deterministic `GPU-`-prefixed id read from
the topology ConfigMap. Different code paths, so they never match and `on (UUID)` returns
nothing at all. The join that works goes through the pod, because the exporter labels each
allocated GPU with its consumer (renamed to `exported_*` by Prometheus, since the scrape
target's own `namespace`/`pod` labels win the collision):

```promql
llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu)
  label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""},
    "namespace", "$1", "exported_namespace", "(.*)"),
    "pod", "$1", "exported_pod", "(.*)")
```

That is what `verify.sh` L4b asserts and what the dashboard's attribution table queries.

## Running the simulator locally

No cluster required:

```sh
task selftest                                # validate the Prometheus output
python3 scripts/llm-sim.py --print           # warm up, print one scrape, exit
python3 scripts/llm-sim.py --profile p.json  # serve on :9401
```

`--selftest` checks the things that are easy to get subtly wrong and hard to notice: bucket
monotonicity, `+Inf` matching `_count`, one `# TYPE` per family, that no `vllm:*` series
carries a `source` label, that serving `/metrics` observes nothing, and that both phase
identities hold on every completed request. It drives an injected clock, so it is
deterministic and finishes instantly.

## The container image

For pointing your **own** dashboards, recording rules and alert expressions at a realistic
vLLM metric surface, without cloning this repo:

```sh
docker run --rm -p 9401:9401 ghcr.io/chrisadkin8/vllm-metrics-sim:latest
```

Verified: **no `--profile` is needed**. The simulator falls back to `DEFAULT_PROFILE` — a
self-consistent steady tenant at 1.8 rps against a modelled capacity of 2.74 rps — and
serves on `:9401` immediately. Mount a profile and pass `--profile /path/to/p.json` to
change the tenant; the file is polled, so edits apply without a restart.

| | |
|--|--|
| tags | `:<release tag>` (e.g. `:v0.5.0`) and `:latest`, published on every release tag |
| platforms | `linux/amd64` and `linux/arm64` |
| provenance | `docker inspect` reads back `org.opencontainers.image.revision` / `.version` / `.source` — an image whose version cannot be tied to a commit is one nobody can debug |
| built from | [`scripts/llm-sim.py`](../scripts/llm-sim.py) directly, never a committed second copy |

### ⚠️ What this image is NOT

**It is not how this rig runs the simulator, and it must not become that.**
`scripts/install.sh` still builds the `llm-sim-script` ConfigMap from the file, and the
compose stack still mounts it. Three reasons, and the third is the one that bites:

- `task selftest` and `--print` run the file directly with no build step, which is what
  makes the simulator editable in seconds;
- the compose path mounts the same file, so an image would fork the two;
- an image-based Deployment pins a **tag**, so a local edit to `llm-sim.py` would stop
  reaching the cluster — *silently*, since the pod would still be `Running`. That is
  exactly the failure the checksum annotation in `install.sh` was added to fix,
  reintroduced one layer up.

The image is for **external consumers**. Someone will otherwise helpfully "simplify" the
Deployment onto it.

**`pip install` is still a non-goal and was not reversed.** The image ships the same
stdlib-only file; that constraint is what makes the Dockerfile a `FROM` and a `COPY`. The
two rules are habitually stated in one breath and only one of them moved.

### ⚠️ The port override is `LLM_SIM_LISTEN_PORT`, not `LLM_SIM_PORT`

```sh
docker run --rm -p 9999:9999 -e LLM_SIM_LISTEN_PORT=9999 \
  ghcr.io/chrisadkin8/vllm-metrics-sim:latest
```

Verified against the built image: `LLM_SIM_LISTEN_PORT=9999` serves on 9999, and
`LLM_SIM_PORT=tcp://10.0.0.1:9401` is **silently ignored** — it does not crash, and it does
not move the listener.

Two things follow, and both belong here because this is where someone meets the name:

- **someone will try `LLM_SIM_PORT`**, find it ignored, and conclude the image has no port
  override at all;
- **do not "simplify" it back to the obvious name — the obvious name is the bug.** kubelet
  injects a Docker-link-compatible `<SVCNAME>_PORT` environment variable for every Service
  in the pod's namespace, so a Service called `llm-sim` sets
  `LLM_SIM_PORT=tcp://<clusterIP>:9401` into every simulator pod. Reading *that* name meant
  `int()` received a URL and every pod died at startup — a `CrashLoopBackOff` whose only
  visible symptom was an LLM dashboard with no data. The full account is in
  `default_port()` in the script.

## Security note

The simulator runs a script mounted from a ConfigMap, so **anyone who can write ConfigMaps
in `llm-sim` can run code in that pod**. The pods themselves run non-root with a read-only
root filesystem and all capabilities dropped. The same reasoning is applied to Grafana's
anonymous access in `helm/kube-prometheus-stack/values.yaml`.

⚠️ **The justification for that trade has narrowed, and this note says so rather than
letting it read as settled.** It used to be "the alternative is a container image and a
pipeline to publish it", with the image ruled out of scope. A published image is now **in
scope** (`CONTRIBUTING.md`), so the alternative is no longer hypothetical.

The trade is still taken, on its own merits rather than by default: mounting the file is
what lets `--selftest` and `--print` run it with no build step, what keeps the compose path
reading the same bytes the cluster does, and what makes an edit reach a pod in seconds
instead of a tag bump. An image-based Deployment pins a **tag**, so a local edit would stop
reaching the cluster silently — the pod would still be Running. That is the failure the
checksum annotation in `install.sh` was added to prevent, and it would come back one layer
up.

So the expected shape is both: the ConfigMap mount for the rig, an image for anyone
consuming the simulator from outside it. If that ever changes, this paragraph is what has
to change with it.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Panels empty | Recording rules only fill in going forward. Wait ~2 min after install |
| `LLMHighTTFT` not firing | `llm-saturated` not Running, or its `arrival_rate_rps` dropped below 2.74 |
| Load change has no effect | Wait 60s; then check **Profile generation**. If it never moves, the profile is mounted with `subPath` |
| Steady tenant alerting | Its `arrival_rate_rps` is above capacity — recompute with the formula above |
| `llm-steady` stuck Pending | No simulated GPU free. Remove the `nvidia.com/gpu` line from its manifest; it runs fine unbound |
| Only one tenant on the board | The two profiles share a `model_name`. They must be distinct — `install.sh` asserts this |
