# Prompt: The chart you can actually install

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.8.0**: the chart is published to `ghcr.io` as an OCI
> artefact and verified by being consumed rather than by being pushed. All twelve
> acceptance criteria are met. Nothing here is outstanding, and nothing here should be
> acted on.
>
> ⚠️ **Its `Background` describes the tree BEFORE this work landed**, so those `file:line`
> citations were true on the date stated and are not now — this prompt is what changed
> them. Read `CLAUDE.md` for the standing law, `docs/ci.md` for what CI proves and
> `docs/releasing.md` for how a release is cut.
>
> **What it did not anticipate**, recorded because that is the value of a record: proving
> W5 uncovered two bugs the prompt had no way to predict — the chart's `helm test` image
> had ceased to exist, and `KPS_RELEASE` worked only for the one release name it was not
> needed for. Both are in the 0.8.0 changelog. The publish itself then found three more,
> which is what `prompt-verify-the-verifiers.md` was written for.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Five
pieces of work:

1. **W1** — publish the chart to `ghcr.io` as an OCI artefact, on every release tag.
2. **W2** — make the chart's own `version` move, because publishing turns it into a
   contract.
3. **W3** — verify the published chart by consuming it, not by trusting the push.
4. **W4** — list it on Artifact Hub, which `Chart.yaml` has been anticipating since 0.5.0.
5. **W5** — prove the BYO path end to end against the **locally built** chart, unproven
   since 0.4.0 and the reason the chart exists at all. W3 re-runs it against the published
   artefact.

**Why.** 0.5.0's changelog says the chart exists because *"people arrive from the catalog,
import one, and find the panels blank for want of the `llm:*` recording rules… That was the
single biggest structural blocker to adoption."* The chart is real, tested in CI, and
assembled into gitignored `dist/` — so the only way to get it is to clone the repo and run
`task chart`. **The blocker it was built to remove is still there, for exactly the person it
was built for.** This is a finishing job, not a feature.

⚠️ **Read `CLAUDE.md` first.** It carries the repo's standing law and this file does not
repeat it; governing rules are cited by number.

### Effort, and where to stop

Estimates from reading the code, not from doing the work. **Treat the ordering as firmer
than the numbers** — and re-derive W1, the largest.

| | Estimate | Depends on |
|--|--|--|
| W1 publish workflow | ~half a day | — |
| W2 chart-version policy, exercised | ~1 hour | — |
| W3 pull-back verification | ~2 hours | W1 |
| W4 Artifact Hub listing | ~1 hour | one published version |
| W5 BYO end-to-end proof | **~1 day** | needs a cluster with a *foreign* release name |
| Docs + CHANGELOG | ~2 hours | continuous |

**~2.5 days.** ⚠️ W5 is priced at a day rather than half of one because it has never been
attempted — it is unproven since 0.4.0 precisely because nobody has walked it — and **both
of its known failure modes produce no error at all**. Something never done, whose failures
are silent, does not get a half-day estimate.

⚠️ W1 is an instrument, not a feature — its output is consumed by strangers,
so price it as code **plus verification**, and W3 is that verification rather than a nicety.

**W5 + W1 + W2 + W3 are the deliverable, in that order.** W4 is a form submission that can
follow whenever.

**W5 is the gate, not an optional extra.** It is independent of everything else and runs
first: if the BYO path does not work end to end, publishing a chart whose entire audience is
BYO users would ship the gap one level up. A failure there stops the release rather than
being worked around.

## ⚠️ Publishing is close to irreversible — decide before the first push

A published chart is pinned by consumers. Unpublishing breaks them, and a version number
cannot be reused: Helm registries reject a re-push of the same `version`, and Artifact Hub
keys its history on it. **Everything in W2 must be settled before W1 pushes anything**,
because the first published version is the one you live with.

## Background / Facts

Read directly in the file cited, on 2026-08-04. Where one is wrong anyway, correct it in
your commit message.

### ⚠️ There is no distribution today — VERIFIED (`git ls-remote`, GitHub Pages API)

No `gh-pages` branch; GitHub Pages returns 404. Nothing has been published, so there are no
consumers to break and no legacy layout to match. Greenfield.

### ⚠️ The chart cannot be installed from the tree — VERIFIED (`Taskfile.yml`, `scripts/chart-build.py`)

`helm install ./charts/...` does not work. Helm's `.Files.Get` cannot read outside the chart
directory, so the chart cannot reference `manifests/dashboards/*.json` or
`manifests/alerts/*.yaml` where they live, and a committed second copy is refused here as
everywhere else. `task chart` assembles into gitignored `dist/charts/`.

**The publish must therefore package from `dist/`, never from `charts/`.** A workflow that
runs `helm package charts/k8s-ai-observability` will produce a chart whose templates
reference files that are not in it — and it will succeed, because packaging does not render.

### ⚠️ The version policy is written and has never been exercised — VERIFIED (`charts/k8s-ai-observability/Chart.yaml:7-16`)

```
#   version    — the CHART's own version. Bump it for a templating change even
#                when nothing about the rig moved.
#   appVersion — the REPO's release tag.
version: 0.1.0
appVersion: "v0.7.0"
```

The distinction is documented and correct. But `version` has moved **once, ever** — it has
been `0.1.0` since the chart landed, across template changes including thirteen render-time
assertions. Unpublished that is harmless. **Published, it is a contract that is already
wrong**, and the second push will be rejected outright. See W2.

### ⚠️ The dependencies are conditional, and one defaults ON — VERIFIED (`Chart.yaml:31-47`)

`kube-prometheus-stack` is `condition: kubePrometheusStack.enabled`, default **false** — the
premise of the chart. `fake-gpu-operator` defaults **true**, because without it nothing
advertises `nvidia.com/gpu` and the GPU board is blank.

⚠️ **Vendoring both dependencies into the package is FORCED, not a choice** — an earlier
draft of this file framed it as one and was wrong. `helm install` of a *packaged* chart does
not resolve missing dependencies; it fails with "found in Chart.yaml, but missing in charts/
directory". And per `CLAUDE.md` rule 8 a conditional dependency must be present even when
its condition is false, so `kube-prometheus-stack` ships in the archive too despite
defaulting off. `helm dependency update` must run on the built chart before `helm package`,
and the package carries both.

The consequence worth stating in the workflow: the archive is several MB and most of it is a
subchart that is disabled by default. That is the cost of a chart a stranger can install
without adding two repositories first.

### ⚠️ `publish-image.yml` is the model to copy — VERIFIED (`.github/workflows/publish-image.yml`)

It already does, on `push: tags: ['v*']`, what a chart publish needs: `permissions:
{contents: read, packages: write}` with no new secrets (`GITHUB_TOKEN` is enough for
`ghcr.io`, which matters because fork PRs never receive secrets), `concurrency` with
`cancel-in-progress: false` — *"never cancel a half-finished registry push"* — a
strict-version guard, and **a pull-back step that scrapes the published artefact**. Mirror
that shape; W3 is that last idea applied to a chart.

## W1 — Publish to `ghcr.io` as OCI

**W1.1 OCI, not a `gh-pages` Helm repo, and the reason is this repo's own rule.** A
classic Helm repo means a branch holding packaged `.tgz` files and a generated `index.yaml`
— **committed derived artefacts**, which this repo refuses everywhere: `dist/` is
gitignored, `compose/.generated/` is gitignored, and the chart has a build step precisely so
a second copy never exists in the tree. A `gh-pages` branch of packages is exactly the thing
those decisions were made to avoid.

OCI also lands the chart in the registry that already holds the simulator image, under the
same `GITHUB_TOKEN`, with no Pages, no branch and no index to regenerate.

```
oci://ghcr.io/chrisadkin8/charts/k8s-ai-observability
```

⚠️ Requires Helm **3.8+** for stable OCI support. CI pins `HELM_VERSION: v3.21.3`
(`ci.yml:50`), which is fine — but the workflow must install that pinned version rather than
whatever the runner ships, exactly as the existing jobs do.

**W1.2 Package from `dist/`, after `chart-build.py`.** Run the build, then
`helm dependency update` on the built chart, then `helm package`. See the facts above for
why `charts/` cannot be packaged directly and why the dependency step is not optional.

**W1.3 Reuse the strict-version guard.** `publish-image.yml` already runs
`chart-build.py --strict-version --skip-deps` and it has already caught a real mistake —
`appVersion='v0.6.0'` against tag `v0.7.0`, which would have shipped a chart that installs
green and runs the previous simulator. The chart publish must not be looser than the image
publish about the same file.

**W1.4 ⚠️ A NEW ghcr.io package is PRIVATE, and the failure is silent for you and fatal
for everyone else.** A package pushed by `GITHUB_TOKEN` starts private unless its visibility
is set or it inherits from the repository. The simulator image is public — anonymous pull
returns 200, checked 2026-08-04 — but that is an *existing* package whose visibility was
settled at some point. **The chart is a new one and starts fresh.**

The failure mode is this repo's signature: the push succeeds, `helm pull` works for whoever
ran it because they are authenticated, and every stranger gets a 401 that reads like the
chart does not exist. Artifact Hub cannot index it either, so W4 fails for a reason that
points nowhere near the cause. **Set the package public and assert it from an unauthenticated
client — see W3.1.**

**W1.5 ⚠️ Do not publish a floating tag.** The image publishes `:latest` and that precedent
makes it the obvious wrong default here. Helm resolves **versions**, not registry tags; a
`:latest` chart tag invites `helm install oci://…:latest`, which pins nothing and changes
under people silently. Publish the version and only the version.

**W1.6 Same guards as the image workflow:** `cancel-in-progress: false`, `contents: read` +
`packages: write`, no new secrets, and a `workflow_dispatch` with a required explicit
version so the path can be exercised before a real tag exists.

## W2 — Make `version` mean something

**W2.1 Decide the bump policy and write it where the number is.** `Chart.yaml` already
states what the two versions *mean*; what is missing is what happens on a release. Settle
it explicitly — the plain reading of the existing comment is that `version` moves when the
chart's templates or values change and `appVersion` moves every release — and record the
consequence: **a release that changes no chart template still publishes, so `version` must
move anyway or the push is rejected.**

**W2.2 Set the first published version deliberately.** `0.1.0` has been the value since the
chart landed and is wrong by the repo's own policy — the templates have changed repeatedly
since, including thirteen render-time assertions. Whatever number is chosen, it is the one
consumers pin first and cannot be reused.

**W2.3 Cross-check it like the pins are cross-checked.** `chart-build.py` already compares
`Chart.yaml`'s dependency pins against `config.sh` **by name** and `appVersion` against the
release tag. Add the chart version to what it refuses to let drift: publishing the same
`version` twice must fail locally, not at the registry.

**W2.4 ⚠️ `artifacthub.io/prerelease: "true"` stays for the first listing**, and the
condition for removing it is written down rather than left to feel. It is honest while the
chart has never been installed by anyone outside this repo.

## W3 — Verify by consuming, not by pushing

A successful `helm push` proves bytes moved. It does not prove the chart installs, and this
repo does not ship unverified claims.

**W3.1 Pull the published chart back ANONYMOUSLY and render it.** `helm registry logout
ghcr.io` first, or run it in a job with no credentials — otherwise this step verifies only
that the person who pushed can read what they pushed, which is true of a private package and
is the one thing W1.4 says must not be assumed. Then `helm pull oci://…` at the version just
pushed, then `helm template` it both ways round: with the stack disabled (the BYO case the
chart exists for) and enabled (greenfield, where the subchart's own templates render too).
`task chart` already renders both ways locally; this is the same assertion against the
*published* artefact.

**W3.2 ⚠️ Assert the render is not empty and contains the dashboards.** A chart packaged
from `charts/` instead of `dist/` renders **successfully** and produces no ConfigMaps,
because `.Files.Get` silently returns empty for a missing file. That is the failure this
whole build step exists to prevent, and it is invisible to `helm lint`, to `helm package`
and to a push that exits 0. Grep the rendered output for a known dashboard `uid` and fail if
it is absent.

**W3.3 Assert the drift guards fire on the published chart**, the way CI already drives all
thirteen render-time assertions to their failure. An assertion that has quietly stopped
firing looks exactly like one that passes.

**W3.4 Repeat W5's BYO install against the published chart.** W5 proved the path with the
locally built chart and gated publishing on it; this proves the *published* copy behaves
identically. It is the same procedure against a different subject, and it is what catches a
package that was assembled wrongly — which `helm lint`, `helm package` and a successful push
all pass in silence.

## W4 — Artifact Hub

**W4.1** Add the repository pointing at the OCI location. The annotations in `Chart.yaml`
(`artifacthub.io/license`, `artifacthub.io/prerelease`) have been inert since 0.5.0 and
start doing something the moment the listing exists.

**W4.2 ⚠️ Claim ownership, or the listing is anonymous and anyone can claim the same
reference.** For an **OCI** repository Artifact Hub verifies ownership by reading an
`artifacthub-repo.yml` metadata artefact pushed to the registry beside the chart — not by a
file in the tree, and not through the web form. Skip it and the chart still lists, without a
verified-publisher badge and with nothing tying it to this repo.

That makes it a **workflow step, not a browser step**, which is exactly why it is easy to
miss when the rest of W4 is a form: push it from the same job that pushes the chart, so the
two cannot drift apart. The whole point of W4 is that a stranger finds this chart and trusts
it; an unverified listing undercuts the second half.

**W4.3 ⚠️ Record the listing in `CHANGELOG.md` under `Changed` as a repository-settings
entry**, the way 0.4.0 recorded Dependabot alerts and the `main` ruleset — *"not in the
tree, recorded here because nothing else would show it"*. A distribution channel that exists
only in someone's browser session is undiscoverable by every other means.

## W5 — Prove the BYO path end to end

**W5.1** `prompt-packaging.md`'s acceptance criterion has been unproven since 0.4.0: it
needs a clean cluster with a **foreign** Helm release name, which means tearing down the
local kind cluster. Every other part of that path is tested.

**W5.2 The test is the chart's own premise**, so run it as a stranger would: install
kube-prometheus-stack under a release name this repo would never choose, then install the
chart against it with `releaseLabel` set to match, and open a board.

⚠️ **Here that is the chart built into `dist/`, not a published one — nothing is published
yet, and that ordering is deliberate.** This run is the gate on whether publishing is worth
doing at all. **W3.4 repeats the identical install against the published artefact**, which
is the copy a stranger actually gets and the only one that can expose a packaging fault.
Same test, two subjects; neither replaces the other.

⚠️ **Both failure modes are silent** (`CLAUDE.md` and `docs/byo-prometheus.md`): a wrong
`releaseLabel` means Prometheus never adopts the rules, so every derived panel is empty; a
wrong `grafana.dashboardLabel` means the sidecar never imports the board and `/d/<uid>`
404s. Every object reports itself successfully created either way. **`helm test` is the only
thing that checks them against a live cluster, and it is opt-in** — so the proof must run it
rather than assume it.

**W5.3** Record the result either way. If it works, the acceptance criterion is struck
(`CLAUDE.md` rule 16) with what was run. If it does not, that is a finding and it blocks
W1 — publishing a chart whose headline path is broken would be worse than not publishing.

## Non-goals

- **A `gh-pages` Helm repository.** See W1.1. If OCI turns out to be unworkable, that is a
  finding to record, not a silent fallback to committing packages.
- **Removing the build step.** It exists because `.Files.Get` cannot read outside the chart
  directory and a second committed copy is refused. Packaging from `dist/` is the cost.
- **Signing or provenance** (`helm package --sign`, Sigstore). Worth doing later; it is a
  key-custody decision, not a distribution one, and bundling it here would stall the release.
- **Publishing on every push to `main`.** Release tags only, as the image is. A chart
  version per commit is noise consumers cannot pin against.
- **The KEDA testbed and the disruption drill.** Separate releases with their own theses.

## Acceptance criteria

1. The publish packages from `dist/` after `chart-build.py`, never from `charts/`.
2. Dependencies are vendored into the package deliberately, and the workflow says so.
3. `chart-build.py --strict-version` gates the chart publish as it gates the image publish.
4. The chart `version` bump policy is written where the number is, and re-publishing an
   existing version fails **locally** rather than at the registry.
5. **The published chart is pulled back, rendered both ways, and asserted non-empty against
   a known dashboard `uid`** — the packaged-from-`charts/` failure is caught.
6. No new CI secrets: `GITHUB_TOKEN` only.
7. `workflow_dispatch` can exercise the whole path before a real tag exists, with a required
   explicit version.
8. The BYO path is proven end to end with a foreign release name, including `helm test`,
   **twice and against two subjects**: W5 against the locally built chart, which gates
   publishing, and W3.4 against the published artefact, which is the copy a stranger gets.
   Both results recorded either way.
9. `artifacthub.io/prerelease` stays `"true"`, with the condition for removing it written
   down.
10. The Artifact Hub listing is **ownership-verified** — `artifacthub-repo.yml` is pushed
    to the registry by the same job that pushes the chart, not added by hand — and the
    listing is recorded in `CHANGELOG.md` as a repository-settings entry.
11. The docs distinguish the two audiences rather than replacing one with the other: a
    **user** installs with `helm install oci://…` (README, `docs/byo-prometheus.md`, the
    chart README), and a **contributor** still builds locally with `task chart` to test a
    template change. `task chart` is not going away and the docs say who each path is for.
12. The chart package is **public**, asserted from an unauthenticated client, and no
    floating tag is published.

## Process

One logical change per commit. **W5 → W2 → W1 → W3 → W4.**

W5 first, deliberately: it is independent, it is the chart's whole premise, and a failure
there means there is nothing worth publishing yet. W2 before W1 because the first published
version cannot be taken back.

⚠️ **Do not push to the registry until W3 exists.** A published chart that nobody has pulled
back is a claim, and this repo does not ship those.
