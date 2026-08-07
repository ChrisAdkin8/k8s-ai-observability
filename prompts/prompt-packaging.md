# Prompt: Cover the compose path in CI, and install into a cluster that already has Prometheus

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.4.0**: compose-path CI and the --skip-monitoring / --byo BYO-Prometheus path. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Two
pieces of work:

1. **W3** — put the compose path under CI, and make the parity between its GPU producer and
   the cluster's a **checked contract** rather than a claim in a file header.
2. **W4** — let someone install the simulators, rules and dashboards into a cluster that
   **already has** Prometheus.

**Why.** `cd compose && docker compose up -d` is the first command in the README and has no
CI coverage at all, while its GPU producer is a second implementation of the exporter
surface that nothing compares against the first. And both dashboards are now published
([25618](https://grafana.com/grafana/dashboards/25618),
[25620](https://grafana.com/grafana/dashboards/25620)), so people arrive from the catalog
with their own Prometheus, import board 25620, find four panels blank for want of the
`llm:*` recording rules, and have no packaged way to get them.

**⚠️ Sequencing.** If `prompt-fidelity.md` is also being worked, do W1 first: it adds a
panel to the LLM board, which `prompt-chart.md` will later package.

**Run order across the three prompts:** `prompt-fidelity.md` → `prompt-packaging.md` →
`prompt-chart.md`. Each is independently runnable; the order matters only where a later one
packages or tests what an earlier one produces.

### Effort, and where to stop

Estimates, derived from reading the code rather than from doing the work. Treat the
ordering as firmer than the numbers.

| | Estimate | Standalone? |
|--|--|--|
| W3.1 contract file + both assertions | ~half a day | yes |
| W3.2-W3.4 compose CI job, py_compile | ~2 hours | needs W3.1 for the selftest step |
| W4.1-W4.3 `--skip-monitoring`, CRD guard, `RELEASE_LABEL` | ~half a day | yes |
| W4.4 `verify.sh --byo` | ~2 hours | needs W4.1 |
| W4.5 `KPS_RELEASE` across the four scripts | ~2 hours | needs W4.1 |
| W4.6 Task passthrough | ~15 min | yes |

**W3 and W4 are independent of each other.** If you can only do one, do **W4**: it is what
catalog traffic runs into, and W3 protects a path that currently works. The cheapest
useful single commit in the whole prompt is W3.4, one `py_compile` line.

## Background / Facts

Every fact below was read directly in the file cited, on 2026-07-31. Where it turns out to
be wrong anyway, correct it in your commit message.

### CI as it stands — VERIFIED (`.github/workflows/ci.yml`)

Three jobs: `fast` (llm-sim selftest, promtool rule tests, `bash -n scripts/*.sh`),
`upstream-drift` (schedule/dispatch only), `stack` (matrix over `full` and `lite`, running
`task local:up`). **Nothing runs the compose path.** **No Python other than `llm-sim.py` is
syntax checked** — `dashboard-publish.py`, `check-vllm-buckets.py`, `gpu-metrics-sim.py`
and the three `docs/*.py` scripts can ship a `SyntaxError` green.

The Docker Hub login step at `ci.yml:192-197` is gated on secrets being present, because
pull requests from forks never receive them. Reuse that pattern; an ungated login fails
every external contribution and looks like the contributor's fault.

### The compose stack — VERIFIED (`compose/compose.yaml`)

Images: `python:3.12-slim` (×3), `prom/prometheus:v3.7.3`, `grafana/grafana:11.6.0`,
`busybox:1.36`. All but Prometheus come from Docker Hub, so the login gate matters.
Grafana is on 3000, Prometheus on 9090, both overridable via `GRAFANA_PORT` /
`PROMETHEUS_PORT` (`compose/README.md`). Anonymous `Viewer` auth is on, as on the cluster.

### The second GPU producer — VERIFIED (`compose/gpu-metrics-sim.py`)

Stands in for `run-ai/fake-gpu-operator` outside Kubernetes. Its header states the parity
it depends on: exactly three series — `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`,
`DCGM_FI_DEV_FB_FREE` — with label set `Hostname, gpu, UUID, modelName, device`. It has no
selftest, is referenced only from `compose.yaml:81-86`, and **nothing asserts the parity**.
If the chart's surface changes, the kind path fails loudly in CI and the compose path
drifts quietly.

### Selector labels are a silent-failure surface — VERIFIED

`release: kube-prometheus-stack` appears on:

- `manifests/alerts/gpu-prometheusrule.yaml:9-12`
- `manifests/alerts/llm-prometheusrule.yaml:11-12`
- `manifests/servicemonitor/fake-gpu-servicemonitor.yaml:16-17`
- `manifests/servicemonitor/llm-sim-servicemonitor.yaml:10-11`

The ServiceMonitor comments say "harmless; picked up regardless". That is true only of this
repo's own `values.yaml`, which sets the selector-nil behaviour accordingly (see W4.3).
On a kube-prometheus-stack installed under a different Helm release name with default
selectors, these objects are **silently ignored**: no scrape, no rules, no error anywhere.
The Grafana sidecar label `grafana_dashboard=1` (`install.sh:63-71`) is that chart's
convention, not a universal one.

### install.sh shape — VERIFIED (`scripts/install.sh`)

`[1/5]` helm repos · `[2/5]` kube-prometheus-stack, unconditional, `--wait --timeout 15m`
(`:39-48`) · `[2b]` dashboards, ServiceMonitors, alerts (`:50-73`) · `[3/5]`
fake-gpu-operator · `[4/5]` sample workloads · `[5/5]` LLM stack. Assertions run before
anything is created (`:18-25`).

---

## W3 — Cover the headline path, and make parity a contract

**W3.1 One contract file, asserted from both sides.** The parity claim is currently prose
in a header. Replace it with a committed artifact — suggested
`tests/contracts/dcgm-surface.json` — listing the three series and the label keys. Then:

- `compose/gpu-metrics-sim.py --selftest` asserts **exact** equality with the contract —
  series set and label-key set — plus valid HELP/TYPE and that rendering observes nothing,
  in the shape of `llm-sim.py --selftest`;
- `scripts/verify.sh` gains a check asserting the **cluster's** exporter satisfies the same
  contract as a **subset** relation: every contract series present, every contract label
  key present on it.

⚠️ **The two sides are deliberately not the same assertion, and an exact match on the
cluster side fails on day one.** Series arriving through Prometheus carry labels the
exporter never emitted — `job`, `instance`, `namespace`, `pod`, `endpoint`, `service` — and
the consumer-pod labels arrive renamed to `exported_*`, because target labels win the
collision (`docs/observability.md`). Put both semantics in the contract file's own header,
so the asymmetry reads as a decision rather than an oversight.

That is what makes it parity rather than self-consistency: one file, two independent
producers, both asserted. A chart bump that changes the exporter's surface then fails the
cluster check, and the compose sim's own selftest tells you it is now the odd one out.

**W3.2 Run the compose selftest in the `fast` job.** It needs no Docker, so the parity
claim gets checked in seconds on every push.

**W3.3 Add a `compose` job to `ci.yml`.** Bring the stack up, then assert like a user
rather than like a process table:

- ⚠️ **assert the simulators through Prometheus, not by curling them.** Only Prometheus
  (`${PROMETHEUS_PORT:-9090}`) and Grafana (`${GRAFANA_PORT:-3000}`) publish ports in
  `compose.yaml`; the three simulator containers publish none, so `curl` from the runner
  cannot reach them. Query `/api/v1/query` for one series from each producer, which also
  proves scraping works and is the better assertion regardless. `docker compose exec` is
  the fallback if you need raw exposition;
- Prometheus `/api/v1/targets` shows every target `up`;
- Grafana serves **both** boards anonymously by uid — `/api/dashboards/uid/gpu-sim-dcgm`
  and `/api/dashboards/uid/llm-sim-overview`. This mirrors `verify.sh` check 4b, which is
  the check that catches a dashboard that exists but was never imported;
- `docker compose logs` uploaded as an artifact and `docker compose down -v` in an
  `always()` step.

Gate the Docker Hub login as the `stack` job does. Timeout around 10 minutes.

**W3.4 Syntax-check the rest of the Python.** One `python3 -m py_compile` over every `*.py`
in the repo, in the `fast` job.

## W4 — Install into a cluster that already has Prometheus

Do the flag first. It is small, it unblocks people immediately, and the chart builds on it.

**W4.1 `install.sh --skip-monitoring`.** Skip the kube-prometheus-stack install
(`:39-48`); everything else proceeds unchanged.

Follow the house pattern for the flag. `install.sh` has **no** argument parsing today —
`TARGET="${1:?usage}"` and a `case` — while `teardown.sh` handles `--destroy` positionally
as `$2` compared against the literal. Match that, rather than introducing `getopts` into one
script and leaving the other inconsistent.

⚠️ **State what happens to `LITE=1`.** Both mutate the same install path. With monitoring
skipped, `LITE` has nothing to trim: `config.sh`'s `KPS_VALUES` construction must not run,
and the combination should warn or be documented as ignored. A flag that silently does
nothing is the failure mode this repo writes assertions against.

**W4.2 ⚠️ Fail fast on preconditions, naming the fix.** With the flag set, the
`ServiceMonitor` and `PrometheusRule` CRDs must already exist. Check, and refuse with an
explicit message if they do not. Applying objects whose CRDs are absent is precisely the
green-install-with-nothing-working failure this repo builds assertions to prevent, and it
belongs with the other assertions at `:18-25`.

**W4.3 ⚠️ Make the selector labels configurable.** `RELEASE_LABEL` (default
`kube-prometheus-stack`) for the four objects listed above, and the Grafana sidecar label
(default `grafana_dashboard=1`). Document that a mismatch produces **no error at all** —
the rules never evaluate, the scrapes never happen, the board stays empty. Correct the
ServiceMonitor comments, which claim the label is harmless without noting that this depends
on this repo's own values.

VERIFIED, and this is exactly what those comments rely on:
`helm/kube-prometheus-stack/values.yaml:14-16` sets `serviceMonitorSelectorNilUsesHelmValues`,
`ruleSelectorNilUsesHelmValues` and `podMonitorSelectorNilUsesHelmValues` to **false**, so
this repo's Prometheus adopts every ServiceMonitor and rule regardless of label. Upstream
defaults all three to `true`, where the selector becomes `release=<their release name>`. A
BYO user therefore has two possible fixes and should be told both: set `RELEASE_LABEL` to
their release name, or set those three values false on their side — the second is often not
theirs to change.

**W4.4 `verify.sh` needs a BYO mode** that skips the checks assuming this repo installed
the monitoring stack, while still asserting everything about the simulators, rules,
scrapes and dashboards.

**W4.5 ⚠️ The BYO story is not finished until someone can open a board.** VERIFIED:
`grafana.sh:61` port-forwards `svc/${KPS_RELEASE}-grafana` and reads the admin password from
`secret ${KPS_RELEASE}-grafana` (`:86`); `prometheus.sh:47` port-forwards
`svc/${KPS_RELEASE}-prometheus`. On a cluster whose release is `my-monitoring`, both target
a Service that does not exist. Installing successfully and then being unable to open the
dashboards with the repo's own tooling is the last mile of the exact journey this work
exists to serve. Make `KPS_RELEASE` overridable and honour it in **all four** scripts:
`install.sh`, `verify.sh`, `grafana.sh`, `prometheus.sh`.

**W4.6 Reach it through the front door.** `taskfiles/target.yml:121-125` runs
`./scripts/install.sh {{.CLOUD}}` with no `{{.CLI_ARGS}}`, so
`task local:install -- --skip-monitoring` silently does nothing. The README calls Task the
front door, and the passthrough pattern already exists here — `task <prefix>:load -- ramp`
uses it. A flag reachable only from the raw script is half-delivered.

**The Helm chart is deliberately not in this prompt.** It is a bigger piece of work with
its own design constraint, and it depends on `RELEASE_LABEL` from W4.3 existing first. See
`prompt-chart.md`.

## Non-goals

- Alertmanager receivers, SLO burn-rate rules, k3d/minikube support.
- Publishing the chart to Artifact Hub. Get it working first.
- ~~A container image for the simulator~~ — **SUPERSEDED 2026-07-31: now IN SCOPE**, see
  `CONTRIBUTING.md`. Still out of scope *for this prompt*, which is about install paths.
  Any `pip install` remains out of scope, unchanged.
- Anything in `scripts/llm-sim.py` or the metric surface — see `prompt-fidelity.md`.
- Real GPU hardware, drivers, quota or model weights.

## Acceptance criteria

1. `python3 compose/gpu-metrics-sim.py --selftest` passes, asserts against
   `tests/contracts/dcgm-surface.json`, and runs in the `fast` job.
2. The contract is asserted from the cluster side too, and **the negative case is a
   permanent test, not a manual experiment**. The compose selftest must include a case that
   feeds a deliberately wrong contract fixture and asserts the checker rejects it, so the
   assertion cannot silently rot into one that passes against anything. Do not prove this
   by editing the real contract and reverting; nothing enforces the revert.
3. The `compose` job passes on a clean checkout, including both anonymous Grafana uid
   lookups, and tears the stack down even when an assertion fails.
4. `python3 -m py_compile` covers every `*.py` in the repo.
5. ~~**The BYO path is demonstrated, not asserted in prose.**~~ **DONE 2026-08-04**, and
   it found a bug that made the criterion unsatisfiable as written.

   Run against a kind cluster with kube-prometheus-stack installed as release **`acme-mon`**
   into `monitoring`, with **no selector overrides** — upstream defaults
   `ruleSelectorNilUsesHelmValues` and its siblings to true, so the selector is
   `release=acme-mon`, and that default *is* the BYO case.

   ⚠️ **The snippet below cannot pass as written, and that is a finding, not a nit.** It
   passes `KPS_RELEASE` to `grafana.sh` only, while `install.sh` and `verify.sh` get
   nothing — so the objects are labelled `release=kube-prometheus-stack` against a
   Prometheus selecting `release=acme-mon`. Run verbatim: `install.sh` **exits 0** and
   prints follow-up commands naming a release that does not exist on the cluster.

   ⚠️ **And `KPS_RELEASE` did not work even when passed everywhere**, because the scripts
   BUILT Service names from it. `${KPS_RELEASE}-prometheus` only resolves when the release
   name contains the chart name, so it worked for `kube-prometheus-stack` and no other
   value — the one name the flag was not needed for. Worse, it failed *as a different
   bug*: the port-forward hit a Service that does not exist, so every query returned
   nothing and every check blamed the selector label, which was correct. Fixed by
   resolving names from the cluster (`resolve_kps`, `scripts/config.sh`); see CHANGELOG.

   What actually passes, with the fix and `KPS_RELEASE` passed to every script:

   ```sh
   ./scripts/kind-up.sh
   helm install acme-mon prometheus-community/kube-prometheus-stack \
     -n monitoring --create-namespace --wait          # no selector overrides
   KPS_RELEASE=acme-mon ./scripts/install.sh local --skip-monitoring
   KPS_RELEASE=acme-mon ./scripts/verify.sh  local --byo
   KPS_RELEASE=acme-mon ./scripts/grafana.sh local
   ```

   Result: **26 passed, 0 failed, 3 skipped** in ~90s (the three skips are anonymous
   Grafana access, which is this repo's values choice and not a BYO user's). `grafana.sh`
   resolved both the Service and the admin Secret and served both boards —
   `GPU Simulation — DCGM Overview` (4 panels) and
   `LLM Simulation — vLLM Serving Overview` (13 panels), fetched by `uid` from the foreign
   Grafana. Before the fix the same command gave six failures over more than ten minutes.

   The chart path was proven separately in the same session (`helm test` -> `ALL
   PRECONDITIONS PASSED` against `acme-mon`, and **FAIL** on the default `releaseLabel`,
   which is the check earning its place). Original text:
   ```sh
   ./scripts/kind-up.sh
   helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
   helm install my-monitoring prometheus-community/kube-prometheus-stack \
     -n monitoring --create-namespace --wait
   ./scripts/install.sh local --skip-monitoring   # note: release is NOT kube-prometheus-stack
   ./scripts/verify.sh local --byo
   KPS_RELEASE=my-monitoring ./scripts/grafana.sh local    # must open BOTH boards
   ```
   The last line is the point: the recipe is not finished at a green verify, it is finished
   when a human sees a dashboard. Prove the Task path too:
   `task local:install -- --skip-monitoring`.
   This must work, which means `RELEASE_LABEL=my-monitoring` has to be reachable from the
   command line — the deliberately mismatched release name is the point of the test.
6. `./scripts/install.sh local --skip-monitoring` against a cluster with no operator CRDs
   refuses with a message naming the fix, and creates nothing.
7. `task local:up` and both CI `stack` legs still pass unchanged.
8. No second copy of any dashboard JSON, rule file or the simulator exists in the tree.
   State how you verified this.

## Process

- **One logical change per commit** (`CONTRIBUTING.md`). The contract file, the compose CI
  job, the `--skip-monitoring` flag and the chart are four commits at minimum.
- **`CHANGELOG.md` always**, under `[Unreleased]`, saying *why*. W4 changes what a cluster
  looks like after install and adds a supported installation mode, so it belongs in the
  notes a user reads before upgrading.
- **Work on a branch and open a PR.** `main` carries a ruleset requiring the CI checks, and
  it has an admin bypass — so a direct push *succeeds* while reporting
  `Bypassed rule violations`, and the work lands without ever being gated. Let CI gate it.
- Do not weaken an existing check to get green.
