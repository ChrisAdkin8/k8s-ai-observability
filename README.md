[![CI](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/workflows/ci.yml/badge.svg)](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ChrisAdkin8/k8s-ai-observability?color=blue)](https://github.com/ChrisAdkin8/k8s-ai-observability/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/kubernetes-v1.36.1-326ce5.svg)](kind/gpu-sim.yaml)

# k8s-ai-observability

**Build and test GPU and LLM observability without a GPU.** A simulated NVIDIA GPU stack,
a simulated vLLM serving stack, and Prometheus + Grafana, on kind, EKS or GKE. No
hardware, no quota, no drivers, no model weights.

![Four time-series panels tracking utilisation, memory, temperature and power across
eight simulated GPUs](docs/gpu-dashboard.png)

```sh
cd compose && docker compose up -d     # both boards on localhost:3000. ~1 min, no Kubernetes.
```

That's the fastest way to see it. To exercise Kubernetes itself (scheduling on
`nvidia.com/gpu`, the device plugin, ServiceMonitor discovery, the Grafana sidecar):

```sh
task local:up        # kind cluster -> full stack -> acceptance checks. ~6 min, $0.
task local:grafana   # holds one port-forward, opens both boards on localhost:3000
task local:destroy   # prompts, then removes everything
```

There's no cloud account, no credentials, no `terraform.tfvars` and no spend. `local` is
not a reduced-fidelity preview: it runs the same manifests, the same pinned charts and the
same acceptance checks as EKS and GKE, which exist only to prove it works on managed
Kubernetes. The compose stack reads those same dashboards, rules, simulator and profiles.
See [compose/](compose/) for what it deliberately cannot cover.

## What you get

- **GPU simulation.** [`run-ai/fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator)
  advertises `nvidia.com/gpu`, injects a fake `nvidia-smi`, and emits DCGM-format
  metrics, so the standard NVIDIA observability stack works as-is.
- **LLM serving simulation.** [`scripts/llm-sim.py`](scripts/llm-sim.py) is one
  dependency-free Python file emitting real vLLM metric names, types and histogram
  buckets. It runs two tenants side by side: one healthy, one deliberately overloaded.
- **Recording rules and alerts** over both domains, [unit-tested with `promtool`](tests/)
  in about a second, with no cluster. The tests cover both sides of every threshold,
  including the ones the rig never drives. If you build alerting here, you can test it here.
- **Two Grafana dashboards**, one `.json` each, used four ways: wrapped in a ConfigMap
  for the sidecar, mounted by the compose stack, imported into any Grafana, or derived into
  the grafana.com upload by `task dashboards`. Nothing is ever clicked into place, so a
  re-install reproduces them exactly. Both are published, so you can import them by id
  into a Grafana you already run:
  [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) (GPU) and
  [25620](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/) (vLLM).
- **An acceptance suite** ([`scripts/verify.sh`](scripts/verify.sh)) that asserts metrics
  are flowing, both boards render, and the alerts actually reach `firing`.
- **A weekly check against real vLLM**, which is what keeps the "real metric names and
  buckets" claim above true rather than merely asserted.
  [`scripts/check-vllm-buckets.py`](scripts/check-vllm-buckets.py) fetches upstream's own
  metrics definitions and fails if a bucket boundary or a metric name has moved, and
  reports the upstream metrics this simulator does not emit so that distance stays
  visible. It exists because 0.1.0 and 0.2.0 shipped the superseded `v0.6.x` bucket layout
  with **every test green** — every test reads the simulator, and the simulator was
  consistent with itself. A drifted boundary does not error or blank a panel; it returns a
  confident, plausible percentile that will not match real hardware, which no self-contained
  test suite can catch.
- **The simulator as a container image**, for pointing your *own* dashboards at a realistic
  vLLM metric surface without cloning anything:

  ```sh
  docker run --rm -p 9401:9401 ghcr.io/chrisadkin8/vllm-metrics-sim:latest
  ```

  No `--profile` needed — it falls back to a self-consistent steady tenant and serves on
  `:9401`. Published on every release tag for `linux/amd64` and `linux/arm64`, and built
  from [`scripts/llm-sim.py`](scripts/llm-sim.py) rather than a second copy of it. ⚠️ The
  port override is **`LLM_SIM_LISTEN_PORT`**, not the more obvious `LLM_SIM_PORT` — see
  [docs/llm-simulation.md](docs/llm-simulation.md#the-container-image) for why that is not
  a matter of taste. This image is **not** how the rig itself runs the simulator, and
  deliberately so.
- **A path for clusters that already run Prometheus.** If you imported one of the boards
  from the catalog and found panels blank for want of the `llm:*` recording rules, you do
  not have to hand your monitoring stack to this repo — either
  [bring your own Prometheus](#bring-your-own-prometheus) with the install script, or use
  the **[Helm chart](charts/k8s-ai-observability/README.md)**:

  ```sh
  task chart                                          # assembles into gitignored dist/
  helm install rig dist/charts/k8s-ai-observability \
    --set releaseLabel=<your monitoring release>
  helm test rig --logs                                # ⚠️ do not skip this
  ```

  It installs the simulators, workloads, rules and dashboards and leaves your monitoring
  stack alone. ⚠️ Two of its values — the `release:` selector and the Grafana sidecar
  label — fail with **no error at all** if they are wrong; `helm test` is what tells you,
  and it is opt-in. The chart README explains both, and why there is a build step.

![Six panels comparing a healthy tenant and an overloaded one side by
side](docs/llm-dashboard.png)

Two tenants sit either side of the 2s alert threshold, which is the point of running two
simulators. `sim-llama-3-8b-steady` answers in ~120 ms; `sim-llama-3-8b-saturated` reports
a p95 of 78s with 16 requests running and 160 queued behind it. Every panel aggregates
`by (model_name)`, so the overloaded tenant is never averaged into the healthy one.

> The screenshots above were captured before the vLLM V1 bucket sync, so the saturated
> tenant reads `1.20 mins` rather than 78s. The simulated latency did not change, only the
> histogram resolution it is reported at. See
> [versions.md](docs/versions.md#keeping-them-honest).

## What transfers, and what doesn't

These metrics are synthetic. That is the trade, and it is worth being precise about which
half of your work it affects.

| Transfers unchanged | Does not |
|--|--|
| Metric names, types and histogram bucket boundaries | Absolute values, and any threshold tuned to them |
| PromQL: recording rules, alert expressions, panel queries | GPU silicon behaviour: real utilisation curves, thermal throttling, profiling |
| Kubernetes behaviour: scheduling, device plugin, ServiceMonitor wiring, autoscaling | Inference behaviour: batching, KV-cache eviction, scheduler dynamics |
| Dashboard JSON, alert rules, SLO definitions | The GPU memory panel, whose shape is degenerate here ([why](docs/observability.md#reading-the-gpu-board)) |

In short: build the *pipeline* here, tune the *numbers* on real hardware.

## Why not just…

| | Why not |
|--|--|
| …use a real GPU node? | That is the goal, not the starting point. It costs money and quota, and you can't put one in CI. This repo's own CI stands the whole stack up on a free runner. |
| …use [kwok](https://kwok.sigs.k8s.io)? | kwok fakes the kubelet, so pods never really run. Excellent for scheduler and scale testing; it emits no DCGM or vLLM metrics, so there is nothing to point a dashboard at. |
| …use Grafana's TestData datasource? | Gives you panels with data, but no metric names, labels or PromQL, so nothing you build against it transfers to a real deployment. |
| …push synthetic series straight into Prometheus? | Gets you series without the Kubernetes half: no device plugin, no `nvidia.com/gpu` scheduling, no ServiceMonitor discovery, no operator wiring to get wrong. |
| …run real vLLM with a tiny model? | Real metrics, but it needs a GPU to be meaningful, and you cannot ask it for a saturated tenant on demand. Here, saturation is a line in a JSON profile. |

## Prerequisites

| Tool | Notes |
|------|-------|
| `kubectl`, `helm`, `curl`, `python3` | required on every target |
| [`kind`](https://kind.sigs.k8s.io) + Docker, colima or podman | local only, and all it needs |
| [`task`](https://taskfile.dev/docs/installation) | the front door. `brew install go-task/tap/go-task`; needs >= 3.32 |
| `terraform` >= 1.6, plus `aws` or `gcloud` | EKS/GKE only. GKE also needs `gke-gcloud-auth-plugin`, a [common silent miss](docs/gke.md#prerequisites) |
| `promtool` | optional, for `task rule-tests`. Ships inside the Prometheus release |
| `docker compose` | optional, for the [no-Kubernetes path](compose/). Nothing else on the host |

`task tools` checks all of the above, and only fails on the target-agnostic ones.

**Size your container runtime first.** Colima's 2 CPU / 2 GiB default cannot run this:
Prometheus alone requests 1Gi and limits 2Gi. `scripts/kind-up.sh` refuses below 5 GiB and
prints the exact `colima` / Docker Desktop / `podman machine` command to fix it. Allow
8 GiB and 4 CPU (the floors and the recommendation are `KIND_MIN_MEMORY_GIB` /
`KIND_MIN_CPUS` and `KIND_WANT_MEMORY_GIB` / `KIND_WANT_CPUS`, if you need to move them):

```sh
colima start --cpu 4 --memory 8 --disk 40
```

**Or run the trimmed stack instead.** `LITE=1` drops Alertmanager, kube-state-metrics,
node-exporter and the chart's ~100 default rules, and puts Prometheus on 256Mi/512Mi. That
lowers the floor to 3 GiB and the recommendation to 4:

```sh
LITE=1 task local:up
```

It keeps everything `local` exists to prove: ServiceMonitor discovery, PrometheusRule
evaluation, the Grafana sidecar import, and scheduling on `nvidia.com/gpu`. Both
dashboards render and every acceptance check in `verify.sh` still runs and still passes,
because none of them touches what was removed.
[`values-lite.yaml`](helm/kube-prometheus-stack/values-lite.yaml) lists exactly what
you give up.

## Quick start

Three targets (`local`, `eks`, `gke`), each with two phases. **Phase 1** creates the
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
| `task prefix:install -- --skip-monitoring` | Phase 2 against a Prometheus you already run ([below](#bring-your-own-prometheus)) |
| `task prefix:load -- ramp` | drive a GPU utilisation curve (`ramp`, `spikes`) |
| `task prefix:llm-load -- ramp` | drive an LLM load curve (`ramp`, `burst`, `saturation`, `idle`) |
| `task prefix:teardown` / `prefix:destroy` | remove the stacks / and the cluster too |
| `task rule-tests` / `task selftest` | no cluster needed, see [tests/](tests/) |
| `task compose` | no cluster at all: both boards in ~1 min, see [compose/](compose/) |

Both load drivers target opt-in Deployments that `install.sh` does *not* apply, so apply
the one you need first or the task fails its precondition:

```sh
kubectl apply -f manifests/workloads/extras/   # for task <prefix>:load
kubectl apply -f manifests/llm/extras/         # for task <prefix>:llm-load
```

`scripts/` stays the source of truth for install ordering, the wrong-context guard and
the drift assertions; Task only wraps it. To drive the phases as scripts instead, see
[docs/usage.md](docs/usage.md).

### Bring your own Prometheus

The default path installs `kube-prometheus-stack`. If you already run one, `--skip-monitoring`
leaves it alone and installs only the simulators, rules, dashboards and workloads:

```sh
./scripts/install.sh local --skip-monitoring
task local:install -- --skip-monitoring        # same thing through the front door
./scripts/verify.sh local --byo
```

**If your Helm release is not named `kube-prometheus-stack`, say so** — one variable
covers every script, because they all read it from `scripts/config.sh`:

```sh
export KPS_RELEASE=my-monitoring
./scripts/install.sh local --skip-monitoring
./scripts/verify.sh   local --byo
./scripts/grafana.sh  local                    # port-forwards svc/$KPS_RELEASE-grafana
```

⚠️ **Two labels decide whether any of this works, and both fail with no error at all** —
no scrape, no rule evaluation, empty boards, every object reporting itself as created:

| | Default | Set it with | If it is wrong |
|--|--|--|--|
| the `release:` selector on the two ServiceMonitors and two PrometheusRules | follows `KPS_RELEASE` | `RELEASE_LABEL` | your Prometheus never adopts them |
| the Grafana sidecar's discovery label | `grafana_dashboard=1` | `GRAFANA_DASHBOARD_LABEL` and `GRAFANA_DASHBOARD_LABEL_VALUE` | the boards are never imported |

The selector default is the upstream chart's: `ruleSelectorNilUsesHelmValues` and its two
siblings default to `true`, making the selector `release=<your release name>`. So you have
two possible fixes — set `RELEASE_LABEL` here, or set those three values `false` on your
side. The second is often not yours to change.

`verify.sh --byo` is what tells you. It still asserts everything about the simulators,
scrapes, rules and dashboards — those are exactly what a wrong label breaks — and relaxes
only the anonymous-Grafana claim, which follows from this repo's own Helm values rather
than from anything it installed. A board Grafana has never heard of stays a hard failure.

The monitoring stack must live in the `monitoring` namespace, and its Grafana sidecar must
watch that namespace; `install.sh` refuses up front, naming the fix, if the namespace or
the Prometheus Operator CRDs are missing.

### Cost and time

| | Time | Cost |
|--|--|--|
| `compose` | ~1 min | $0 |
| `local` | ~6 min end to end | $0 |
| `eks` / `gke` | ~20 min, node provisioning being the long pole | Billed hourly whether idle or not: control plane, 2 nodes, one NAT gateway, disks. Order of magnitude: tens of cents an hour. **Always `task <prefix>:destroy` when finished.** |

No GPU instances or GPU quota are used on any target.

## Opening the dashboards

Grafana stays ClusterIP, so there is no load balancer, no ingress and no cost.
`install.sh` prints both URLs when it finishes, and `task <prefix>:grafana` holds one
port-forward and opens both:

- <http://localhost:3000/d/gpu-sim-dcgm> for utilisation, memory, temperature and power
- <http://localhost:3000/d/llm-sim-overview> for first-token latency, throughput, queue
  depth, KV cache and prefix-cache reuse

Viewing needs no login: anonymous access is `Viewer`-only. To edit, log in as `admin`; the
script prints the generated password. Use `GRAFANA_PORT=3001` if 3000 is taken, and
`--no-open` to skip the browser.

> **⚠️ Anonymous access is safe only because the Service is ClusterIP**, so the sole way
> in is a port-forward, which already requires cluster RBAC. If you switch
> `grafana.service.type` to `LoadBalancer` or `NodePort`, disable `auth.anonymous` in
> `helm/kube-prometheus-stack/values.yaml` first. Otherwise you publish the dashboards to
> anyone who can reach the address.

If a board 404s or its panels come up empty, see
[docs/troubleshooting.md](docs/troubleshooting.md).

## Documentation

| Document | Covers |
|--|--|
| [docs/architecture.md](docs/architecture.md) | how the pieces fit at runtime, what the stack deliberately omits, install ordering, every EKS↔GKE difference, and the naming invariant to read before editing |
| [docs/observability.md](docs/observability.md) | where each metric comes from, reading the GPU board, eight PromQL queries that also work against real hardware, derived temperature and power, driving load |
| [docs/llm-simulation.md](docs/llm-simulation.md) | the vLLM simulator, load profiles, LLM alerts |
| [tests/](tests/) | the rule tests and the simulator selftest: what they cover and how to add one |
| [compose/](compose/) | the no-Kubernetes path: what it shares with the cluster, and what it cannot exercise |
| [CONTRIBUTING.md](CONTRIBUTING.md) | the invariants that fail *silently* if broken, what to re-check when bumping a pinned version, and what is deliberately out of scope |
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
