# The no-Kubernetes path

Both dashboards, both alert sets, in under a minute. No cluster, no kind, no helm.

```sh
cd compose && docker compose up -d
```

- <http://localhost:3000/d/gpu-sim-dcgm> — GPU: utilisation, memory, temperature, power
- <http://localhost:3000/d/llm-sim-overview> — LLM: first-token latency, throughput,
  queue depth, KV cache
- <http://localhost:9090> — Prometheus: targets, alerts, expression browser

No login needed to view (anonymous `Viewer`, as on the cluster). To edit, log in as
`admin` / `admin`. Stop with `docker compose down`.

> **Ports 3000 and 9090 clash with `scripts/grafana.sh` and `scripts/prometheus.sh`.**
> If you are also running a cluster port-forward, the loopback-bound forward wins and you
> get the *other* Prometheus at the same URL, with no error anywhere. Override:
> `PROMETHEUS_PORT=19090 GRAFANA_PORT=13000 docker compose up -d`

## What this is for

`task local:up` needs a container runtime with 8 GiB, kind, helm and about six minutes.
That is the right price for proving scheduling, the device plugin and the operator
wiring — and far too high for "show me the boards" or "does my PromQL work". This
answers those.

## What is shared with the cluster path

Verbatim, from the same files the cluster applies — not a copy:

| | Source |
|--|--|
| Both dashboards | `../manifests/dashboards/*.json`, mounted straight into Grafana |
| Recording rules + alerts | `../manifests/alerts/`, unwrapped by `scripts/extract.sh` |
| The LLM simulator | `../scripts/llm-sim.py` |
| Both load profiles | `../manifests/llm/10-profiles.yaml`, same unwrapping |
| Scrape job names | `llm-sim`, `fake-dcgm-exporter` — so every query in [docs/observability.md](../docs/observability.md#queries-to-start-with) pastes in unchanged |

The `generate` service does that unwrapping at start-up, into `.generated/` (gitignored).
It is why `docker compose up` needs nothing installed on the host: `scripts/extract.sh`
is POSIX sh and awk, and runs in busybox.

**Measured on this stack**, with the same rules the cluster loads:

```
llm:ttft:p95_5m   sim-llama-3-8b-steady      0.099   ← under the 2s alert threshold
llm:ttft:p95_5m   sim-llama-3-8b-saturated  78      ← over it, on purpose
firing            GPUHighUtilization  GPUHighMemoryUsage  LLMHighTTFT
```

Both figures are bucket-quantised rather than noisy, which is why they are quotable at
all: the steady tenant's latency sits in `(0.08, 0.1]` and the saturated one's ~58s queue
wait in V1's `(40, 80]`, so `histogram_quantile` reports `0.08 + 0.02×0.95` and
`40 + 40×0.95` respectively. The saturated figure read `81.5` before the V1 bucket sync,
on boundaries vLLM no longer has — same simulated latency, different resolution.

## What is *not* shared

`gpu-metrics-sim.py` stands in for [`run-ai/fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator).
That operator is a device plugin and a DaemonSet — neither has any meaning outside a
cluster, so something has to emit the DCGM series here.

It copies the fake exporter's surface exactly: the same three series
(`DCGM_FI_DEV_GPU_UTIL`, `_FB_USED`, `_FB_FREE`), the same labels, the same eight
`Tesla-T4`s, the same four utilisation bands, and the same all-or-nothing memory model.
Three series are enough for all four GPU panels and every GPU alert, because temperature
and power are synthesised from utilisation by the recording rules — the same PromQL,
running here. See the file header for the details.

**"Exactly" is now checked rather than claimed.** The series and label keys live in
[`tests/contracts/dcgm-surface.json`](../tests/contracts/dcgm-surface.json), and both
producers are asserted against it — this one for exact equality, the cluster's exporter
for a subset (`verify.sh` check 3b, since Prometheus adds target labels the exporter never
emitted). One file, two independent producers:

```sh
task compose-selftest        # or: python3 compose/gpu-metrics-sim.py --selftest
```

It needs no docker and runs in CI's `fast` job on every push. Before the contract existed
the agreement was prose in that file's header, so a chart bump renaming a series would
fail the kind path loudly and let this path drift in silence — backwards, given this is
the first command in the README.

## Is this covered by CI?

Yes, as of the `compose` job in `.github/workflows/ci.yml`. It brings the stack up on a
runner and asserts it the way a user would: both producers **through Prometheus** rather
than by curling them (the simulator containers publish no ports — only Prometheus and
Grafana do), every scrape target `up`, the derived temp/power/p95 series that only exist
if the extracted rules loaded, and both boards fetched by uid over an unauthenticated
request. Logs upload on failure; `docker compose down -v` always runs.

## So what can't you do here?

Kubernetes itself. Nothing in this stack exercises scheduling on `nvidia.com/gpu`, the
device plugin's `Allocate()` path, ServiceMonitor discovery, the Grafana sidecar import,
or the naming invariant that ties them together. `task local:up` covers those, and
`scripts/verify.sh` asserts them.

Use this to build and check dashboards, PromQL, recording rules and alert thresholds.
Use the cluster to prove the wiring.

## Editing while it runs

- **Dashboards** — edit `../manifests/dashboards/*.json`; Grafana reloads within 10s.
- **Rules and profiles** — edit the manifests, then `docker compose up -d --force-recreate generate prometheus`
  to re-unwrap them.
- **Load** — the simulators poll their profile every 10s, so editing
  `.generated/profiles/*.json` changes load live, with no restart and no gap in the
  counters. Note that `generate` overwrites those on next start; edit the manifest to
  make it stick.
