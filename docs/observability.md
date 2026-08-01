# GPU observability & driving load

> **Looking for the LLM serving metrics?** They are a separate stack with their own
> dashboard, load driver and alerts — see **[llm-simulation.md](llm-simulation.md)**.
> This page covers the simulated **GPU** metrics only.

## Where the metrics come from

Two independent producers feeding one Prometheus. Neither talks to real hardware or a real
model, and some of what the boards plot is not emitted by either producer. Knowing which is
which is the difference between reading a panel and trusting it.

Three provenance classes, and every series on the boards is one of them:

| Class | Which series | Where it comes from |
|--|--|--|
| exporter | `DCGM_FI_DEV_GPU_UTIL`, `_FB_USED`, `_FB_FREE` | the fake `dcgm-exporter` on `:9400`. Exactly three series at the pinned chart version — no temperature, no power, no SM clock |
| simulator | all `vllm:*`, plus `llmsim_*` | [`scripts/llm-sim.py`](../scripts/llm-sim.py) on `:9401`, emitting real vLLM names, types and histogram buckets. `llmsim_*` is namespaced to mark it simulation-only |
| derived | `DCGM_FI_DEV_GPU_TEMP`, `_POWER_USAGE`, everything `llm:*` | recording rules in [`manifests/alerts/`](../manifests/alerts/). Nothing emits these; Prometheus computes them |

Both producers are scraped every 15s. **Keep that ≤30s:** `rate()` over a 1m window needs two
samples inside it, and at 60s you get one — which looks like a dead counter rather than a
scrape-interval choice.

> **The `source` label tells you what you're looking at.** Synthesised GPU series carry
> `source="derived"`; LLM recording rules carry `source="simulated"`; the raw `vllm:*`
> series carry no `source` label at all. That is deliberate: real vLLM emits none
> and adding one would break exact-match joins against a real deployment.
> `llm-sim.py --selftest` asserts that.

Two consequences worth knowing before you trust a panel. GPU memory tracks *allocation*, not
load — see [Reading the GPU board](#reading-the-gpu-board). And
**temperature and power do not exist at source**: recording rules synthesise them from
utilisation on a T4 curve, then record them under the real DCGM names so a stock DCGM board
finds what it expects.

The full chain for each side lives in the per-topic docs:
[Metrics](#metrics) for GPU,
[llm-simulation.md](llm-simulation.md#metrics) for LLM. Between them they cover the
topology, device-plugin allocation, the annotation that drives utilisation, and the
derived-series coefficients with their self-disabling guard.

## Metrics

The fake `dcgm-exporter` emits the real DCGM metric names, so production dashboards/queries
work unchanged — but it emits **only three series**, the rows marked `exporter` below
(chart 0.0.59, image `status-exporter`). Temperature and power are recording rules this
repo adds; see [Derived temperature & power](#derived-temperature--power).

| Metric | Meaning | Source |
|--------|---------|--------|
| `DCGM_FI_DEV_GPU_UTIL` | GPU utilisation % | exporter |
| `DCGM_FI_DEV_FB_USED` / `DCGM_FI_DEV_FB_FREE` | Framebuffer (memory) used / free (MiB) | exporter |
| `DCGM_FI_DEV_GPU_TEMP` | GPU temperature (°C) | **derived** (recording rule) |
| `DCGM_FI_DEV_POWER_USAGE` | Power draw (W) | **derived** (recording rule) |

Every exporter series is labelled by `Hostname`, `UUID`, `gpu` and `modelName` — the same label
set real DCGM uses, which is what lets a stock board group and filter without modification.
`UUID` is the exporter's deterministic per-GPU id from the topology ConfigMap; note that it is
*not* the same thing as the device plugin's allocation id (see
[How a pod gets a simulated GPU](#how-a-pod-gets-a-simulated-gpu)).

Check for yourself at any time:

```sh
kubectl -n gpu-operator run m --rm -i --restart=Never --image=curlimages/curl:8.10.1 \
  -- -s http://nvidia-dcgm-exporter:9400/metrics | grep '^# HELP DCGM'
```

> **Controllable vs static:** utilisation is the only quantity driven by the pod
> annotation (below). **Memory is all-or-nothing per GPU**, not annotation-driven: a GPU
> with a pod on it reports `FB_USED=15360, FB_FREE=0`; an unallocated GPU reports the
> reverse. So `GPUHighMemoryUsage` trips on *allocation*, not on load. It also never
> clears: an allocated GPU reads exactly 100% for as long as it is held, so the alert
> fires ~2m after install and stays firing. That is a property of the fake exporter, not
> of the rule, which is correct against real hardware where `FB_USED` follows model
> weights and KV-cache growth. The alert comment in `manifests/alerts/gpu-prometheusrule.yaml`
> repeats this deliberately, so the rule can be read without leaving the file.

### Scrape the exporters by hand

These go straight to the producer, with Prometheus out of the picture. Check them first when a
panel is empty, since they separate "nothing is being produced" from "nothing is being scraped
or queried". Both stacks are covered here, GPU and LLM, so one page answers the question:

```sh
# GPU: the fake DCGM exporter (:9400)
kubectl -n gpu-operator run m --rm -i --restart=Never --image=curlimages/curl:8.10.1 \
  -- -s http://nvidia-dcgm-exporter:9400/metrics | grep '^DCGM_FI_DEV_GPU_UTIL'

# LLM: via the Service, which load-balances across BOTH tenants (so repeated
# runs may answer as either model_name)
kubectl -n llm-sim run m --rm -i --restart=Never --image=curlimages/curl:8.10.1 \
  -- -s http://llm-sim:9401/metrics | grep '^vllm:num_requests_'

# LLM: pinned to one tenant, no extra pod (python3 is already in the image)
kubectl -n llm-sim exec deploy/llm-saturated -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9401/metrics').read().decode())" \
  | grep '^vllm:num_requests_'
```

Only `_GPU_UTIL`, `_FB_USED` and `_FB_FREE` come back from `:9400`. If you grep for
`DCGM_FI_DEV_GPU_TEMP` there and find nothing, that is correct: it is a recording rule, so it
exists only inside Prometheus. Query *that* layer instead.

### How a pod gets a simulated GPU

The operator's device plugin advertises `nvidia.com/gpu: 8` on every labelled node, so an
ordinary pod asking for `limits: {nvidia.com/gpu: 1}` schedules and binds with no special
handling. The plugin's `Allocate()` response is also what injects
`MOCK_NVIDIA_VISIBLE_DEVICES` into the container.

> **There is no mutating webhook.** Nothing the plugin injects ever appears in the pod spec:
> `kubectl get pod -o yaml` will not show it, though `printenv` inside the container will.
> Looking for injected env in the spec and concluding the allocation failed is the usual
> wrong turn here.

### Derived temperature & power

There is no exporter knob for temp/power/SM-clock — no container args, no metrics-config
env var, nothing in `helm show values`. `DCGM_FI_DEV_SM_CLOCK` is therefore **not
available at all**, and the two dashboard panels for temperature and power would be
permanently blank.

Instead, `manifests/alerts/gpu-prometheusrule.yaml` synthesises them from utilisation
with recording rules, modelling a Tesla-T4:

| Series | Rule | Idle → 100% util |
|--------|------|------------------|
| `DCGM_FI_DEV_GPU_TEMP` | `32 + 0.45 * DCGM_FI_DEV_GPU_UTIL` | 32 °C → ~77 °C |
| `DCGM_FI_DEV_POWER_USAGE` | `12 + 0.58 * DCGM_FI_DEV_GPU_UTIL` | 12 W → 70 W (T4 TDP) |

They are recorded under the **real DCGM names** deliberately, so stock DCGM dashboards
find the series they expect. Two safeguards keep that honest:

- every derived sample carries `source="derived"` — filter it out with
  `DCGM_FI_DEV_GPU_TEMP{source!="derived"}` to see only genuine exporter data;
- each rule ends in `unless on (UUID) <metric>{source!="derived"}`, so if a future chart
  version starts exporting the real series, the derived one silently stands down for that
  GPU rather than double-plotting.

Because they are recording rules, the panels only fill in **going forward** from when the
`PrometheusRule` was applied — expect an empty left-hand edge on a freshly installed
cluster.

## Driving load

Utilisation is set by a **pod-template** annotation. The value is a range, and the exporter
resamples within it on every scrape, so the series oscillates inside the band rather than
sitting on one number:

```yaml
spec:
  template:
    metadata:
      annotations:
        run.ai/simulated-gpu-utilization: "85-99"   # NOT on the Deployment's top-level metadata
```

The sample workloads (`manifests/workloads/gpu-workloads.yaml`, applied by `install.sh`)
ship three **static** profiles: `gpu-idle` (0-5), `gpu-steady` (40-60), `gpu-busy`
(85-99). To change load, edit the annotation and re-apply — the metrics follow within a
scrape interval or two.

### Richer & time-varying workloads (opt-in)

[`manifests/workloads/extras/`](../manifests/workloads/extras/) (not auto-applied) adds
more workload-like behaviour — its README describes what each manifest does:

```sh
kubectl apply -f manifests/workloads/extras/
```

- **`gpu-multi`** — 2 replicas × 2 GPUs each (multi-GPU-per-node view).
- **`gpu-batch`** (CronJob) — every 15 min a pod loads a GPU for ~5 min at 70-90% then
  completes, so you see a workload appear → load → finish. Trigger now:
  `kubectl create job --from=cronjob/gpu-batch gpu-batch-now -n default`.
- **`gpu-driven`** + **`scripts/drive-load.sh`** — a moving curve instead of a flat band:
  ```sh
  ./scripts/drive-load.sh ramp     # staircase 0→95→0
  ./scripts/drive-load.sh spikes   # baseline/spike train
  ```
  Each step patches the pod-template annotation (rolling a new pod). See
  `manifests/workloads/extras/README.md` for details and capacity notes (the fake topology
  advertises 8 GPUs/node to fit these).

## Dashboard

A self-contained **GPU Simulation — DCGM Overview** dashboard (util / memory / temp /
power) ships as `manifests/dashboards/gpu-sim-dcgm.json`, which `install.sh` wraps in a
sidecar ConfigMap and the compose stack mounts directly. The
temp/power panels depend on the recording rules above — apply the dashboard without
`manifests/alerts/` and they stay blank.

```sh
./scripts/grafana.sh <eks|gke|local>   # → http://localhost:3000/d/gpu-sim-dcgm
```

The one port-forward serves both boards, and the script opens both: the LLM board is
at <http://localhost:3000/d/llm-sim-overview>. If a board hasn't been imported by the
sidecar yet the script says so and skips it, rather than opening a tab onto a 404.

Grafana is ClusterIP; the script holds the port-forward and deep-links by dashboard
**uid** (`gpu-sim-dcgm`), which survives re-installs — no hunting in the UI. Anonymous
`Viewer` auth is on, so viewing needs no login; that is only safe while the Service
stays ClusterIP (see the warning in `helm/kube-prometheus-stack/values.yaml`). The uid
and ConfigMap name live in `scripts/config.sh`, and `install.sh` fails loudly if either
drifts from the JSON.

To swap in the fuller upstream board 12239 (verify label compatibility first), see
`manifests/dashboards/README.md` — keep the `uid` if you want the deep link to work.

### Reading the GPU board

Two panels look wrong at first glance and are not.

**Four moving traces, and a crowd of flat ones.** There are eight `Tesla-T4`s per
*node*, one series per GPU, so the series count follows the node pool — the EKS default
of two nodes gives 16. Only four are ever allocated, and those four carry the annotation
that drives their utilisation: `gpu-busy` at 85-99%, `gpu-steady` at 40-60%, `llm-steady`
at 25-40%, `gpu-idle` at 0-5%. Every other GPU is unallocated and sits flat at zero,
which is a correct reading rather than a missing series. The headroom is there for the
opt-in extras in `manifests/workloads/extras/`.

**Memory in two bands with nothing between them.** Allocation is all-or-nothing here: an
allocated GPU reports `FB_USED` = the whole framebuffer, a free one reports zero. So the
panel tracks *allocation*, not load, and can never show an intermediate value. This is
the one panel whose shape does not transfer — on real hardware `FB_USED` follows model
weights and KV-cache growth and moves continuously. It is also why `GPUHighMemoryUsage`
fires permanently on this rig; see [Alerts](#alerts).

### Warm-up: the first few minutes after install

Allow about six minutes from the end of `install.sh` before treating a panel as wrong.
Three delays stack up, and none of them is a fault:

- **Recording-rule windows.** The rules in `manifests/alerts/` use `[5m]`. A counter that
  starts *inside* that window makes `rate()` climb to its true value as the window fills
  rather than jump to it, so throughput and quantile panels under-read at first. The
  saturated tenant's generation rate walks roughly 76 → 700 tok/s over exactly five
  minutes. That is the window filling, not the simulator ramping: its queue sits at the
  160-request plateau from the very first scrape.
- **Alert `for:` durations.** 1m to 5m depending on the rule, stacked on top of the above,
  so `/alerts` stays quiet for longer than the boards stay wrong.
- **First scrape, first evaluation.** ServiceMonitors scrape every 15s and rules evaluate
  every 30s, so the earliest sample for a newly started pod lands 20-45s behind it.

Both boards default to a 15-minute window for this reason. A freshly installed cluster has
only minutes of history, and on a 30-minute window that history is squeezed into the
right-hand third with the rest of the canvas blank — which reads as "the panels never
populated" when the data was there all along.

Prometheus storage is deliberately ephemeral (`emptyDir`, reasoned out in
`helm/kube-prometheus-stack/values.yaml`), so a Prometheus restart repeats the whole
warm-up from zero.

## Alerts

`manifests/alerts/gpu-prometheusrule.yaml` holds two groups: `gpu.simulation.derived`
(the recording rules above) and `gpu.simulation.rules` (the alerts):

| Alert | Expr (simplified) | For |
|-------|-------------------|-----|
| `GPUHighUtilization` | `DCGM_FI_DEV_GPU_UTIL > 80` | 1m |
| `GPUHighMemoryUsage` | `used/(used+free) > 90%` | 2m |
| `GPUMetricsAbsent` | `absent(DCGM_FI_DEV_GPU_UTIL)` | 5m |

### Worked example — push an alert to firing

The `gpu-busy` workload (util 85-99) keeps `DCGM_FI_DEV_GPU_UTIL > 80`, so
`GPUHighUtilization` moves `pending → firing` after its 1m `for:`. Watch it:

```sh
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
# Prometheus UI → Alerts, or:
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=ALERTS{alertname="GPUHighUtilization",alertstate="firing"}' | jq .
```

To exercise `GPUMetricsAbsent`, stop the fake dcgm-exporter. It may be a Deployment or a
DaemonSet depending on chart version — find it first, then scale/delete:

```sh
kubectl -n gpu-operator get deploy,daemonset          # locate the dcgm-exporter workload
# if a Deployment:
kubectl -n gpu-operator scale deploy/<name> --replicas=0
# if a DaemonSet (can't scale) — patch it to schedule nowhere, then revert:
kubectl -n gpu-operator patch daemonset/<name> -p '{"spec":{"template":{"spec":{"nodeSelector":{"no-such":"node"}}}}}'
# ...wait 5m for the alert; then restore (scale back up / remove the nodeSelector patch).
```

`scripts/verify.sh` automates the utilisation-alert check (polling out the `for:` window).

## The Prometheus console

Grafana answers *what do the boards show*; Prometheus answers *is the data there at
all*. Same treatment, its own command and its own port, so both consoles can be open at
once:

```sh
./scripts/prometheus.sh eks     # or: gke  /  task eks:prometheus  /  make prom-eks
```

It holds the port-forward, waits for `/-/ready`, opens the expression browser, and prints the
three pages worth bookmarking: `/targets`, `/alerts`, `/rules`. That readiness wait matters —
`/-/ready` stays 503 while Prometheus replays its WAL, so "ready" here means queries will
actually answer.

**If any scrape target is down it says so on startup.** A dead exporter is the most common
reason a panel is empty, and it is completely invisible from Grafana.

| Option | How |
|--|--|
| Port 9090 already in use | `PROMETHEUS_PORT=9091 ./scripts/prometheus.sh eks` |
| Don't auto-open a browser | `./scripts/prometheus.sh eks --no-open` |

## Queries to start with

Paste these into the expression browser. They are also the argument this repo is making: none
of them contains anything simulation-specific. The same text works against a real DCGM exporter
and a real vLLM.

```promql
# 1. The raw exporter surface: one series per simulated GPU.
DCGM_FI_DEV_GPU_UTIL

# 2. The synthesised series answers to the REAL DCGM name. These two are identical,
#    which is the whole transfer argument: a stock DCGM board finds what it expects.
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_GPU_TEMP{source="derived"}

# 3. Which workload is driving which GPU. `exported_pod`, not `pod`: the exporter
#    labels each allocated GPU with its consumer, and Prometheus renames those
#    because the scrape target's own pod label wins the collision.
max by (exported_namespace, exported_pod, gpu) (DCGM_FI_DEV_GPU_UTIL{exported_pod!=""})

# 4. The two tenants, one either side of the 2s alert threshold. This single query
#    is the point of running two simulators.
llm:ttft:p95_5m

# 5. The same number computed by hand. The recording rule is a shortcut, not magic,
#    and THIS is the expression you would paste into a real vLLM deployment.
histogram_quantile(0.95, sum by (model_name, le) (rate(vllm:time_to_first_token_seconds_bucket[5m])))

# 6. Throughput per tenant. Needs >= 2 samples in the window, which is why the
#    ServiceMonitor scrape interval is 15s and must stay <= 30s.
sum by (model_name) (rate(vllm:generation_tokens_total[5m]))

# 7. Where did a request's time actually GO? queue -> prefill -> decode, and the
#    three sum to the end-to-end mean exactly.
#    ⚠️ MEANS, not percentiles, and both reasons are measured: quantiles are not
#    additive (a p95 breakdown does not reach the p95 total), and these buckets
#    cannot resolve prefill at all — the first boundary is 0.3s against a modelled
#    0.08s, so a p95 reads ~3x high. That second effect TRANSFERS to real vLLM,
#    because the boundaries are upstream's. A histogram mean has no bucket
#    dependence, `_sum` and `_count` being exact.
#    ⚠️ A regex on __name__, NOT `a or b or c`. PromQL's set operators match on
#    labels EXCLUDING the metric name, so with all three carrying the same
#    {model_name, source} the `or` form returns only the FIRST series — silently,
#    and it looks like the other two rules are missing. Verified with promtool.
{__name__=~"llm:(queue|prefill|decode):mean5m"}

#    ...and the assertion that it adds up, which is what `verify.sh` L8 checks:
(llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m

# 8. Attribution: which real GPU is a simulator pod actually on? Joined on the POD,
#    because llmsim_gpu_binding_info's device_id is the device plugin's allocation id
#    and never matches a DCGM UUID (see the note below).
llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu)
  label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""},
    "namespace", "$1", "exported_namespace", "(.*)"),
    "pod", "$1", "exported_pod", "(.*)")

# 9. What the rig is complaining about right now.
ALERTS{alertstate="firing"}
```

> **`device_id` is not a GPU UUID.** The device plugin injects its own per-allocation
> id as `MOCK_NVIDIA_VISIBLE_DEVICES`: a bare random v4, minted fresh every time a pod is
> scheduled. The exporter labels that same GPU differently, with a deterministic
> `GPU-`-prefixed id from the topology ConfigMap. The two come from different code paths
> and never match, so an `on (UUID)` join returns nothing, silently and forever. Query 8
> is the join that does work, and the `L4b` check in `verify.sh` asserts that exact
> expression.

The same queries over HTTP, when you want them in a script rather than a browser:

```sh
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=llm:ttft:p95_5m' | python3 -m json.tool
curl -s localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c
```
