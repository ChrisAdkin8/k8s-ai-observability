# k8s-ai-observability — Helm chart

GPU and vLLM observability simulation for **a cluster that already runs Prometheus**.

Both boards are published in the Grafana catalog
([25618](https://grafana.com/grafana/dashboards/25618), [25620](https://grafana.com/grafana/dashboards/25620)),
so people arrive from there, import a board, and find the panels blank for want of the
`llm:*` recording rules. Until this chart, the only route was
[`scripts/install.sh`](../../scripts/install.sh), which installs kube-prometheus-stack over
the top of whatever you already run — which nobody with a production monitoring stack will
do. This chart is the other route: it installs the simulators, workloads, rules and
dashboards and **leaves your monitoring stack alone**.

---

## ⚠️ There is a build step. `helm install ./charts/...` does not work.

```sh
task chart                                        # assembles into gitignored dist/
helm install rig dist/charts/k8s-ai-observability \
  --set releaseLabel=<your monitoring release>
helm test rig --logs                              # ← do not skip this
```

**Why.** Helm's `.Files.Get` cannot read outside the chart directory, so a chart under
`charts/` cannot reference `manifests/dashboards/*.json` or `manifests/alerts/*.yaml` where
those files live. There were three ways out:

| | | |
|--|--|--|
| **(a)** | build step — assemble into gitignored `dist/` from the canonical files | **chosen** |
| (b) | symlinks under `charts/.../files/` | `helm package` and `git archive` follow them inconsistently across platforms |
| (c) | generate and **commit** the copies, with a CI check that they still match | the copies exist |

**(a) is the only option where the second copy never exists in the tree.** This repo
refuses second copies everywhere else — the DCGM surface contract, the dashboards, the
simulator image — because a drifted copy is invisible from the outside. The cost is this
step, and it is a real cost: cloning and running `helm install ./charts/...` fails.
`scripts/chart-build.py` has the full argument.

> If you do try `./charts/...` directly, Helm complains about **missing dependencies**
> first, which is misleading — the real problem is the missing `files/`. Run
> `helm dependency build`, try again, and the chart's own assertion then tells you to build
> it properly.

**`scripts/llm-sim.py` is not in that list any more.** It used to be the hardest item —
executable code, the one file a drifted copy of would be genuinely dangerous. The
[published image](../../docs/llm-simulation.md#the-container-image) removed it from the
problem entirely: a chart whose simulator Deployment references an image needs a **tag** in
`values.yaml`, not the file. That is the one structural difference between this chart and
`install.sh`, which still mounts the script from a ConfigMap.

**The profiles are not copied either**, for a different reason: the chart *templates* them
from `values.yaml`, so the numbers are genuinely reachable. A verbatim copy would freeze
every one of them at its default while appearing configurable.

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
helm install rig dist/charts/k8s-ai-observability --set releaseLabel=my-monitoring
```

You have two possible fixes and should know both: set `releaseLabel`, or set those three
values `false` on your side — and the second is often not yours to change.

### Reaching the boards when your release is not called `kube-prometheus-stack`

`scripts/grafana.sh` and `scripts/prometheus.sh` build the Service name from the release
(`<release>-grafana`, `<release>-prometheus`), which is a chart convention rather than a
Kubernetes one. They read it from the environment:

```sh
KPS_RELEASE=my-monitoring ./scripts/grafana.sh local
KPS_RELEASE=my-monitoring ./scripts/verify.sh local --byo
```

Chart users hit this first, before anything else in this file matters.

---

## ⚠️ One prerequisite this chart cannot satisfy

**A chart cannot label nodes.** At least one node must already carry the GPU node-pool
label, or the fake operator watches a pool nothing belongs to — a green install with
**zero GPUs**, every sample workload `Pending`, and a blank GPU board:

```sh
kubectl label node <node> run.ai/simulated-gpu-node-pool=default
```

This is the [three-way naming invariant](../../docs/architecture.md#the-naming-invariant-read-before-editing):
the node label value, the fake operator's topology pool name and the workloads' selector
must all agree. The chart checks the two halves it can see at render time and the third in
`helm test`.

---

## Where each invariant is caught

`scripts/install.sh` runs five assertions before it creates anything, and a `helm install`
runs none of them. Reproducing that net is a first-class requirement of this chart, not a
nicety — a chart that installs cleanly and produces an empty dashboard is worse than no
chart, because the failure arrives later and looks like your fault.

This table maps [`CONTRIBUTING.md`'s invariants table](../../CONTRIBUTING.md) onto where
each row is enforced here, so the two cannot drift silently.

| Invariant | `install.sh` | This chart | When |
|--|--|--|--|
| dashboard filename vs the `uid` inside it | `assert_dashboard_contract` | `fail` in `_assertions.tpl` | **`--dry-run`** |
| every board parses as JSON | `assert_dashboard_contract` | `fail` in `_assertions.tpl`, and again in `chart-build.py` | **`--dry-run`** |
| the two LLM `model_name`s are distinct and non-empty | `assert_llm_contract` | `fail` in `_assertions.tpl` | **`--dry-run`** |
| node-pool label key matches the operator's topology | `assert_gpu_contract` | `fail` in `_assertions.tpl` | **`--dry-run`** |
| node-pool **name** is one of the topology pools | `assert_gpu_contract` | `fail` in `_assertions.tpl` | **`--dry-run`** |
| namespaces are non-empty | `assert_manifest_namespaces` | `fail` in `_assertions.tpl` | **`--dry-run`** |
| the rules were actually extracted | — | `fail` in `_assertions.tpl` | **`--dry-run`** |
| **the capacity arithmetic still separates the tenants** | — *(profiles are static files there)* | `fail` in `_assertions.tpl` | **`--dry-run`** |
| value types and ranges | — | `values.schema.json` | **`--dry-run`** |
| Prometheus Operator CRDs exist | `assert_monitoring_crds` | `helm test` | live |
| **`releaseLabel` matches a real `ruleSelector`** | — *(cannot: it is another chart's object)* | `helm test` | live |
| dashboard ConfigMaps carry the sidecar label | — | `helm test` | live |
| a node carries the GPU pool label | `assert_kind_contract` / `assert_terraform_contract` | `helm test` | live |
| `nvidia.com/gpu` is advertised | `verify.sh` check 1 | `helm test` | live |
| the simulators serve the `vllm:` surface | `verify.sh` L1–L9 | `helm test` | live |

The **capacity** row is the one with no `install.sh` counterpart, and it is new hazard
rather than an oversight there: the script path's profiles are static files, so nobody can
set an arrival rate that stops the two tenants straddling the 2s alert threshold. Making
them templatable created that possibility, so the chart refuses to render it.

⚠️ **`helm test` is opt-in.** `helm install` does not run it, and the two silent selectors
are *only* checked there. That is a genuine weakness of this design, stated rather than
hidden; the chart's `NOTES.txt` tells you to run it in the imperative for that reason.

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
simulator.** `task chart` and the publish workflow both cross-check it rather than trusting
it, which is the only reason that coupling is safe.

### ⚠️ `llm.profile.*` — these numbers interlock

```
itl_full = baseItlSeconds x 1.5                                    = 0.0225
capacity = maxConcurrency / (baseTtftSeconds + genMean x itl_full) = 2.74 rps
```

`steady` at 1.8 rps sits at 0.66× capacity; `saturated` at 6.0 rps sits at 2.19×, so its
queue fills to `maxInFlight - maxConcurrency = 160` and TTFT plateaus at ~58s. The 2s
`LLMHighTTFT` threshold sits between them, and that separation is the entire demonstration.

Change one and the threshold, `verify.sh`'s L3b bound and every promtool expectation have
to be re-derived. The chart refuses to render values where the tenants stop straddling
capacity — it cannot re-derive the rest for you, but it will not let you silently lose the
thing the rig exists to show.

---

## What this chart deliberately does not do

- **Replace `scripts/install.sh`.** That stays the source of truth for install ordering and
  the wrong-context guard, and remains what CI exercises end to end.
- **Publish to Artifact Hub or a `helm repo`.** Separate, mostly-administrative work.
- **Template the Terraform**, or any cloud-specific resource.
- **Create the monitoring namespace** by default. On a BYO cluster it already exists and is
  someone else's; creating it would make `helm uninstall` delete their namespace and take
  their Prometheus with it. `createMonitoringNamespace=true` for greenfield.

## Uninstalling

```sh
helm uninstall rig
```

Removes what the chart created and nothing else. The dashboard ConfigMaps carry
`app.kubernetes.io/part-of=gpu-sim-dashboards` as well as the sidecar label, so a manual
cleanup can select on ownership rather than on `grafana_dashboard=1` — which would take the
several boards kube-prometheus-stack ships with it. That is the trap
[`teardown.sh`](../../scripts/teardown.sh) avoids for the script path; `helm uninstall` is
scoped by release ownership and does not have it, but the labels are carried so both paths
produce identical objects.
