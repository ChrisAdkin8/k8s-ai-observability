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

While the version is `0.x` a breaking change bumps the **MINOR**, per semver's
initial-development rule — the table above applies as written from `1.0.0`. Either way
the migration steps are in the entry.

Pinned upstream versions live in [docs/versions.md](docs/versions.md); a bump there is
worth an entry below whenever it changes behaviour rather than just a number.

Comparison links are at the foot of this file, one per released version.

## [Unreleased]

_Nothing yet._

## [0.3.0] — 2026-07-31

**The simulator had stopped matching real vLLM, and nothing here could tell.** Two metric
names and all three histogram bucket layouts had been superseded by the V1 engine while
every check in this repo stayed green — because every check reads the simulator, and the
simulator was perfectly consistent with itself. It was consistent with the wrong thing.

That is the failure this rig exists to prevent, so this release does three things rather
than one: resyncs the surface, re-derives every number that depended on it, and adds a
check that points *upstream* — the only kind that could have caught it. Also a trimmed
monitoring profile, for the laptops that cannot spare 8 GiB.

⚠️ **Breaking, by this file's own table** — it changes metric names, which under semver's
initial-development rule bumps the MINOR while the version is `0.x`. Re-run
`./scripts/install.sh <target>`, and see Fixed below for the two names to change in any
dashboard or alert of your own. `--vllm-surface both` is the quickest way to find them.

### Fixed

- ⚠️ **The simulated vLLM metric surface was two releases out of date, and failing
  silently.** `0.1.0` and `0.2.0` emitted the pre-V1 spellings of two series. Nothing
  broke, which is the whole problem: a renamed metric does not error, it just stops
  matching, so every test here stayed green while the repo's central claim — that what
  you build against the simulator transfers unchanged to a real vLLM deployment — had
  quietly stopped being true for these two.

  | Was | Now | Why upstream moved it |
  |--|--|--|
  | `vllm:gpu_cache_usage_perc` | `vllm:kv_cache_usage_perc` | V1 dropped CPU KV-cache offload, so `gpu_` distinguished nothing |
  | `vllm:time_per_output_token_seconds` | `vllm:inter_token_latency_seconds` | same measurement, clearer name |

  Every other series this rig emits — TTFT, e2e latency, the token counters,
  `request_success_total`, the running/waiting gauges — kept its name and is unchanged.

  Updated together, so none of them can disagree: the simulator, the
  `LLMKVCacheSaturated` alert, the `llm:tpot:p95_5m` recording rule, the LLM dashboard,
  the promtool tests and the docs. **The recorded rule names are deliberately NOT
  renamed** — `llm:tpot:p95_5m` stays, because `tpot` is what the measurement is called
  and renaming it would break every dashboard built on it for no gain.

  ⚠️ **Upgrading an existing install:** re-run `./scripts/install.sh <target>`. Any
  dashboard or alert of your own that references the two old names needs the same edit —
  `--vllm-surface both` below is the quickest way to find them.

- ⚠️ **The histogram bucket boundaries had drifted too — and that was the more dangerous
  half.** A wrong metric name fails loudly: the panel is blank and you go looking. A wrong
  bucket boundary fails quietly, because `histogram_quantile()` still returns a confident,
  plausible number that simply will not match real hardware.

  All three lists were the v0.6.x layout. `TTFT_BUCKETS` was the one that actually broke:

  ```
  both:  0.001 0.005 0.01 0.02 0.04 0.06 0.08 0.1 0.25 0.5 0.75 1.0 2.5 5.0 7.5 10.0
  v0.6:  │ 15   20   30   45   60   90  120
  V1:    │ 20   40   80  160  640 2560
  ```

  Sixteen identical boundaries, then nothing in common — and the saturated tenant sits at
  ~58s, inside the tail that had diverged. So the single number this rig exists to teach
  you to read was the number it got wrong. **The reported p95 moves 59.25 → 78 with the
  simulated latency completely unchanged**; only the resolution it is measured at moved.

  `TPOT_BUCKETS` was a strict *prefix* of V1's, so it was never wrong at the operating
  point — V1 only extended the tail. `E2E_BUCKETS` gained sub-second resolution
  (`0.3/0.5/0.8`) that the old list, starting at `1.0`, did not have at all.

  All three are now transcribed from `vllm/v1/metrics/loggers.py`. Knock-on changes, all
  re-derived rather than adjusted until green:

  - `tests/rules/llm-rules_test.yaml` fed `le="45.0"`/`le="60.0"`, boundaries V1 does not
    have. The saturated case now places its mass in `(40, 80]` and expects `78`. The
    steady tenant's boundaries (`0.08`, `0.1`) are identical in both layouts, so its three
    expectations are untouched — which makes them the control.
  - **The 2s `LLMHighTTFT` threshold survives, and is now pinned rather than assumed.** A
    new test block brackets it as tightly as can be done portably — `0.9875` below,
    `4.875` above.

    ⚠️ *Portably* is load-bearing there. The tighter test — a tenant inside `(1.0, 2.5]`,
    the bucket `2` actually falls in — **cannot be written**: arm64 and amd64 return
    values one ULP apart (`2.4250000000000003` vs `2.425`), promtool compares exactly, and
    the annotation flips between `2.42s` and `2.43s`. The mechanism is not established and
    is deliberately not guessed at in the comment. Every pinned percentile is now verified
    against a real linux/amd64 promtool as well as the local one;
    [tests/](tests/#-check-a-new-expected-value-on-amd64-before-committing-it) has the
    recipe and the list of values known green on both.
  - `verify.sh`'s L3b 120s bound still holds but no longer means what it did. 58s
    quantises to `78` in `(40, 80]`; the next bucket up interpolates to `152`, so the
    check jumps straight past 120 rather than degrading. It now reads "the queue wait has
    not escaped `(40, 80]`", and says so.

  ⚠️ **The screenshots in the README predate this** and show the saturated tenant at
  `1.20 mins` rather than 78s. Noted in place rather than silently left to disagree.

### Added

- **`--vllm-surface v1|v0|both`** on `scripts/llm-sim.py` (or `LLM_SIM_VLLM_SURFACE`),
  defaulting to `v1`. `both` emits the superseded v0 names alongside the current ones,
  which turns the rename above into something useful: point an existing dashboard at a
  `both` simulator and every panel still bound to a v0 name is precisely the set your
  engine upgrade will break. `METRIC_SURFACES` in that file is the single place the
  mapping lives, and `--selftest` now asserts the default emits v1 and *not* v0.
  Real vLLM only ever emits one surface — `both` is a rig affordance, not a fidelity
  claim.

- **`scripts/check-vllm-buckets.py`, and a CI job that runs it weekly.** Fetches
  `vllm/v1/metrics/loggers.py` and asserts each of the three bucket lists appears there
  verbatim, reporting the exact point of divergence when one does not.

  It exists because **nothing else in this repo could have caught either drift.** Every
  test here reads the simulator, and the simulator was perfectly consistent with itself —
  it was consistent with the wrong thing. A fault in a relationship to something *outside*
  the suite needs a check that points outside it, which is the same reasoning behind the
  existing weekly Helm-chart drift detection.

  It does not model upstream's file structure, which would itself be a thing that drifts:
  an `ast` walk pulls out every numeric list literal and looks for ours among them, so it
  survives renames and code moving between functions, and fires only when the *numbers*
  change. **Scheduled and dispatch only** — an upstream release is not a contributor's
  fault, and reddening unrelated pull requests trains people to ignore red. Exits `0` in
  sync, `1` on drift, `2` when it could not check at all, because "we did not look" and
  "we looked and it was fine" are not the same result.

- **A trimmed monitoring profile — `LITE=1`.**
  [`helm/kube-prometheus-stack/values-lite.yaml`](helm/kube-prometheus-stack/values-lite.yaml),
  an overlay applied on top of `values.yaml` rather than a second copy of it, so nothing
  load-bearing is stated twice. It drops Alertmanager, kube-state-metrics, node-exporter
  and the chart's ~100 default rules, and puts Prometheus on 256Mi/512Mi with 2h
  retention — taking the runtime floor from 5 GiB to 3 and the recommendation from 8 to 4.

  This exists because the 8 GiB ask is the most common reason a first `task local:up`
  never finishes: colima ships 2 CPU / 2 GiB, Prometheus sits Pending, and the run reads
  as a broken repo rather than an under-provisioned VM. The compose path answers "show me
  the boards"; this answers "let me exercise Kubernetes on the laptop I have".

  Nothing `local` exists to prove is given up — ServiceMonitor discovery, PrometheusRule
  evaluation, the sidecar dashboard import and `nvidia.com/gpu` scheduling all still run,
  and every check in `verify.sh` still applies. Verified rather than assumed: no `kube_*`
  or `node_*` series appears in either dashboard, either rule file or `verify.sh`, and
  the alert checks read Prometheus's own `ALERTS` series, which never involves
  Alertmanager. Alert *delivery* is not exercised — but it was not exercised by the full
  profile either.

  `LITE` is resolved in `scripts/config.sh`, so the Helm values stack and `kind-up.sh`'s
  sizing floor cannot disagree about which profile is being installed.

- **A social preview card** — `docs/social-preview.png`, the 1280×640 image GitHub
  renders in place of a bare link wherever this repo is shared. The setting lives only in
  GitHub's web UI and is exposed by no API, so the artefact is kept here to give it a
  history and a way back: the card is the top row of the LLM board — both tenants either
  side of the 2s threshold — over the repo name, the compose one-liner and the CI claim.

  `docs/social-preview.py` regenerates it (needs `pillow`, nothing else). It crops
  `docs/llm-dashboard.png` by **fraction rather than by pixel**, because the invariant
  that actually holds is "the top row occupies this proportion of the board" — Grafana
  lays panels out on a 24-column grid, so that survives both a differently-sized window
  and a later resize of the file. It warns if the crop lands under 1280px, the width
  below which it would be upscaling.

  Uploading it is manual, and re-uploading is the only way to change what GitHub serves:
  **Settings → General → Social preview → Edit → Upload an image**. Confirm with
  `gh api repos/ChrisAdkin8/k8s-ai-observability --jq .uses_custom_open_graph_image`.

- `docs/optimize-images.py` — resizes and palette-quantises the dashboard screenshots to
  the settings below. Idempotent: a file already at the target width and depth is skipped,
  so it is safe to re-run and safe to wire into a pre-commit hook.

### Changed

- **The dashboard screenshots are optimised** — 1.1 MB down to 414 KB, a 62% cut in what
  the README costs a first-time visitor before they have read a word. They were 3456px
  wide against GitHub's ~896px content column, so three quarters of every byte was
  reaching no display at any pixel ratio.

  Visually lossless rather than merely acceptable: these are flat UI screenshots with
  ~2700 distinct colours, so a 256-entry palette reproduces them at a measured mean
  channel error of 0.19/255, and dithering changes nothing because there is almost no
  gradient to dither. Re-check that if a board ever gains a heat-map or a photographic
  panel.

  **2400px, not the 1792px the README alone would want**, because `social-preview.py`
  crops 78% of `llm-dashboard.png` and renders it 1280px wide: at a 1792px source that
  crop is a 1.09x downscale and the card's text visibly softens. 2400px keeps it a 1.45x
  supersample. The alternative was a second full-resolution copy of the same screenshot,
  which is the kind of duplicate this repo avoids everywhere else.

## [0.2.0] — 2026-07-31

Two things this rig could not do before: show you the boards without a cluster, and hand
you a dashboard you could import anywhere.

⚠️ **Upgrading from 0.1.0 requires one manual step** — see the ConfigMap rename under
Changed.

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

- The README now opens with the compose path, since it is the fastest way to see the
  boards; `task local:up` follows as the way to exercise Kubernetes itself.

- The compose stack's ports are overridable — `PROMETHEUS_PORT` and `GRAFANA_PORT`, the
  same names `scripts/config.sh` already uses. Not cosmetic: `scripts/prometheus.sh` and
  `scripts/grafana.sh` hold port-forwards on 9090 and 3000, and a loopback-bound
  port-forward wins over the container's wildcard binding. Run both at once and you get
  the *other* Prometheus at the same URL, with nothing reporting an error.

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

[Unreleased]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/releases/tag/v0.1.0
