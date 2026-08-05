# k8s-ai-observability — Helm chart

GPU and vLLM observability simulation for **a cluster that already runs Prometheus**.

Both boards are published in the Grafana catalog
([25618](https://grafana.com/grafana/dashboards/25618), [25620](https://grafana.com/grafana/dashboards/25620)),
so people arrive from there, import a board, and find the panels blank for want of the
`llm:*` recording rules. This chart is the fix: it installs the simulators, workloads,
rules and dashboards and **leaves your monitoring stack alone**.

---

## Install

```sh
helm install rig oci://ghcr.io/chrisadkin8/charts/k8s-ai-observability \
  --version 0.2.5 \
  --set releaseLabel=<your monitoring release>
helm test rig --logs                              # ← do not skip this
```

> ⚠️ **Take the newest version.** Registry versions are immutable, so earlier ones stay
> published with their faults rather than being replaced:
>
> | | |
> |--|--|
> | `0.2.0` | `helm test --logs` exits 1 even when every precondition passes — Helm tries to read pod logs from the test's ServiceAccount. A green result reported as red, on the command this page tells you to run. |
> | `0.2.1` | fixes that, but the test greps an 18.7 kB scrape through a pipe, which under `pipefail` can report a present metric as missing. |
> | `0.2.2` onwards | both fixed. |

### What it installs

| | |
|--|--|
| 2 dashboard `ConfigMap`s | the GPU and vLLM boards, carrying the Grafana sidecar label |
| 2 `PrometheusRule`s | 19 recording rules and 9 alerts across both domains |
| 2 `ServiceMonitor`s | for the LLM simulators and the fake DCGM exporter |
| LLM simulation | two tenants, one healthy and one deliberately overloaded, plus a Service and their profile `ConfigMap`s |
| 3 GPU workloads | `gpu-idle`, `gpu-steady`, `gpu-busy` |
| a `helm test` hook | the preconditions below, checked against your live cluster |

Two optional subcharts: `fakeGpuOperator` (**on** by default — without it nothing
advertises `nvidia.com/gpu`) and `kubePrometheusStack` (**off** by default — `true` serves
a greenfield cluster instead of a BYO one).

---

## ⚠️ The two settings that fail with no error at all

This is the single most likely way this chart appears broken, and neither produces a
message anywhere.

| Value | If it is wrong | Symptom |
|--|--|--|
| `releaseLabel` | your Prometheus never adopts the `PrometheusRule`s or `ServiceMonitor`s | rules never evaluate, scrapes never happen, every `llm:*` and derived-DCGM panel is empty |
| `grafana.dashboardLabel` | the Grafana sidecar never imports the dashboard `ConfigMap`s | the boards do not exist; `/d/llm-sim-overview` 404s |

Every object involved reports itself successfully created. **`helm test` is what says so
out loud** — run it.

### Getting `releaseLabel` right

Upstream kube-prometheus-stack defaults `ruleSelectorNilUsesHelmValues` and its two
siblings to **true**, which makes its selector `release=<its own release name>`. So:

```sh
helm install rig oci://ghcr.io/chrisadkin8/charts/k8s-ai-observability \
  --set releaseLabel=my-monitoring
```

You have two possible fixes and should know both: set `releaseLabel`, or set those three
values `false` on your side — and the second is often not yours to change.

---

## ⚠️ One prerequisite this chart cannot satisfy

**A chart cannot label nodes.** At least one node must already carry the GPU node-pool
label, or the fake operator watches a pool nothing belongs to — a green install with
**zero GPUs**, every sample workload `Pending`, and a blank GPU board:

```sh
kubectl label node <node> run.ai/simulated-gpu-node-pool=default
```

The node label value, the fake operator's topology pool name and the workloads' selector
must all agree. The chart checks the two halves it can see at render time and the third in
`helm test`.

You also need the Prometheus Operator CRDs (`PrometheusRule`, `ServiceMonitor`) already
present, which any kube-prometheus-stack install gives you. `helm test` checks that too.

---

## Where each invariant is caught

A `helm install` runs no preflight of its own, and a chart that installs cleanly and
produces an empty dashboard is worse than no chart, because the failure arrives later and
looks like your fault. So the checks are built in, half at render time and half live:

| Invariant | Caught by | When |
|--|--|--|
| dashboard filename vs the `uid` inside it | `fail` in `_assertions.tpl` | **`--dry-run`** |
| every board parses as JSON | `fail` in `_assertions.tpl` | **`--dry-run`** |
| the two LLM `model_name`s are distinct and non-empty | `fail` in `_assertions.tpl` | **`--dry-run`** |
| node-pool label key matches the operator's topology | `fail` in `_assertions.tpl` | **`--dry-run`** |
| node-pool **name** is one of the topology pools | `fail` in `_assertions.tpl` | **`--dry-run`** |
| namespaces are non-empty | `fail` in `_assertions.tpl` | **`--dry-run`** |
| the rules were actually extracted | `fail` in `_assertions.tpl` | **`--dry-run`** |
| **the capacity arithmetic still separates the tenants** | `fail` in `_assertions.tpl` | **`--dry-run`** |
| value types and ranges | `values.schema.json` | **`--dry-run`** |
| Prometheus Operator CRDs exist | `helm test` | live |
| **`releaseLabel` matches a real `ruleSelector`** | `helm test` | live |
| dashboard `ConfigMap`s carry the sidecar label | `helm test` | live |
| a node carries the GPU pool label | `helm test` | live |
| `nvidia.com/gpu` is advertised | `helm test` | live |
| the simulators serve the `vllm:` surface | `helm test` | live |

`--dry-run` means `helm install --dry-run` or `helm template` catches it before anything
is created. **`releaseLabel` is the one that cannot be checked at render time** — it names
an object belonging to another release, so only a live cluster can answer it.

⚠️ **`helm test` is opt-in.** `helm install` does not run it, and the two silent selectors
are *only* checked there. That is a genuine weakness of this design, stated rather than
hidden; `NOTES.txt` tells you to run it in the imperative for that reason.

---

## Values

Full annotated list in [`values.yaml`](values.yaml). The ones that matter:

| | Default | |
|--|--|--|
| `releaseLabel` | `kube-prometheus-stack` | ⚠️ the `release:` selector. Wrong = silent |
| `grafana.dashboardLabel` | `grafana_dashboard` | ⚠️ the sidecar key. Wrong = silent |
| `kubePrometheusStack.enabled` | `false` | the premise of this chart. `true` serves a greenfield cluster |
| `fakeGpuOperator.enabled` | `true` | without it nothing advertises `nvidia.com/gpu` |
| `llm.image.tag` | `""` → `Chart.appVersion` | ⚠️ leave empty. See below |
| `llm.steady.modelName` / `llm.saturated.modelName` | — | ⚠️ an identity, must be distinct |
| `llm.profile.*` | — | ⚠️ interlocking numbers; see below |
| `nodePoolLabelKey` / `nodePoolName` | `run.ai/simulated-gpu-node-pool` / `default` | must match a real node label |
| `workloads.enabled` | `true` | the three sample GPU Deployments |

### ⚠️ `llm.image.tag` — leave it empty

Empty means "use `Chart.appVersion`", which is what keeps the chart and the published
simulator image in step automatically. Pinning it by hand is exactly how it goes stale, and
a stale tag is not a visible failure: **the chart installs cleanly and runs an old
simulator.**

### ⚠️ `llm.profile.*` — these numbers interlock

```
itl_full = baseItlSeconds x 1.5                                    = 0.0225
capacity = maxConcurrency / (baseTtftSeconds + genMean x itl_full) = 2.74 rps
```

`steady` at 1.8 rps sits at 0.66× capacity; `saturated` at 6.0 rps sits at 2.19×, so its
queue fills to `maxInFlight - maxConcurrency = 160` and TTFT plateaus at ~58s. The 2s
`LLMHighTTFT` threshold sits between them, and that separation is the entire demonstration.

Change one and the alert threshold and every rule expectation have to be re-derived. The
chart refuses to render values where the tenants stop straddling capacity — it cannot
re-derive the rest for you, but it will not let you silently lose the thing the rig exists
to show.

---

## What this chart deliberately does not do

- **Template Terraform**, or any cloud-specific resource.
- **Create the monitoring namespace** by default. On a BYO cluster it already exists and is
  someone else's; creating it would make `helm uninstall` delete their namespace and take
  their Prometheus with it. `createMonitoringNamespace=true` for greenfield.
- **Tune anything to real hardware.** These metrics are synthetic: names, types and
  histogram bucket boundaries transfer to real vLLM, absolute values do not.

## Uninstalling

```sh
helm uninstall rig
```

Removes what the chart created and nothing else. The dashboard `ConfigMap`s carry
`app.kubernetes.io/part-of=gpu-sim-dashboards` as well as the sidecar label, so a manual
cleanup can select on ownership rather than on `grafana_dashboard=1` — which would take the
several boards kube-prometheus-stack ships with it.

---

Source, issues and the rest of the rig:
**<https://github.com/ChrisAdkin8/k8s-ai-observability>**. Building the chart from a clone
is a different path with its own prerequisites, documented at
[`charts/README.md`](https://github.com/ChrisAdkin8/k8s-ai-observability/blob/main/charts/README.md).
