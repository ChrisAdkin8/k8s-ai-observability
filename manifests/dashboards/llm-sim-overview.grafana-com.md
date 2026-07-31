# LLM Simulation — vLLM Serving Overview

Time to first token, inter-token latency, throughput, queue depth and KV-cache usage for
vLLM — every panel broken out `by (model_name)`, so a saturated tenant is never averaged
into a healthy one.

Built for a **simulated** vLLM fleet — it ships with
[k8s-ai-observability](https://github.com/ChrisAdkin8/k8s-ai-observability), a rig that
stands up GPU and LLM observability with no GPU and no model weights — but the queries are
plain vLLM PromQL against the **V1** metric surface. Build the board and its alerts here,
point them at a real deployment afterwards.

![Six panels comparing a healthy tenant and an overloaded one side by
side](https://raw.githubusercontent.com/ChrisAdkin8/k8s-ai-observability/main/docs/llm-dashboard.png)

The first panel is the whole design: two tenants either side of the 2s alert threshold,
identical code, one degraded. A board that summed them would show neither.

> The screenshot pre-dates the V1 histogram-bucket sync, so the saturated tenant reads
> `1.20 mins` where it now reads ~78s. Same simulated latency — different bucket
> resolution to report it at. That difference is itself worth knowing about, and it is
> covered under [inter-token latency](#read-the-inter-token-latency-panel-carefully) below.

## Panels

| Panel | Query | Notes |
|--|--|--|
| Time to first token — p95 | `llm:ttft:p95_5m` | red threshold line at 2s |
| Inter-token latency — p95 | `llm:tpot:p95_5m` | read the caveat below before setting an SLO |
| Requests running vs waiting | `vllm:num_requests_running`, `vllm:num_requests_waiting` | **repeats per model**, waiting on the right axis |
| Token throughput | `llm:tokens:generation_rate5m`, `llm:tokens:prompt_rate5m` | prefill and decode, separately |
| Generated tokens per GPU watt | `llm:tokens_per_watt:5m` | cross-domain, needs DCGM — see below |
| KV cache utilisation | `vllm:kv_cache_usage_perc` | fraction 0–1, axis pinned |
| Request outcomes by finish reason | `rate(vllm:request_success_total[5m])`, `rate(llmsim_requests_rejected_total[5m])` | rejections are counted apart from successes |
| Profile generation | `llmsim_profile_generation` | simulator-only |
| Simulated GPU attribution | `llmsim_gpu_binding_info` joined to `DCGM_FI_DEV_GPU_UTIL` | simulator-only |

**Running vs waiting repeats per model rather than sharing one panel**, because the two
tenants operate at different scales: a saturated tenant queues ~160 against a running batch
of 16, and on one shared axis the running series flattens to nothing. Waiting is on the
right axis for the same reason. A healthy tenant should sit near zero waiting; a plateau
means arrival has exceeded capacity.

## What it needs — read this before importing

Unlike a pure `vllm:*` board, **four panels read recording rules rather than raw
series**, and two more read simulator-only metrics. Import without them and you get an
empty board and no explanation.

| Tier | Series | Against real vLLM |
|--|--|--|
| vLLM V1 | `vllm:num_requests_running`, `_waiting`, `kv_cache_usage_perc`, `prompt_tokens_total`, `generation_tokens_total`, `request_success_total`, and the `time_to_first_token_seconds` / `inter_token_latency_seconds` histograms | emitted directly — nothing to do |
| recording rules | `llm:ttft:p95_5m`, `llm:tpot:p95_5m`, `llm:tokens:generation_rate5m`, `llm:tokens:prompt_rate5m`, `llm:tokens_per_watt:5m` | **you must apply these** (below), or inline the expressions into the panels |
| simulator-only | `llmsim_profile_generation`, `llmsim_requests_rejected_total`, `llmsim_gpu_binding_info` | never emitted by vLLM — those panels stay blank, see [trimming](#trimming-it-for-a-real-deployment) |

### The recording rules

Quantiles are recorded once rather than repeated in every panel and alert, so a dashboard
and the alert firing beside it can never disagree about what "p95 TTFT" means:

```yaml
- record: llm:ttft:p95_5m
  expr: histogram_quantile(0.95, sum by (model_name, le) (rate(vllm:time_to_first_token_seconds_bucket[5m])))
- record: llm:tpot:p95_5m
  expr: histogram_quantile(0.95, sum by (model_name, le) (rate(vllm:inter_token_latency_seconds_bucket[5m])))
- record: llm:tokens:generation_rate5m
  expr: sum by (model_name) (rate(vllm:generation_tokens_total[5m]))
- record: llm:tokens:prompt_rate5m
  expr: sum by (model_name) (rate(vllm:prompt_tokens_total[5m]))
```

**⚠️ Aggregate `by (model_name)`, not globally.** A global quantile merges every tenant
into one number that describes none of them, and hides the degraded one — which is the
tenant the alert exists to catch. The `p50` and `p99` variants are in the source file if
you want them.

Two traps worth inheriting, both encoded above:

- **Do not point `llm:tpot:p95_5m` at `vllm:request_time_per_output_token_seconds`**,
  which current vLLM also exposes. That is a per-request mean, not a per-token histogram,
  and the quantile means something different.
- **The rule name says `tpot` while the series says `inter_token_latency`** on purpose.
  V1 renamed the metric; TPOT is what people call the measurement, and renaming the
  recorded series would break every dashboard and alert built on it for no gain.

Ready to apply as a `PrometheusRule`, reasoning included:
[`manifests/alerts/llm-prometheusrule.yaml`](https://github.com/ChrisAdkin8/k8s-ai-observability/blob/main/manifests/alerts/llm-prometheusrule.yaml).

Because these are recording rules, the panels fill in **going forward** from when the
rules were applied, and `rate()` over `[5m]` under-reads until the window fills — expect a
few minutes of climb on a fresh install rather than a jump to the true value.

### ⚠️ V1 metric names — check yours before blaming the board

Two series were spelled differently before the V1 engine, and this board uses the new
names:

| Older vLLM | V1, used here |
|--|--|
| `vllm:gpu_cache_usage_perc` | `vllm:kv_cache_usage_perc` |
| `vllm:time_per_output_token_seconds` | `vllm:inter_token_latency_seconds` |

Nothing errors when this is wrong — that is the problem. A renamed metric fails
*silently*: the panel goes blank and the alert stops firing while everything else stays
green. On an older engine, rewrite those two names in the JSON.

## Read the inter-token latency panel carefully

**The p95 there is dominated by bucket resolution, not by load**, and this is one caveat
that transfers to real hardware rather than being a simulation artifact — real vLLM uses
the same bucket boundaries.

Worked through on the rig: a full batch models ITL at 22.5 ms, which falls inside the wide
`(25ms, 50ms]` bucket, so `histogram_quantile` interpolates and reports ~43 ms. The panel
is not wrong and neither is the histogram; the resolution simply is not there. **Do not
set an ITL SLO from a number that lands in a wide bucket** — check which bucket your
operating point falls into first.

The same effect explains the screenshot discrepancy above. V1 replaced TTFT's entire tail
above 10s (`15/20/30/45/60/90/120` became `20/40/80/160/640/2560`), and the saturated
tenant sits at ~58s — inside the part that moved.

## The cross-domain panel

`llm:tokens_per_watt:5m` is `sum(rate(vllm:generation_tokens_total[5m])) /
sum(DCGM_FI_DEV_POWER_USAGE)` — cluster-aggregate, and it needs **DCGM metrics in the same
Prometheus**. Without a GPU exporter the panel is blank; that is expected, not a fault.

**⚠️ On this rig the two signals are driven independently**: GPU load comes from pod
annotations, LLM load from simulator profiles, and nothing makes one follow the other. The
panel demonstrates the query pattern and the wiring across two metric families — it does
not show causation. Against real hardware it becomes meaningful, provided you are honest
about which GPUs are actually serving the model.

## Variables

| Variable | Query | |
|--|--|--|
| Prometheus datasource | — | prompted for at import |
| `Model` | `label_values(vllm:num_requests_running, model_name)` | multi-select, defaults to All |

The `model` query works against real vLLM unchanged. `$model` filters every panel and
drives the repeat on running-vs-waiting, so a fleet of ten models yields ten of those
panels at two per row — narrow the selection if that is more than you want.

## Import

Grafana **Dashboards → New → Import**, by id or by uploading the JSON. It prompts for a
Prometheus datasource; the model variable populates itself.

| | |
|--|--|
| Grafana | 10.0 or newer (`schemaVersion` 39) |
| Datasource | Prometheus, prompted for on import |
| Panel plugins | `timeseries`, `stat`, `table` — all core |
| Default window | last 15 minutes, refresh 30s |

**The 15-minute default suits a rig that has been up for minutes.** On a 30-minute window
a short history is squeezed into the right-hand third with the rest of the canvas blank,
which reads as "the panels never populated" when the data was there all along. With real
retention behind you, widen it.

## Trimming it for a real deployment

Two panels and one series are rig-specific. Delete them and the rest stands on its own:

- **Profile generation** — `llmsim_profile_generation` ticks when a simulator reloads its
  load profile. No analogue in vLLM.
- **Simulated GPU attribution** — joins simulator pods to the GPUs they hold. The join
  itself is worth stealing even though the panel is not: it goes `on (namespace, pod)`,
  never `on (UUID)`, because the device plugin's allocation id and DCGM's UUID come from
  different code paths and never match. An `on (UUID)` join returns nothing, silently and
  forever.
- **The rejected series** on request outcomes — `llmsim_requests_rejected_total` counts
  arrivals refused at the in-flight cap. A rejected request never ran, so it is
  deliberately not a vLLM "success" of any `finished_reason`; real vLLM leaves that
  accounting to whatever sits in front of it.

Also reconsider the **2s TTFT threshold line**. It is placed between this rig's two
tenants (~0.1s and ~60s) to make the demonstration legible. It is not an SLO, and a
threshold tuned against synthetic latency is not one to ship.

## Matching alerts

The rules that ship alongside the board, [unit-tested with
`promtool`](https://github.com/ChrisAdkin8/k8s-ai-observability/tree/main/tests) on both
sides of every threshold:

| Alert | Expression | For |
|--|--|--|
| `LLMHighTTFT` | `llm:ttft:p95_5m > 2` | 2m |
| `LLMQueueBacklog` | `vllm:num_requests_waiting > 50` | 5m |
| `LLMKVCacheSaturated` | `vllm:kv_cache_usage_perc > 0.9` | 5m |
| `LLMMetricsAbsent` | `absent(vllm:num_requests_running)` | 5m |

`vllm:kv_cache_usage_perc` is a **fraction (0–1)**, so `> 0.9` is correct and `> 90` can
never fire — an easy one to get wrong, and it fails by staying silent forever.

## Source

Original to [k8s-ai-observability](https://github.com/ChrisAdkin8/k8s-ai-observability),
not derived from another catalog board. MIT licensed.

The repo runs two simulated tenants — one healthy, one deliberately overloaded — from a
dependency-free Python file that emits real vLLM names, types and histogram buckets, with
the buckets drift-checked weekly against `vllm/v1/metrics/loggers.py`. It can also emit
the superseded v0 names alongside the V1 ones, which turns it into an upgrade rehearsal:
point your existing dashboard at it and every panel still bound to an old name is a panel
your engine upgrade will break. On kind, EKS, GKE, or `docker compose up` with no
Kubernetes at all.
