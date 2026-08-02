# Pinned versions

Every version this repo pins, and the single place each one is set. Changing a value
anywhere other than the listed source will drift and, in most cases, fail an assertion
in `scripts/config.sh` at install time.


| Component | Where | Value |
|-----------|-------|-------|
| Kubernetes | `terraform/modules/contract` + `scripts/config.sh` | `1.36` — ages out; the three ceilings are documented in the contract module |
| kind node image (local) | `kind/gpu-sim.yaml` | `kindest/node:v1.36.1` — asserted to stay on the `K8S_VERSION` line |
| kube-prometheus-stack chart | `scripts/config.sh` | `87.17.0` |
| fake-gpu-operator chart | `scripts/config.sh` | `0.0.59` |
| vLLM metric surface mirrored | `scripts/config.sh` (`LLM_VLLM_VERSION`) | `v1` — names and buckets, drift-checked weekly, see below |
| promtool (rule tests) | `.github/workflows/ci.yml` (`PROMETHEUS_VERSION`) | `3.7.3` — CI only; locally any promtool works |
| LLM simulator base image (cluster) | `manifests/llm/20-simulators.yaml` | `python:3.12-slim` — the ConfigMap-mounted path, which is how the rig itself runs it |
| LLM simulator base image (published) | `Dockerfile` | `python:3.12-slim` — the same base for `ghcr.io/<owner>/vllm-metrics-sim` |
| Published simulator image tag | `charts/.../Chart.yaml` (`appVersion`) | the repo's release tag. The chart's `llm.image.tag` defaults to it; see below |
| Helm chart version | `charts/.../Chart.yaml` (`version`) | `0.1.0` — moves independently of `appVersion` |
| Helm (CI) | `.github/workflows/ci.yml` (`HELM_VERSION`) | `3.21.3` — **v3, not the v4 line.** Helm 4 is a major this repo has not been validated against, and CI should exercise what users run |
| `kubectl` image for `helm test` | `charts/.../values.yaml` (`tests.image`) | `bitnami/kubectl:1.31` |
| DCGM dashboard | `manifests/dashboards/gpu-sim-dcgm.json` | shipped in-repo, published as grafana.com [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) (board 12239 is an optional swap-in) |
| vLLM dashboard | `manifests/dashboards/llm-sim-overview.json` | shipped in-repo, published as grafana.com [25620](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/) |
| aws provider | `terraform/eks/versions.tf` | `~> 6.55` |
| eks module | `terraform/eks/main.tf` | `~> 21.24.0` — patch-level on purpose; the reason is in the comment above it |
| vpc module | `terraform/eks/main.tf` | `~> 5.21.0` — patch-level on purpose, same reason |
| EKS node OS | `terraform/eks/main.tf` (`ami_type`) | AL2023 (`AL2023_x86_64_STANDARD`) |
| google provider | `terraform/gke` | `~> 7.40` |

## ⚠️ Two pins exist in two places, and could not be made to exist in one

`scripts/config.sh` pins **kube-prometheus-stack** and **fake-gpu-operator** for the script
install path. `charts/k8s-ai-observability/Chart.yaml` pins the same two as chart
dependencies. Helm cannot read a shell variable and the shell cannot read `Chart.yaml`, so
unlike the dashboards and the rule files — which reach the chart through `task chart`, and
are therefore never committed twice — this is genuinely one number written down twice.

**Bump one and not the other and both installs still succeed**, while the two paths deploy
different versions of the same operator. `verify.sh` passes on each. CI passes on each. The
only symptom is that a chart bump verified through one path was never verified through the
other.

The `fake-gpu-operator` pin is the dangerous half. `config.sh` records that this repo
hard-codes facts true of `0.0.59` specifically — the exporter's three series, the
ServiceMonitor's selector, and the labels the dashboards and rules join on — and none of
those has a plan-time check.

So the divergence fails the build instead: `scripts/chart-build.py` compares the two on
every `task chart` and in the `chart` CI job, matching each dependency by name rather than
by position (the two blocks are structurally identical, and a loose match would compare one
against the other's pin and pass on a coincidence). Move them together.

## The vLLM surface

The vLLM version matters more than it looks: the simulator copies that release's histogram
bucket boundaries, and `histogram_quantile()` accuracy depends entirely on bucket placement.
Change it and re-check `scripts/llm-sim.py`.

**This pin has two halves, and both had drifted.** Releases `0.1.0` and `0.2.0` mirrored
v0.6.x while the V1 engine had moved on.

- **Metric names — V1.** Two series were renamed
  (`gpu_cache_usage_perc` → `kv_cache_usage_perc`,
  `time_per_output_token_seconds` → `inter_token_latency_seconds`). `METRIC_SURFACES` in
  `scripts/llm-sim.py` is the one place that mapping lives, and `--vllm-surface both`
  emits the old names alongside — see
  [llm-simulation.md](llm-simulation.md#which-engines-names).
- **Bucket boundaries — V1.** `TTFT_BUCKETS`, `TPOT_BUCKETS` and `E2E_BUCKETS` are
  transcribed verbatim from `vllm/v1/metrics/loggers.py`.

The second was the more dangerous, and it is worth being precise about why. A wrong
metric *name* fails loudly — the panel is blank and you go looking. A wrong *bucket
boundary* fails quietly: `histogram_quantile()` still returns a confident number, the
panel still draws a plausible line, and the SLO you derive from it is wrong only once it
meets real hardware.

TTFT is the one that had actually broken. Its first sixteen boundaries were identical in
v0.6.x and V1, so nothing looked wrong — but V1 replaced the entire tail:

```
both:  0.001 0.005 0.01 0.02 0.04 0.06 0.08 0.1 0.25 0.5 0.75 1.0 2.5 5.0 7.5 10.0
v0.6:  │ 15   20   30   45   60   90  120
V1:    │ 20   40   80  160  640 2560
```

The saturated tenant sits at ~58s — inside that tail. Same simulated latency, different
reported p95 (59.25 → 78), purely from the resolution it is measured at. `TPOT_BUCKETS`
was a strict prefix of V1's and so was never wrong at the operating point; `E2E_BUCKETS`
gained sub-second resolution (`0.3/0.5/0.8`) it previously had none of.

## Keeping them honest

`scripts/check-vllm-buckets.py` fetches `loggers.py` and checks **both** halves of the
surface: each of the three bucket lists still appears there verbatim, and every `vllm:`
name this repo emits is still declared upstream. It runs weekly in CI beside the
Helm-chart drift detection — **scheduled and dispatch only**, so an upstream release
never reddens a contributor's pull request.

It exists because nothing else could have caught this. Every other test in this repo
reads the simulator, and the simulator was perfectly consistent with itself — it was
consistent with the wrong thing. A fault in a relationship to something *outside* the
suite needs a check that points outside it.

```sh
python3 scripts/check-vllm-buckets.py   # 0 in sync · 1 drift · 2 could not check
```

The two directions are **not** treated alike, and the asymmetry is the point:

| | Means | Exit |
|--|--|--|
| We emit a name upstream no longer declares | drift — the rename case that cost two releases | **1** |
| Upstream declares a name we do not emit | a gap, printed in full | **0** |

Upstream declares around 40 `vllm:` metrics and this simulator emits 15 of them, so the
gap list is long by design. Reddening a weekly run for each metric vLLM adds would train
everyone to ignore it; printing the list keeps the distance visible, which is the thing
that was previously invisible. It is also the list to pick from when closing one.

The `_total` rule is the subtle part. Upstream declares counters *without* the suffix and
the Prometheus client appends it at exposition time — so our `vllm:prompt_tokens_total`
and upstream's `vllm:prompt_tokens` are one metric — but `vllm:iteration_tokens_total` is
declared *with* it, as a histogram. A blanket strip would report "in sync" on a name with
no upstream counterpart at all, which the real run cannot reveal because it prints a
plausible answer either way. So the rule is unit-tested against a committed fixture,
`tests/fixtures/upstream-vllm-metric-names.txt`, on every push:

```sh
python3 scripts/check-vllm-buckets.py --selftest   # no network
```

Drift means updating `scripts/llm-sim.py` and then re-deriving the expected values
in `tests/rules/llm-rules_test.yaml`, which are pinned to specific boundaries on purpose
and will fail until you do.
