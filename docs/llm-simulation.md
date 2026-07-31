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
| `llm-saturated` | Overloaded on purpose. ~6 requests/sec against ~2.7 rps of capacity, so the queue fills to 160 and p95 climbs to ~60s |

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
| `llm-simulation-alerts` | `monitoring` | Recording rules + four alerts |
| `llm-sim-overview-dashboard` | `monitoring` | The Grafana board, generated from `manifests/dashboards/llm-sim-overview.json` and loaded by the sidecar |

The simulator itself is [`scripts/llm-sim.py`](../scripts/llm-sim.py) — one standard-library
Python file, no dependencies, no image to build. `install.sh` loads it into ConfigMap
`llm-sim-script` and runs it on a stock `python:3.12-slim` image, so there is no registry and
no `pip install` anywhere in the path.

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
| `vllm:time_to_first_token_seconds` | histogram | TTFT |
| `vllm:inter_token_latency_seconds` | histogram | Inter-token latency |
| `vllm:e2e_request_latency_seconds` | histogram | Whole-request latency |
| `vllm:request_success_total` | counter | Completions, by `finished_reason` |

### Which engine's names

The table above is the **V1** surface. Two of those series were spelled differently
before V1, and this repo emitted the old spellings up to and including `0.2.0`:

| v0 | V1 | Why it moved |
|--|--|--|
| `vllm:gpu_cache_usage_perc` | `vllm:kv_cache_usage_perc` | V1 dropped CPU KV-cache offload, so `gpu_` no longer distinguished anything |
| `vllm:time_per_output_token_seconds` | `vllm:inter_token_latency_seconds` | Same measurement, clearer name |

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
`llm:tokens:generation_rate5m` and `llm:tokens:prompt_rate5m` — all aggregated
**`by (model_name)`** so the two tenants never merge into one meaningless number.

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
| `LLMHighTTFT` threshold | p95 > 2s for 2m | Above steady (~0.1s), below saturated (~60s) |
| Steady p95 | ~0.1s | Must stay **below** the threshold or the healthy tenant alerts |
| Saturated p95 | ~78s | Must stay **above** it, and under `verify.sh`'s 120s sanity bound. The underlying wait is `160 / 2.74 rps` ≈ 58s by Little's Law, so it moves if you change `max_in_flight` or capacity — but the *reported* figure is quantised by V1's `(40, 80]` bucket to `40 + 40×0.95 = 78`, and will read 78 for any true latency inside that band |

A malformed profile is never fatal: the simulator logs the problem, keeps the last good
profile, and increments `llmsim_profile_reload_errors_total`.

## Alerts

| Alert | Fires when | Exercised by default? |
|---|---|---|
| `LLMHighTTFT` | p95 TTFT > 2s for 2m | **Yes** — `llm-saturated` keeps it firing |
| `LLMQueueBacklog` | > 50 requests queued for 5m | **Yes** — same |
| `LLMKVCacheSaturated` | KV cache > 90% for 5m | No — lower `kv_cache_tokens_capacity` in a profile to test |
| `LLMMetricsAbsent` | No serving metrics for 5m | No — `kubectl -n llm-sim scale deploy/llm-steady --replicas=0` to test |

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
carries a `source` label, and that serving `/metrics` observes nothing. It drives an
injected clock, so it is deterministic and finishes instantly.

## Security note

The simulator runs a script mounted from a ConfigMap, so **anyone who can write ConfigMaps
in `llm-sim` can run code in that pod**. That is an accepted trade for keeping this repo
build-free and registry-free — the alternative is a container image and a pipeline to
publish it. The pods themselves run non-root with a read-only root filesystem and all
capabilities dropped. The same reasoning is applied to Grafana's anonymous access in
`helm/kube-prometheus-stack/values.yaml`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Panels empty | Recording rules only fill in going forward. Wait ~2 min after install |
| `LLMHighTTFT` not firing | `llm-saturated` not Running, or its `arrival_rate_rps` dropped below 2.74 |
| Load change has no effect | Wait 60s; then check **Profile generation**. If it never moves, the profile is mounted with `subPath` |
| Steady tenant alerting | Its `arrival_rate_rps` is above capacity — recompute with the formula above |
| `llm-steady` stuck Pending | No simulated GPU free. Remove the `nvidia.com/gpu` line from its manifest; it runs fine unbound |
| Only one tenant on the board | The two profiles share a `model_name`. They must be distinct — `install.sh` asserts this |
