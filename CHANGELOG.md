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

Comparison links are at the foot of this file, one per released version.

## [Unreleased]

### Added

- **A no-Kubernetes path** — [`compose/`](compose/). `docker compose up -d` gives both
  dashboards and both alert sets on `localhost:3000` in about a minute, with nothing
  installed on the host. It reads the *same* dashboards, recording rules, alerts,
  simulator and load profiles the cluster applies, and uses the same scrape job names, so
  every query in `docs/observability.md` pastes in unchanged. What it cannot exercise is
  Kubernetes itself — scheduling on `nvidia.com/gpu`, ServiceMonitor discovery, the
  sidecar import — and `compose/README.md` says so plainly.
- `compose/gpu-metrics-sim.py` — a compose-only stand-in for the fake-gpu-operator's
  exporter, which is a device plugin and a DaemonSet and so has no meaning outside a
  cluster. Copies its exact surface: three series, same labels, same eight `Tesla-T4`s,
  same all-or-nothing memory model. Three series are enough for all four GPU panels and
  every GPU alert, because temperature and power are derived by the shipped rules.
- `scripts/extract.sh` — unwraps the Prometheus rules and LLM profiles out of the
  Kubernetes manifests. Shared by the promtool tests and the compose stack, so both read
  what the cluster actually applies rather than a second copy.
- `task compose` / `make compose`.
- Instructions for publishing the boards to grafana.com, in
  `manifests/dashboards/README.md`. Both were checked against a Grafana whose only
  datasource was named and uid'd differently: `id` is null and no panel carries a fixed
  datasource uid, so they import anywhere.

### Changed

- **Dashboards are now plain `.json`** in `manifests/dashboards/`, rather than JSON
  embedded in hand-maintained ConfigMap YAML. One artefact, used three ways: wrapped in a
  ConfigMap by `install.sh`, mounted by the compose stack, or uploaded to grafana.com and
  imported into any Grafana. The filename is the uid, and `assert_dashboard_contract`
  fails the install if a filename and the `uid` inside it disagree.

  The generated ConfigMap names change with it — `dcgm-gpu-dashboard` becomes
  `gpu-sim-dcgm-dashboard`, `llm-sim-dashboard` becomes `llm-sim-overview-dashboard`. On
  a cluster installed before this change, delete the two old ConfigMaps by hand;
  otherwise both are imported and Grafana holds two boards under one uid.

- Dashboards now carry `app.kubernetes.io/part-of=gpu-sim-dashboards`, and `teardown.sh`
  removes them by it.

### Fixed

- `teardown.sh` left the dashboard ConfigMaps behind. They were never applied from a file,
  so there was no `-f` to delete them by, and nothing else covered them. Selected on the
  ownership label above rather than `grafana_dashboard=1`, because the chart ships several
  boards under that same sidecar label and the delete runs before the Helm uninstall.

## [0.1.0] — 2026-07-30

Initial public release. Build and test GPU and LLM observability without a GPU.

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
- **Tests that need no cluster** — `promtool` unit tests over every recording rule and
  alert (`task rule-tests`), and a simulator exposition selftest (`task selftest`). Both
  run in seconds. They cover what a live check structurally cannot: both sides of every
  threshold, the derived temperature and power arithmetic, the `by (model_name)`
  aggregation that keeps the two tenants apart, the self-disabling `unless on (UUID)`
  guard, and the alerts nothing on the rig ever drives. See [tests/](tests/), which also
  documents the two properties these tests cannot pin, and why.
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

[Unreleased]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/releases/tag/v0.1.0
