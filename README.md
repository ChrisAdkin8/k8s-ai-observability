[![CI](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/workflows/ci.yml/badge.svg)](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ChrisAdkin8/k8s-ai-observability?color=blue)](https://github.com/ChrisAdkin8/k8s-ai-observability/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/kubernetes-v1.36.1-326ce5.svg)](kind/gpu-sim.yaml)
[![GPU board](https://img.shields.io/badge/grafana.com-25618-F46800.svg)](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/)
[![vLLM board](https://img.shields.io/badge/grafana.com-25620-F46800.svg)](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/)
[![Simulator image](https://img.shields.io/badge/ghcr.io-vllm--metrics--sim-2496ed.svg)](https://github.com/ChrisAdkin8/k8s-ai-observability/pkgs/container/vllm-metrics-sim)
[![Helm chart](https://img.shields.io/badge/ghcr.io-helm%20chart-0f1689.svg)](https://github.com/ChrisAdkin8/k8s-ai-observability/pkgs/container/charts%2Fk8s-ai-observability)
[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/k8s-ai-observability)](https://artifacthub.io/packages/search?repo=k8s-ai-observability)

# k8s-ai-observability

**Build and test GPU and LLM observability without a GPU.** A simulated NVIDIA GPU stack,
a simulated vLLM serving stack, and Prometheus + Grafana, on kind, EKS or GKE. No
hardware, no quota, no drivers, no model weights.

![Four time-series panels tracking utilisation, memory, temperature and power across
eight simulated GPUs](docs/gpu-dashboard.png)

## Try it

```sh
cd compose && docker compose up -d     # both boards on localhost:3000. ~1 min, no Kubernetes.
```

That's the fastest way to see it. To exercise Kubernetes itself (scheduling on
`nvidia.com/gpu`, the device plugin, ServiceMonitor discovery, the Grafana sidecar):

```sh
task local:up && task local:grafana    # deploy the kind cluster & full stack then open the grafana dashboards ~6 min.
```

![task local:up && task local:grafana: a kind cluster comes up, the stack installs, then verify.sh reports PASS
across every acceptance check and both Grafana boards render with live data](demo.gif)

The install is fast-forwarded. `verify.sh` runs at the recording's own pace, because the
checks are the point.

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
- **Two Grafana dashboards**, one `.json` each and never clicked into place, so a
  re-install reproduces them exactly. Both are in the catalog. Import by id into a Grafana
  you already run:
  [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) (GPU),
  [25620](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/) (vLLM).
- **An acceptance suite** ([`scripts/verify.sh`](scripts/verify.sh)) that asserts metrics
  are flowing, both boards render, and the alerts actually reach `firing`.
- **A weekly check against real vLLM**, which is what makes the claim above *checked*
  rather than asserted. [`check-vllm-buckets.py`](scripts/check-vllm-buckets.py) fetches
  upstream's own metric definitions and fails if a name or a bucket boundary has moved. It
  exists because two releases shipped the wrong bucket layout with **every test green**.
  A drifted boundary returns a confident, plausible, wrong percentile
  ([the full story](docs/versions.md#keeping-them-honest)).
- **The simulator as a container image**, so you can point your *own* dashboards at a
  realistic vLLM metric surface without cloning anything:
  `docker run --rm -p 9401:9401 ghcr.io/chrisadkin8/vllm-metrics-sim:latest`. Built from
  [`scripts/llm-sim.py`](scripts/llm-sim.py) rather than a copy of it, for `amd64` and
  `arm64` ([details](docs/llm-simulation.md#the-container-image)).
- **A path for clusters that already run Prometheus**, so panels blank for want of the
  `llm:*` recording rules are a two-minute fix rather than a reason to hand your monitoring
  stack to an install script. Either
  [bring your own Prometheus](docs/byo-prometheus.md) or the
  [Helm chart](charts/k8s-ai-observability/README.md).

![The LLM board: a healthy tenant and an overloaded one side by side across first-token
latency, queue depth, throughput, KV cache, prefix-cache reuse and the TTFT error-budget
burn rate](docs/llm-dashboard.png)

Two tenants sit either side of the 2s alert threshold, which is the point of running two
simulators. `sim-llama-3-8b-steady` answers in ~120 ms; `sim-llama-3-8b-saturated` reports
a p95 of 78s with 16 requests running and 160 queued behind it. Every panel aggregates
`by (model_name)`, so the overloaded tenant is never averaged into the healthy one.

> **The screenshot's healthy tenant reads ~480 ms rather than ~120 ms**, with its queue flat
> at zero throughout. Both are right: `~120 ms` is what the profile arithmetic models, while
> a live capture catches a batch that fills often enough for some requests to wait, which a
> 15s-sampled gauge misses and the TTFT histogram records in full. It is still an order of
> magnitude under the 2s threshold, which is what the panel exists to show.
> [The arithmetic](docs/llm-simulation.md#why-an-observed-steady-p95-runs-higher-than-01s).

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

## Install

> Needs `kubectl`, `helm` and `task`, plus `kind` + a container runtime for `local`.
> See [Prerequisites](#prerequisites). ⚠️ **Size your container runtime first**: colima's
> 2 CPU / 2 GiB default cannot run this. `task tools` checks the lot.

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
| `task chart` | assemble the [Helm chart](charts/README.md) into `dist/`, lint and render it |
| `task image` | build the simulator container image and smoke-test it |

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

The default path installs `kube-prometheus-stack`. **If you already run one**, this repo
does not need to touch it. Use either `install.sh --skip-monitoring` or the
[Helm chart](charts/k8s-ai-observability/README.md):

```sh
./scripts/install.sh local --skip-monitoring
./scripts/verify.sh   local --byo
```

⚠️ **Two labels decide whether that works, and both fail with no error at all**: no
scrape, no rule evaluation, empty boards, every object reporting itself as created. The
`release:` selector on the rules and ServiceMonitors, and the Grafana sidecar's discovery
label. **[docs/byo-prometheus.md](docs/byo-prometheus.md)** has both, what to set them to,
and why `verify.sh --byo` is what tells you.

### Cost and time

| | Time | Cost |
|--|--|--|
| `compose` | ~1 min | $0 |
| `local` | ~6 min end to end | $0 |
| `eks` / `gke` | ~20 min, node provisioning being the long pole | Billed hourly whether idle or not: control plane, 2 nodes, one NAT gateway, disks. Order of magnitude: tens of cents an hour. **Always `task <prefix>:destroy` when finished.** |

No GPU instances or GPU quota are used on any target.

### Faster cold builds on `local`

`kind delete cluster` throws away the node's image store, so every cold `task local:up`
re-pulls the whole stack: ~915 MB across 14 images, `grafana` alone 352 MB. Optional
pull-through caches sit on the kind network and outlive the cluster.

```sh
task cache:up          # once; they persist across kind delete
task cache:status      # what each one is holding
task cache:down        # stop them, keeping what they cached
task cache:purge       # stop them and discard it
```

The build that *populates* them is slower, not faster. Every cold build after it reads
from local disk instead of from five registries.

Off by default, and safe to stop at any point: with no cache running `kind-up.sh` writes
no mirror config, and containerd falls back to the real registry regardless.

⚠️ Unverified: the podman path. These have only ever been run against Docker.

## Opening the dashboards

Grafana stays ClusterIP, so there is no load balancer, no ingress and no cost.
`install.sh` prints both URLs when it finishes, and `task <prefix>:grafana` holds one
port-forward and opens both:

- <http://localhost:3000/d/gpu-sim-dcgm> for utilisation, memory, temperature and power
- <http://localhost:3000/d/llm-sim-overview> for first-token latency and its error budget,
  throughput, queue depth, KV cache and prefix-cache reuse

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

## Prerequisites

| Tool | Notes |
|------|-------|
| `kubectl`, `helm`, `curl`, `python3` | required on every target |
| [`kind`](https://kind.sigs.k8s.io) + Docker, colima or podman | local only, and all it needs |
| [`task`](https://taskfile.dev/docs/installation) | the front door. `brew install go-task/tap/go-task`; needs >= 3.32 |
| `terraform` >= 1.6, plus `aws` or `gcloud` | EKS/GKE only. GKE also needs `gke-gcloud-auth-plugin`, a [common silent miss](docs/gke.md#prerequisites) |
| `promtool` | optional, for `task rule-tests`. Ships inside the Prometheus release |
| `docker compose` | optional, for the [no-Kubernetes path](compose/). Nothing else on the host |
| `docker` | optional, for `task image` (build the simulator image) and `task chart` (which needs `helm` and network too) |

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
node-exporter and the chart's ~100 default rules, and puts Prometheus on 256Mi/512Mi.
Measured at 2.2 GiB in use, so it fits a small runtime. Allocate 4 GiB:

```sh
colima stop && colima start --cpu 4 --memory 4 --disk 40
LITE=1 task local:up
```

4 is the recommendation, not the floor. The floor is 3 and it now means what it says:
ask for 3 and the preflight accepts it, where it used to refuse. What it has never
been is *measured* at 3, which is why the command above still says 4.
[How the floors are read](docs/troubleshooting.md#the-floors-are-read-from-what-the-runtime-reports).

It keeps everything `local` exists to prove: ServiceMonitor discovery, PrometheusRule
evaluation, the Grafana sidecar import, and scheduling on `nvidia.com/gpu`. Both
dashboards render and every acceptance check in `verify.sh` still runs and still passes,
because none of them touches what was removed.
[`values-lite.yaml`](helm/kube-prometheus-stack/values-lite.yaml) lists exactly what
you give up.

## Why not just…

| | Why not |
|--|--|
| …use a real GPU node? | That is the goal, not the starting point. It costs money and quota, and you can't put one in CI. This repo's own CI stands the whole stack up on a free runner. |
| …use [kwok](https://kwok.sigs.k8s.io)? | kwok fakes the kubelet, so pods never really run. Excellent for scheduler and scale testing; it emits no DCGM or vLLM metrics, so there is nothing to point a dashboard at. |
| …use Grafana's TestData datasource? | Gives you panels with data, but no metric names, labels or PromQL, so nothing you build against it transfers to a real deployment. |
| …push synthetic series straight into Prometheus? | Gets you series without the Kubernetes half: no device plugin, no `nvidia.com/gpu` scheduling, no ServiceMonitor discovery, no operator wiring to get wrong. |
| …run real vLLM with a tiny model? | Real metrics, but it needs a GPU to be meaningful, and you cannot ask it for a saturated tenant on demand. Here, saturation is a line in a JSON profile. |

## Documentation

| Document | Covers |
|--|--|
| [docs/architecture.md](docs/architecture.md) | how the pieces fit at runtime, what the stack deliberately omits, install ordering, every EKS↔GKE difference, and the naming invariant to read before editing |
| [docs/observability.md](docs/observability.md) | where each metric comes from, reading the GPU board, nine PromQL queries that also work against real hardware, derived temperature and power, driving load |
| [docs/llm-simulation.md](docs/llm-simulation.md) | the vLLM simulator, load profiles, LLM alerts and the TTFT error budget |
| [docs/byo-prometheus.md](docs/byo-prometheus.md) | installing against a Prometheus you already run, and the two labels that fail silently |
| [charts/k8s-ai-observability/](charts/k8s-ai-observability/README.md) | installing the Helm chart: the BYO story, the two labels that fail silently, and where each invariant is caught. Ships with the chart |
| [charts/](charts/README.md) | changing the Helm chart: the build step and why it exists, the script path it mirrors, and what CI drives to failure |
| [tests/](tests/) | the rule tests and the simulator selftest: what they cover and how to add one |
| [compose/](compose/) | the no-Kubernetes path: what it shares with the cluster, and what it cannot exercise |
| [CONTRIBUTING.md](CONTRIBUTING.md) | the invariants that fail *silently* if broken, what to re-check when bumping a pinned version, and what is deliberately out of scope |
| [docs/ci.md](docs/ci.md) | what CI actually proves, the two kind legs and how they differ, why the check names are load-bearing, and how to reproduce a failure locally |
| [docs/usage.md](docs/usage.md) | running every phase as scripts instead of Task |
| [docs/versions.md](docs/versions.md) | every pinned version and the one place each is set |
| [docs/eks.md](docs/eks.md) / [docs/gke.md](docs/gke.md) | per-cloud specifics |
| [docs/troubleshooting.md](docs/troubleshooting.md) | empty panels, 404s, dead targets, warm-up |
| [ROADMAP.md](ROADMAP.md) | where the rig goes next: fault injection so the alerts fire against real broken states, an ingest path so load arrives from outside, llm-d-inference-sim as a graded second opinion, autoscaling with a fixed point, and what a fuller provisioned sandbox would have to prove |

## Contributing and support

- **A panel is empty, or something 404s.** Start with
  [docs/troubleshooting.md](docs/troubleshooting.md). Nearly every failure here surfaces the
  same way, and the table tells the causes apart.
- **Still stuck, or upstream has moved.**
  [Open an issue](https://github.com/ChrisAdkin8/k8s-ai-observability/issues/new/choose).
  There is a bug report form, and an upstream-drift form for a vLLM metric name or bucket
  boundary that has changed under the rig.
- **A security report.** Not a public issue: [SECURITY.md](SECURITY.md) has the private
  reporting link, and sets out which parts of a deliberately unauthenticated test rig are in
  scope and which are the point.
- **A change.** [CONTRIBUTING.md](CONTRIBUTING.md) covers the invariants that fail *silently*
  when broken, what to re-check when bumping a pinned version, and what is deliberately out
  of scope.
- **Conduct.** The [Contributor Covenant](CODE_OF_CONDUCT.md) applies to both.

## Licence

[MIT](LICENSE), © 2026 Chris Adkin.

Nothing third-party is vendored here. The Helm charts
([`fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator),
[`kube-prometheus-stack`](https://github.com/prometheus-community/helm-charts)) are pulled
from their own repos at install time, under their own licences. Both dashboards in
`manifests/dashboards/` are original to this repo rather than derived from a grafana.com
board, so the MIT grant above covers everything the repo actually ships.
