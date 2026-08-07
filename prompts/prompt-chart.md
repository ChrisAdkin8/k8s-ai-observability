# Prompt: A Helm chart, for clusters that already have a monitoring stack

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.5.0**: the Helm chart in charts/k8s-ai-observability. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Package
the simulators, workloads, rules and dashboards as `charts/k8s-ai-observability`, so
someone who already runs Prometheus can `helm install` this rig instead of handing their
monitoring stack to `scripts/install.sh`.

**Why.** Both boards are published ([25618](https://grafana.com/grafana/dashboards/25618),
[25620](https://grafana.com/grafana/dashboards/25620)). People now arrive from the catalog,
import a board, and find panels blank because they lack the `llm:*` recording rules. Today
their only route is a script that installs kube-prometheus-stack over the top of whatever
they already run, which nobody with a production monitoring stack will do. This is the
single biggest structural blocker to adoption.

> ## STATUS — updated 2026-07-31, read before starting
>
> **Prerequisite 1 below is SATISFIED.** `prompt-packaging.md` W4.1–W4.3 shipped in
> **0.4.0**: `--skip-monitoring`, the CRD precondition, and `RELEASE_LABEL` /
> `GRAFANA_DASHBOARD_LABEL` as real, overridable variables in `scripts/config.sh`. W-C3's
> values now map onto things that exist rather than things to invent. **This chart is
> unblocked.**
>
> **⚠️ W-C2's calculus has changed, and this is the part to re-read before choosing an
> option.** W-C2 is the hard constraint: `.Files.Get` cannot read outside the chart
> directory, so the chart cannot reach `manifests/dashboards/*.json`,
> `manifests/alerts/*.yaml` or `scripts/llm-sim.py` where they live, and a second committed
> copy is refused.
>
> A **published container image for the simulator is now in scope** (`CONTRIBUTING.md`,
> `docs/architecture.md`; the work is W2 of `prompt-phases-and-image.md`). If it ships,
> **`scripts/llm-sim.py` drops out of that list entirely** — a chart whose simulator
> Deployment references an image needs a tag in `values.yaml`, not the file. That leaves
> the dashboards and the rules, both static YAML/JSON rather than executable code, which
> makes option **(a)** (`task chart` builds into gitignored `dist/`) cheaper than it looked
> when this brief was written.
>
> It introduces one coupling this brief could not have anticipated: **the chart's default
> image tag and the repo's release tag must agree, and nothing enforces it.** A chart
> pinned to a stale tag installs cleanly and runs an old simulator — a green install with
> something silently wrong, which is the failure class this repo builds assertions against.
> Assert it wherever `task chart` runs, and treat it as belonging to W-C4's list.
>
> Everything else below stands unchanged, including W-C4, the acceptance criterion that a
> wrong `releaseLabel` fails **loudly**, and the non-goals.

**⚠️ Prerequisites, in order.**

1. ~~`prompt-packaging.md` W4.1-W4.3 must land first.~~ **Satisfied — shipped in 0.4.0.**
   This chart depends on `RELEASE_LABEL` and the CRD precondition existing as concepts, and
   they now do.
2. If `prompt-fidelity.md` W1 is also being worked, do it first: it adds a panel to the LLM
   board that this chart ships. **(Also satisfied — W1 shipped in 0.4.0.)** The equivalent
   live dependency is now W1 of `prompt-phases-and-image.md`, which adds a further panel.
3. **New:** if the simulator image (W2 of `prompt-phases-and-image.md`) is being worked,
   do it **before** this — see the STATUS box above for why it changes W-C2.

**Effort: 1-2 days.** The templating is a day; the constraint in W-C2 is what makes it two.

**Run order across the three prompts:** `prompt-fidelity.md` → `prompt-packaging.md` →
`prompt-chart.md`. Each is independently runnable; the order matters only where a later one
packages or tests what an earlier one produces.

## Background / Facts — VERIFIED 2026-07-31

### What `install.sh` actually creates

| Step | Objects |
|--|--|
| `:39-48` | kube-prometheus-stack Helm release — **this chart's job is to not do this** |
| `:63-71` | one ConfigMap per `manifests/dashboards/*.json`, labelled `grafana_dashboard=1` and `app.kubernetes.io/part-of=gpu-sim-dashboards` |
| `:72-73` | `manifests/servicemonitor/` (2), `manifests/alerts/` (2) |
| `:80-85` | the `fake-gpu-operator` Helm release, with `topology.nodePoolLabelKey` set from config |
| `:106` | `manifests/workloads/` — the sample GPU Deployments |
| `:112-118` | `llm-sim` namespace, the `llm-sim-script` ConfigMap **built from `scripts/llm-sim.py`**, then `manifests/llm/` |

Namespaces in play: `monitoring`, `gpu-operator`, `llm-sim`, all set in `scripts/config.sh`.

### ⚠️ The safety net lives in the script, and a chart install walks straight past it

`install.sh:18-25` runs five assertions **before anything is created**:
`assert_manifest_namespaces`, `assert_gpu_contract`, `assert_dashboard_contract`,
`assert_llm_contract`, `assert_terraform_contract`. `CONTRIBUTING.md` documents what each
one catches, and every one of them exists because breaking it produces **a green install
with something silently wrong** — a dashboard whose filename and uid disagree, two LLM
tenants sharing a `model_name`, a node-pool label that matches nothing.

A `helm install` runs none of them. **Reproducing that safety net is a first-class
requirement of this work, not a nicety** — see W-C4. A chart that installs cleanly and
produces an empty dashboard is worse than no chart, because the failure arrives later and
looks like the user's fault.

### The three-way naming invariant

`docs/architecture.md#the-naming-invariant-read-before-editing`: the node-pool label value,
the fake operator's topology pool name and the workloads' node selector must agree. A chart
cannot label nodes, so this becomes a documented prerequisite plus a check.

## Requirements

**W-C1 Chart scope.** Templates for: the two dashboard ConfigMaps, two ServiceMonitors, two
PrometheusRules, the LLM namespace, profile ConfigMaps, the `llm-sim-script` ConfigMap, the
simulator Deployments and Service, and the sample GPU workloads. `fake-gpu-operator` is a
**conditional dependency** in `Chart.yaml` (`condition: fakeGpuOperator.enabled`, default
true), not a vendored copy. `kubePrometheusStack.enabled` defaults to **false**; when true,
add it as a second conditional dependency so the chart can also serve a greenfield cluster.

**W-C2 ⚠️ The single-source-of-truth constraint, which is the hard part.** Helm's
`.Files.Get` cannot read outside the chart directory, so the chart cannot reference
`manifests/dashboards/*.json`, `manifests/alerts/*.yaml` or `scripts/llm-sim.py` where they
live. **A second committed copy is not acceptable** — it is the one thing this repo refuses
everywhere else, and a drifted copy of `llm-sim.py` would be undetectable from the outside.

Pick one and justify it in the chart's README:

- **(a) Build step.** `task chart` assembles the chart into gitignored `dist/` from
  the canonical files, on the same terms as `scripts/dashboard-publish.py`. Nothing is
  committed twice. Cost: `helm install ./charts/...` from a clone does not work without the
  build first, which is a surprising failure for a contributor.
- **(b) Symlinks.** `charts/k8s-ai-observability/files/` holds symlinks to the canonical
  files. Cost: `helm package` and `git archive` follow them inconsistently across
  platforms — verify before choosing this.
- **(c) Generate and commit, with an assertion.** Copies are committed, and a check in CI
  and in `install.sh` fails if any copy differs from its source. Cost: the copies exist.

Recommended: **(a)**, with `task chart` wired into the release flow, because it is the
only option where the second copy never exists in the tree. Whichever you choose, a
divergence must **fail something automatically**.

**W-C3 Values, and the labels that fail silently.** At minimum:

```yaml
kubePrometheusStack: { enabled: false }
fakeGpuOperator:     { enabled: true }
releaseLabel: kube-prometheus-stack   # the `release:` selector on rules + ServiceMonitors
grafana:
  dashboardLabel: grafana_dashboard    # sidecar selector key
  dashboardLabelValue: "1"
namespaces: { monitoring: monitoring, gpuOperator: gpu-operator, llmSim: llm-sim }
nodePoolLabelKey: run.ai/simulated-gpu-node-pool
llm:
  steadyModel: ...
  saturatedModel: ...
  # added by prompt-fidelity.md W1 — the chart templates the profile ConfigMaps,
  # so these have to be reachable as values or they are frozen at their defaults
  prefixCacheHitRate: { steady: 0.35, saturated: 0.15 }
  kvBlockTokens: 16
workloads: { enabled: true }           # the sample GPU Deployments
```

The four objects that carry the `release:` selector today are listed in
`prompt-packaging.md`; do not restate them here, cite them. Document, in `values.yaml`
itself, that a wrong `releaseLabel` or `dashboardLabel` produces **no error at all**: the rules never evaluate, the scrapes never happen, the boards stay
empty. That is the single most likely way this chart appears broken.

**W-C4 Port the assertions into the chart.** Every invariant `install.sh` checks must fail
the install here too. Use template-time `fail` where the input is knowable at render time —
dashboard filename versus the `uid` inside the JSON, that every board parses as JSON at all
(`assert_dashboard_contract` checks this too, because an unparseable board breaks the
install mid-apply), and the two LLM `model_name`s being distinct — and a `helm test` hook
for
what needs a live cluster, such as the operator CRDs being present and
`nvidia.com/gpu` being advertised by at least one node. A `--dry-run` must catch the
render-time class. Reference `CONTRIBUTING.md`'s table so the two lists cannot drift.

**W-C5 Chart metadata and versioning.** `Chart.yaml` `appVersion` tracks the repo's release
tag; the chart `version` moves independently. `helm lint` clean, `--dry-run` clean.
`values.schema.json` for the values above, because a typo in `releaseLabel` is otherwise
undetectable until someone notices an empty board.

**W-C6 CI.** Add a job that runs `helm lint`, then `helm template` with both
`kubePrometheusStack.enabled=false` and `=true`, and asserts the render-time assertions
fire on deliberately broken values.

⚠️ **The `fast` job has no Helm.** It installs promtool and nothing else
(`.github/workflows/ci.yml`), so this needs its own install step with the version pinned
the way `HELM_VERSION` is pinned for the `stack` job — v3, not the v4 line, for the reason
given there. Adding it to `fast` is fine if it stays quick; a separate `chart` job is also
fine. Do not leave it depending on a Helm that happens to be on the runner.

**W-C7 Documentation.** A chart README covering the BYO story, the prerequisite node label,
the W-C2 decision and why, the two silent-failure labels, and **how to reach the boards
when the monitoring release is not called `kube-prometheus-stack`** — `grafana.sh` and
`prometheus.sh` hardcode that name today, which `prompt-packaging.md` W4.5 makes
overridable. Chart users hit that first. Link it from the repo README
(the "What you get" section) and from `manifests/dashboards/README.md`, where someone
arriving from the catalog will land. `CHANGELOG.md` entry: this adds a supported
installation mode.

## Non-goals

- Publishing to Artifact Hub, or a `helm repo` on GitHub Pages. Get it working first; that
  is a separate, mostly-administrative piece.
- Replacing `scripts/install.sh`. It stays the source of truth for install ordering and the
  wrong-context guard, and remains what CI exercises.
- Templating the Terraform, or any cloud-specific resource.
- ~~A container image for the simulator~~ — **SUPERSEDED 2026-07-31: now IN SCOPE**, see
  `CONTRIBUTING.md`. ⚠️ This one MATTERS here: if an image ships, `scripts/llm-sim.py`
  drops out of W-C2's list of files the chart cannot reach, and the chart's default image
  tag becomes a new thing that must track the release tag. Any `pip install` is unchanged.
- Alertmanager receivers, SLO rules, k3d/minikube support.

## Acceptance criteria

1. `helm lint charts/k8s-ai-observability` is clean, and `helm template` renders with both
   `kubePrometheusStack.enabled=false` and `=true`.
2. **The BYO install works against a foreign release name**, which is the case the labels
   get wrong:
   ```sh
   ./scripts/kind-up.sh
   helm install my-monitoring prometheus-community/kube-prometheus-stack \
     -n monitoring --create-namespace --wait
   task chart                                  # option (a) only — see W-C2
   helm install rig dist/charts/k8s-ai-observability \
     --set releaseLabel=my-monitoring --wait
   ./scripts/verify.sh local --byo
   ```
   ⚠️ **The path depends on the W-C2 decision.** Under option (a) the installable chart is
   the built one in gitignored `dist/`, and `./charts/...` holds templates with no files
   beside them. Under (b) or (c) it is `./charts/k8s-ai-observability` directly. Whichever
   you pick, the chart README's quickstart and this recipe must agree — a README that
   `helm install`s a path that does not resolve is the first thing a new user hits.
3. With `releaseLabel` left at its default against that same cluster, the boards are empty
   and **the chart's own `helm test` says why**. This is the failure mode to make loud; if
   it is silent, W-C3 and W-C4 are not done.
4. Every assertion in `CONTRIBUTING.md`'s invariants table either fires at `--dry-run` or is
   covered by `helm test`. State which is which in the chart README.
5. No second committed copy of any dashboard JSON, rule file, profile or `llm-sim.py`
   exists in the tree. State the command you used to verify it.
6. `task local:up`, both CI `stack` legs and `./scripts/install.sh local` still pass
   unchanged. The chart is an addition, not a replacement.
7. `helm uninstall` removes everything the chart created and nothing it did not — in
   particular it must not delete dashboards the monitoring stack itself ships, the same
   trap `teardown.sh:67-71` avoids by selecting on `app.kubernetes.io/part-of=gpu-sim-dashboards`
   rather than `grafana_dashboard=1` — VERIFIED, with the reasoning in a comment there.

## Process

- **One logical change per commit** (`CONTRIBUTING.md`): the skeleton and W-C2 decision;
  the templates; the assertions; CI; docs.
- **`CHANGELOG.md` always**, under `[Unreleased]`, saying *why*.
- **Work on a branch and open a PR.** `main` carries a ruleset requiring the CI checks, and
  it has an admin bypass — so a direct push *succeeds* while reporting
  `Bypassed rule violations`, and the work lands without ever being gated. Let CI gate it.
- Do not weaken an existing check to get green.
- If the W-C2 option you pick turns out to be unworkable, say so in the commit message and
  switch — do not fall back to an unchecked second copy.
