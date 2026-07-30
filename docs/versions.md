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
| vLLM metric surface mirrored | `scripts/config.sh` (`LLM_VLLM_VERSION`) | `v1` — names only; **buckets still from v0.6.x**, see below |
| promtool (rule tests) | `.github/workflows/ci.yml` (`PROMETHEUS_VERSION`) | `3.7.3` — CI only; locally any promtool works |
| LLM simulator base image | `manifests/llm/20-simulators.yaml` | `python:3.12-slim` |
| DCGM dashboard | `manifests/dashboards/gpu-sim-dcgm.json` | shipped in-repo (grafana.com board 12239 is an optional swap-in) |
| vLLM dashboard | `manifests/dashboards/llm-sim-overview.json` | shipped in-repo |
| aws provider | `terraform/eks/versions.tf` | `~> 6.55` |
| eks module | `terraform/eks/main.tf` | `~> 21.24.0` — patch-level on purpose; the reason is in the comment above it |
| vpc module | `terraform/eks/main.tf` | `~> 5.21.0` — patch-level on purpose, same reason |
| EKS node OS | `terraform/eks/main.tf` (`ami_type`) | AL2023 (`AL2023_x86_64_STANDARD`) |
| google provider | `terraform/gke` | `~> 7.40` |

The vLLM version matters more than it looks: the simulator copies that release's histogram
bucket boundaries, and `histogram_quantile()` accuracy depends entirely on bucket placement.
Change it and re-check `scripts/llm-sim.py`.

**This pin is split, and the two halves are not equally solid.**

- **Metric names — V1, verified.** Two series were renamed when the V1 engine landed
  (`gpu_cache_usage_perc` → `kv_cache_usage_perc`,
  `time_per_output_token_seconds` → `inter_token_latency_seconds`). Releases `0.1.0` and
  `0.2.0` shipped the superseded spellings. `METRIC_SURFACES` in `scripts/llm-sim.py` is
  the one place that mapping lives, and `--vllm-surface both` emits the old names
  alongside — see [llm-simulation.md](llm-simulation.md#which-engines-names).
- **Bucket boundaries — still v0.6.x, NOT re-verified.** `TTFT_BUCKETS`, `TPOT_BUCKETS`
  and `E2E_BUCKETS` are as transcribed for v0.6.x. Nothing here proves V1 kept them.

That second bullet is the more dangerous of the two, and deliberately so stated. A wrong
metric *name* fails loudly — the panel is blank and you go looking. A wrong *bucket
boundary* fails quietly: `histogram_quantile()` still returns a confident number, the
panel still draws a plausible line, and the SLO you derive from it is wrong only once it
meets real hardware. Re-check them against a live V1 `/metrics` dump before trusting a
percentile built here.
