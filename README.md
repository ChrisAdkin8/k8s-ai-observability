[![CI](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/workflows/ci.yml/badge.svg)](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/kubernetes-v1.36.1-326ce5.svg)](kind/gpu-sim.yaml)

# k8s-ai-observability

**Build and test GPU and LLM observability without a GPU.** A simulated NVIDIA GPU
stack, a simulated vLLM serving stack, and Prometheus + Grafana — on kind, EKS or GKE.
No hardware, no quota, no drivers, no model weights.

![Four time-series panels tracking utilisation, memory, temperature and power across
eight simulated GPUs](docs/gpu-dashboard.png)

```sh
cd compose && docker compose up -d     # both boards on localhost:3000. ~1 min, no Kubernetes.
```

That is the fastest way to see it. To exercise Kubernetes itself — scheduling on
`nvidia.com/gpu`, the device plugin, ServiceMonitor discovery, the Grafana sidecar:

```sh
task local:up        # kind cluster -> full stack -> acceptance checks. ~6 min, $0.
task local:grafana   # holds one port-forward, opens both boards on localhost:3000
task local:destroy   # prompts, then removes everything
```

No cloud account, no credentials, no `terraform.tfvars`, no spend. `local` is not a
reduced-fidelity preview: it runs the same manifests, the same pinned charts and the same
acceptance checks as EKS and GKE, which exist only to prove it works on managed
Kubernetes. The compose stack reads those same dashboards, rules, simulator and profiles
— see [compose/](compose/) for what it deliberately cannot cover.

## What you get

- **GPU simulation** — [`run-ai/fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator)
  advertises `nvidia.com/gpu`, injects a fake `nvidia-smi`, and emits DCGM-format
  metrics, so the standard NVIDIA observability stack works as-is.
- **LLM serving simulation** — [`scripts/llm-sim.py`](scripts/llm-sim.py), one
  dependency-free Python file emitting real vLLM metric names, types and histogram
  buckets. Two tenants side by side: one healthy, one deliberately overloaded.
- **Recording rules and alerts** over both domains, [unit-tested with `promtool`](tests/)
  in about a second, with no cluster — both sides of every threshold, including the ones
  the rig never drives. If you build alerting here, you can test it here.
- **Two Grafana dashboards**, one `.json` each, used three ways: wrapped in a ConfigMap
  for the sidecar, mounted by the compose stack, or imported into any Grafana. Nothing is
  ever clicked into place, so a re-install reproduces them exactly.
- **An acceptance suite** ([`scripts/verify.sh`](scripts/verify.sh)) that asserts metrics
  are flowing, both boards render, and the alerts actually reach `firing`.

![Six panels comparing a healthy tenant and an overloaded one side by
side](docs/llm-dashboard.png)

Two tenants either side of the 2s alert threshold — the point of running two simulators.
`sim-llama-3-8b-steady` answers in ~120 ms; `sim-llama-3-8b-saturated` reports a p95 of
78s with 16 requests running and 160 queued behind it. Every panel aggregates
`by (model_name)`, so the overloaded tenant is never averaged into the healthy one.

> The screenshots above were captured before the vLLM V1 bucket sync, so the saturated
> tenant reads `1.20 mins` rather than 78s. The simulated latency did not change — only
> the histogram resolution it is reported at. See
> [versions.md](docs/versions.md#keeping-them-honest).

## What transfers, and what doesn't

These metrics are synthetic. That is the trade, and it is worth being precise about which
half of your work it affects.

| Transfers unchanged | Does not |
|--|--|
| Metric names, types and histogram bucket boundaries | Absolute values, and any threshold tuned to them |
| PromQL — recording rules, alert expressions, panel queries | GPU silicon behaviour: real utilisation curves, thermal throttling, profiling |
| Kubernetes behaviour — scheduling, device plugin, ServiceMonitor wiring, autoscaling | Inference behaviour: batching, KV-cache eviction, scheduler dynamics |
| Dashboard JSON, alert rules, SLO definitions | The GPU memory panel, whose shape is degenerate here ([why](docs/observability.md#reading-the-gpu-board)) |

In short: build the *pipeline* here, tune the *numbers* on real hardware.

## Why not just…

| | Why not |
|--|--|
| …use a real GPU node? | That is the goal, not the starting point. It costs money and quota, and you cannot put one in CI — this repo's own CI stands the whole stack up on a free runner. |
| …use [kwok](https://kwok.sigs.k8s.io)? | kwok fakes the kubelet, so pods never really run. Excellent for scheduler and scale testing; it emits no DCGM or vLLM metrics, so there is nothing to point a dashboard at. |
| …use Grafana's TestData datasource? | Gives you panels with data, but no metric names, labels or PromQL — so nothing you build against it transfers to a real deployment. |
| …push synthetic series straight into Prometheus? | Gets you series without the Kubernetes half: no device plugin, no `nvidia.com/gpu` scheduling, no ServiceMonitor discovery, no operator wiring to get wrong. |
| …run real vLLM with a tiny model? | Real metrics, but it needs a GPU to be meaningful, and you cannot ask it for a saturated tenant on demand. Here, saturation is a line in a JSON profile. |

## Prerequisites

| Tool | Notes |
|------|-------|
| `kubectl`, `helm`, `curl`, `python3` | required on every target |
| [`kind`](https://kind.sigs.k8s.io) + Docker, colima or podman | local only — and all it needs |
| [`task`](https://taskfile.dev/docs/installation) | the front door. `brew install go-task/tap/go-task`; needs >= 3.32 |
| `terraform` >= 1.6, plus `aws` or `gcloud` | EKS/GKE only. GKE also needs `gke-gcloud-auth-plugin` — a [common silent miss](docs/gke.md#prerequisites) |
| `promtool` | optional, for `task rule-tests`. Ships inside the Prometheus release |
| `docker compose` | optional, for the [no-Kubernetes path](compose/). Nothing else on the host |

`task tools` checks all of the above, and only fails on the target-agnostic ones.

**Size your container runtime first.** Colima's 2 CPU / 2 GiB default cannot run this —
Prometheus alone requests 1Gi and limits 2Gi. `scripts/kind-up.sh` refuses below 5 GiB and
prints the exact `colima` / Docker Desktop / `podman machine` command to fix it. Allow
8 GiB and 4 CPU:

```sh
colima start --cpu 4 --memory 8 --disk 40
```

**Or run the trimmed stack instead.** `LITE=1` drops Alertmanager, kube-state-metrics,
node-exporter and the chart's ~100 default rules, and puts Prometheus on 256Mi/512Mi —
which lowers the floor to 3 GiB and the recommendation to 4:

```sh
LITE=1 task local:up
```

It keeps everything `local` exists to prove: ServiceMonitor discovery, PrometheusRule
evaluation, the Grafana sidecar import, and scheduling on `nvidia.com/gpu`. Both
dashboards render and every acceptance check in `verify.sh` still runs and still passes —
none of them touches what was removed.
[`values-lite.yaml`](helm/kube-prometheus-stack/values-lite.yaml) lists exactly what
you give up.

## Quick start

Three targets — `local`, `eks`, `gke` — each with two phases. **Phase 1** creates the
cluster with the GPU-sim node label (`scripts/kind-up.sh` locally, `terraform apply` on
the clouds). **Phase 2** is `scripts/install.sh`, identical everywhere. The phases stay
separate so the Kubernetes tooling is never pointed at a cluster that does not exist yet.

`task` on its own lists everything. The task list is defined once in
`taskfiles/target.yml` and included three times, so the targets cannot drift apart.

```sh
# EKS
cp terraform/eks/terraform.tfvars.example terraform/eks/terraform.tfvars   # set api_allowed_cidrs
task eks:up

# GKE is identical bar the prefix (Standard, not Autopilot)
echo 'GCP_PROJECT=my-project' > .env
task gke:up
```

Every command below takes any of the three prefixes ( `local` | `eks` | `gke` ):

| Command | What it does |
|--|--|
| `task prefix:up` | the lot: cluster → stacks → acceptance checks |
| `task prefix:grafana` / `prefix:prometheus` | open the boards / the Prometheus console |
| `task prefix:install` / `prefix:verify` | Phase 2 only, when the cluster already exists |
| `task prefix:load -- ramp` | drive a GPU utilisation curve (`ramp`, `spikes`) |
| `task prefix:llm-load -- ramp` | drive an LLM load curve (`ramp`, `burst`, `saturation`, `idle`) |
| `task prefix:teardown` / `prefix:destroy` | remove the stacks / and the cluster too |
| `task rule-tests` / `task selftest` | no cluster needed — see [tests/](tests/) |
| `task compose` | no cluster at all — both boards in ~1 min, see [compose/](compose/) |

Both load drivers target opt-in Deployments that `install.sh` does *not* apply, so apply
the one you need first or the task fails its precondition:

```sh
kubectl apply -f manifests/workloads/extras/   # for task <prefix>:load
kubectl apply -f manifests/llm/extras/         # for task <prefix>:llm-load
```

`scripts/` stays the source of truth for install ordering, the wrong-context guard and
the drift assertions — Task only wraps it. To drive the phases as scripts instead, see
[docs/usage.md](docs/usage.md).

### Cost and time

| | Time | Cost |
|--|--|--|
| `compose` | ~1 min | $0 |
| `local` | ~6 min end to end | $0 |
| `eks` / `gke` | ~20 min, node provisioning being the long pole | Billed hourly whether idle or not — control plane, 2 nodes, one NAT gateway, disks. Order of magnitude: tens of cents an hour. **Always `task <prefix>:destroy` when finished.** |

No GPU instances or GPU quota are used on any target.

## Opening the dashboards

Grafana stays ClusterIP — no load balancer, no ingress, no cost. `install.sh` prints both
URLs when it finishes, and `task <prefix>:grafana` holds one port-forward and opens both:

- <http://localhost:3000/d/gpu-sim-dcgm> — utilisation, memory, temperature, power
- <http://localhost:3000/d/llm-sim-overview> — first-token latency, throughput, queue
  depth, KV cache

Viewing needs no login: anonymous access is `Viewer`-only. To edit, log in as `admin` —
the script prints the generated password. Use `GRAFANA_PORT=3001` if 3000 is taken, and
`--no-open` to skip the browser.

> **⚠️ Anonymous access is safe only because the Service is ClusterIP**, so the sole way
> in is a port-forward, which already requires cluster RBAC. If you switch
> `grafana.service.type` to `LoadBalancer` or `NodePort`, disable `auth.anonymous` in
> `helm/kube-prometheus-stack/values.yaml` first — otherwise you publish the dashboards to
> anyone who can reach the address.

If a board 404s or its panels come up empty, see
[docs/troubleshooting.md](docs/troubleshooting.md).

## Documentation

| Document | Covers |
|--|--|
| [docs/architecture.md](docs/architecture.md) | how the pieces fit at runtime, what the stack deliberately omits, install ordering, every EKS↔GKE difference, and the naming invariant to read before editing |
| [docs/observability.md](docs/observability.md) | where each metric comes from, reading the GPU board, eight PromQL queries that also work against real hardware, derived temperature and power, driving load |
| [docs/llm-simulation.md](docs/llm-simulation.md) | the vLLM simulator, load profiles, LLM alerts |
| [tests/](tests/) | the rule tests and the simulator selftest — what they cover and how to add one |
| [compose/](compose/) | the no-Kubernetes path: what it shares with the cluster, and what it cannot exercise |
| [docs/usage.md](docs/usage.md) | running every phase as scripts instead of Task |
| [docs/versions.md](docs/versions.md) | every pinned version and the one place each is set |
| [docs/eks.md](docs/eks.md) / [docs/gke.md](docs/gke.md) | per-cloud specifics |
| [docs/troubleshooting.md](docs/troubleshooting.md) | empty panels, 404s, dead targets, warm-up |

## Licence

[MIT](LICENSE), © 2026 Chris Adkin.

Nothing third-party is vendored here. The Helm charts
([`fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator),
[`kube-prometheus-stack`](https://github.com/prometheus-community/helm-charts)) are pulled
from their own repos at install time, under their own licences. Both dashboards in
`manifests/dashboards/` are original to this repo rather than derived from a grafana.com
board, so the MIT grant above covers everything the repo actually ships.
