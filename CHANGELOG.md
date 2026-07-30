# Changelog

Notable changes to this repo, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**What a version means here.** This is a rig, not a library — nothing imports it, so
"breaking" is about a cluster you already have rather than an API:

| | Breaks | Examples |
|--|--|--|
| **MAJOR** | an existing install, or a re-apply against it | the three-way naming invariant, Terraform input names or defaults, namespaces, dashboard `uid`s, metric or recording-rule names |
| **MINOR** | nothing | a new dashboard, alert, load profile, target or check |
| **PATCH** | nothing | fixes, docs, and pinned-version bumps that change none of the above |

Pinned upstream versions live in [docs/versions.md](docs/versions.md); a bump there is
worth an entry below whenever it changes behaviour rather than just a number.

> **No git tags exist yet**, so there are no release-comparison links in this file — a
> confident link to a 404 is the failure mode this repo goes out of its way to avoid
> elsewhere. Tag the baseline to make it pinnable:
> `git tag -a v0.1.0 067b4c4 -m "Initial public release" && git push origin v0.1.0`

## [Unreleased]

### Added

- **Unit tests for every recording rule and alert**, run with `promtool` in about a
  second with no cluster and no network: `task rule-tests` (or `make rule-tests`, or
  `./scripts/rule-tests.sh`). `scripts/rule-tests.sh` extracts the `spec:` block from
  each PrometheusRule custom resource into a plain rule file, then runs `promtool check
  rules` and `promtool test rules` over it, so the applied manifest stays the single
  source of truth rather than being generated from something else.

  These cover what `scripts/verify.sh` structurally cannot. A live check only ever sees
  the alerts the shipped workloads happen to drive — `GPUHighUtilization` and
  `LLMHighTTFT` — and only from the firing side. The new tests assert both sides of every
  threshold, the derived temperature and power arithmetic, the `by (model_name)`
  aggregation that keeps the two tenants apart, the self-disabling `unless on (UUID)`
  guard, and the two alerts nothing on the rig ever reaches (`LLMKVCacheSaturated`,
  `GPUMetricsAbsent` / `LLMMetricsAbsent`). See [tests/](tests/), which also documents
  the two properties these tests cannot pin and why.

- `promtool` is now reported by `task tools` (optional — nothing else needs it), and
  pinned for CI as `PROMETHEUS_VERSION` in `.github/workflows/ci.yml`.

- **"Why not just…"** in the README: honest comparisons against a real GPU node, kwok,
  Grafana's TestData datasource, pushing synthetic series straight into Prometheus, and
  running real vLLM with a small model.

- **"Reading the GPU board"** in [docs/observability.md](docs/observability.md) —
  why four traces move while the rest sit flat at zero, and why the memory panel shows
  two bands with nothing between them.

### Changed

- The README is restructured around what a first-time reader needs: a dashboard screenshot
  and a runnable command above the fold, the fidelity caveat reframed from a disclaimer
  into a *transfers / does not transfer* table below the pitch, and a cost-and-time table.
  Panel-level explanation moved to `docs/observability.md`.

- CI's fast job is now `selftest + rule tests + shell syntax`.

## 0.1.0 — 2026-07-30

Initial public release (untagged; `067b4c4`). Build and test GPU and LLM observability
without a GPU.

### Added

- **GPU simulation** — [`run-ai/fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator)
  advertises `nvidia.com/gpu` through the device plugin's `Allocate()` response, injects a
  fake `nvidia-smi`, and emits DCGM-format metrics, so the standard NVIDIA observability
  stack works unmodified.
- **LLM serving simulation** — `scripts/llm-sim.py`, a dependency-free standard-library
  Python file emitting the real vLLM metric surface: names, types and histogram bucket
  boundaries. Two tenants run side by side, one healthy and one deliberately saturated,
  driven by JSON profiles that can be edited without restarting a pod. `--selftest`
  validates the exposition with no cluster.
- **Observability stack** — `kube-prometheus-stack`, two Grafana dashboards shipped as
  sidecar ConfigMaps under stable `uid`s (`gpu-sim-dcgm`, `llm-sim-overview`), and
  recording rules and alerts across both domains. No grafana.com egress at install time.
  Temperature and power are synthesised from utilisation by recording rules and recorded
  under the real DCGM names, because the fake exporter emits neither.
- **Three targets from one definition** — `local` (kind), `eks` and `gke`, with per-target
  tasks defined once in `taskfiles/target.yml` and included three times so they cannot
  drift apart. `local` needs no cloud account, credentials or spend, and runs the same
  manifests, pinned charts and acceptance checks as the clouds.
- **Two front doors** — a Taskfile and a Makefile, both thin wrappers over `scripts/`,
  which remains the source of truth for install ordering and the wrong-context guard.
- **Acceptance suite** — `scripts/verify.sh`, GPU checks 1-5 (including 4b/4c/4d) and LLM
  checks L1-L6: metrics flowing, both boards served to an unauthenticated request, and the
  alerts genuinely reaching `firing`.
- **Drift assertions** — `scripts/config.sh` aborts the install when its constants
  disagree with the static manifests, the Helm values, `kind/gpu-sim.yaml` or the
  Terraform outputs. Each guards a failure that would otherwise be a green install with
  zero GPUs, or a confident dashboard link to a Grafana 404.
- **CI** — a fast job plus the full stack stood up on kind on a runner that has never seen
  a GPU, running the advertised `task local:up` end to end including every acceptance
  check, with a diagnostics bundle on failure and weekly upstream-drift detection.
