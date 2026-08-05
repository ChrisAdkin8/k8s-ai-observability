# Building and changing the chart

This page is for people working **in this repo**. If you only want to install the chart,
everything you need is in
[`k8s-ai-observability/README.md`](k8s-ai-observability/README.md), which is the copy that
ships inside the published artefact.

⚠️ **Keep it that way.** That README is packaged into the OCI artefact and rendered on
Artifact Hub, where there is no repository: a `../../docs/...` link 404s, and `task chart`,
`dist/` and `scripts/install.sh` name things the reader cannot see. Anything repo-shaped
belongs here instead. This file is a sibling of the chart directory rather than inside it,
so `helm package` never picks it up.

---

## ⚠️ There is a build step. `helm install ./charts/...` does not work.

```sh
task chart                                        # assembles into gitignored dist/
helm install rig dist/charts/k8s-ai-observability \
  --set releaseLabel=<your monitoring release>
helm test rig --logs
```

`task chart` is not replaced by the published artefact; it is how you test a template edit.

**Why the step exists.** Helm's `.Files.Get` cannot read outside the chart directory, so a
chart under `charts/` cannot reference `manifests/dashboards/*.json` or
`manifests/alerts/*.yaml` where those files live. There were three ways out:

| | | |
|--|--|--|
| **(a)** | build step — assemble into gitignored `dist/` from the canonical files | **chosen** |
| (b) | symlinks under `charts/.../files/` | `helm package` and `git archive` follow them inconsistently across platforms |
| (c) | generate and **commit** the copies, with a CI check that they still match | the copies exist |

**(a) is the only option where the second copy never exists in the tree.** This repo
refuses second copies everywhere else — the DCGM surface contract, the dashboards, the
simulator image — because a drifted copy is invisible from the outside. The cost is real:
cloning and running `helm install ./charts/...` fails.
[`scripts/chart-build.py`](../scripts/chart-build.py) has the full argument.

> If you do try `./charts/...` directly, Helm complains about **missing dependencies**
> first, which is misleading — the real problem is the missing `files/`. Run
> `helm dependency build`, try again, and the chart's own assertion then tells you to build
> it properly.

**`scripts/llm-sim.py` is not in that list any more.** It used to be the hardest item —
executable code, the one file a drifted copy of would be genuinely dangerous. The
[published image](../docs/llm-simulation.md#the-container-image) removed it from the
problem entirely: a chart whose simulator Deployment references an image needs a **tag** in
`values.yaml`, not the file. That is the one structural difference between this chart and
[`scripts/install.sh`](../scripts/install.sh), which still mounts the script from a
ConfigMap.

**The profiles are not copied either**, for a different reason: the chart *templates* them
from `values.yaml`, so the numbers are genuinely reachable. A verbatim copy would freeze
every one of them at its default while appearing configurable.

---

## The chart against the script path

[`scripts/install.sh`](../scripts/install.sh) runs five assertions before it creates
anything, and a `helm install` runs none of them. Reproducing that net is a first-class
requirement of the chart, not a nicety. This table maps
[`CONTRIBUTING.md`'s invariants table](../CONTRIBUTING.md) onto both paths, so the two
cannot drift silently.

| Invariant | `install.sh` | The chart |
|--|--|--|
| dashboard filename vs the `uid` inside it | `assert_dashboard_contract` | `_assertions.tpl` |
| every board parses as JSON | `assert_dashboard_contract` | `_assertions.tpl`, and again in `chart-build.py` |
| the two LLM `model_name`s are distinct and non-empty | `assert_llm_contract` | `_assertions.tpl` |
| node-pool label key matches the operator's topology | `assert_gpu_contract` | `_assertions.tpl` |
| node-pool **name** is one of the topology pools | `assert_gpu_contract` | `_assertions.tpl` |
| namespaces are non-empty | `assert_manifest_namespaces` | `_assertions.tpl` |
| the rules were actually extracted | — | `_assertions.tpl` |
| **the capacity arithmetic still separates the tenants** | — *(profiles are static files there)* | `_assertions.tpl` |
| value types and ranges | — | `values.schema.json` |
| Prometheus Operator CRDs exist | `assert_monitoring_crds` | `helm test` |
| **`releaseLabel` matches a real `ruleSelector`** | — *(cannot: it is another chart's object)* | `helm test` |
| dashboard ConfigMaps carry the sidecar label | — | `helm test` |
| a node carries the GPU pool label | `assert_kind_contract` / `assert_terraform_contract` | `helm test` |
| `nvidia.com/gpu` is advertised | `verify.sh` check 1 | `helm test` |
| the simulators serve the `vllm:` surface | `verify.sh` L1–L9 | `helm test` |

The **capacity** row is the one with no `install.sh` counterpart, and it is new hazard
rather than an oversight there: the script path's profiles are static files, so nobody can
set an arrival rate that stops the two tenants straddling the 2s alert threshold. Making
them templatable created that possibility, so the chart refuses to render it.

⚠️ **If you add a render-time assertion, add its negative case to the `chart` CI job.** That
job drives every assertion to its failure and fails if a broken input still renders — see
[`CONTRIBUTING.md`](../CONTRIBUTING.md) and [`docs/ci.md`](../docs/ci.md).

The chart does **not** replace `install.sh`. That stays the source of truth for install
ordering and the wrong-context guard, and remains what CI exercises end to end.

---

## Reaching the boards when your release is not called `kube-prometheus-stack`

[`scripts/grafana.sh`](../scripts/grafana.sh) and
[`scripts/prometheus.sh`](../scripts/prometheus.sh) build the Service name from the release
(`<release>-grafana`, `<release>-prometheus`), which is a chart convention rather than a
Kubernetes one. They read it from the environment:

```sh
KPS_RELEASE=my-monitoring ./scripts/grafana.sh local
KPS_RELEASE=my-monitoring ./scripts/verify.sh local --byo
```

## Versions move in two places

`Chart.yaml` carries the chart `version`, its `appVersion`, and the two subchart pins that
`scripts/config.sh` also holds — Helm cannot read a shell variable, so those pins genuinely
exist twice and `chart-build.py` cross-checks them by dependency name. The chart README's
`helm install --version` is checked against `Chart.yaml` by
[`scripts/check-doc-claims.py`](../scripts/check-doc-claims.py), because a stale pin there
installs an old published chart rather than failing. See
[`docs/releasing.md`](../docs/releasing.md) for the order these move in.

## Uninstalling the script path

`helm uninstall rig` is scoped by release ownership. The manual-cleanup trap it avoids —
selecting on `grafana_dashboard=1` and taking kube-prometheus-stack's own boards with it —
is handled for the script path by [`scripts/teardown.sh`](../scripts/teardown.sh). Both
paths carry the same labels, so they produce identical objects.
