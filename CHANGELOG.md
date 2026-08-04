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

### Added

- **`docs/releasing.md`** — the release procedure, which existed only in commit messages.
  Three of the four releases cut on 2026-08-04 hit a bug in the procedure rather than in
  the code, and the step that catches them (tag locally, run the strict check, *then* push)
  is the one that looks skippable.
- **`doc-claims` now checks the CI job count**, because `CONTRIBUTING.md` claimed six jobs
  when there were eight and named `upstream-drift` as the only job absent from pull
  requests after `settings-drift` had joined it. Same class the checker already covers for
  dashboard ids and metric counts, and the derivation was already there.

### Changed

- **`doc-claims` now checks two CI counts, not one.** "Jobs beyond `fast`" and "expensive
  jobs `fast` gates" are different sets — eight against five — and conflating them is the
  easy mistake, so each has its own derivation. The second check found a stale "four" in
  **`CLAUDE.md` and `CONTRIBUTING.md`** on its first run, one of which a manual review of
  the same file had already missed.

- **`chart-build.py` has a `--selftest`**, and it was the last script in `scripts/` without
  one — while being the script that produced the most bugs. All three were *decisions*
  rather than I/O, so the fixtures are text and lists: a regex that could not match
  `tag: ""` (the value meaning "track appVersion", and therefore the correct one), the
  re-use guard counting the tag being released against itself, and the off-tag hole that
  is still open and still marked. Both fixable bugs were reintroduced deliberately to
  confirm the selftest fails on them. It runs in `task chart`, `task preflight` and CI.

- **`CLAUDE.md`**: a rule that `zsh` is not `bash` and CI is `bash` — two bugs on
  2026-08-04 were invisible in the local shell, one of which could have skipped the cluster
  jobs on a code change. The Map gains the files that landed that day, and the corrected CI
  timings move to `docs/ci.md`, which owns that topic now that it exists.

## [0.9.0] — 2026-08-04

**This release is about the verifiers, not the thing they verify.** Four bugs surfaced in a
single day and every one of them was in code whose job is to check other code: `helm test`
reporting a passing chart as a failure, a version guard colliding with the release it
gated, an archive check failing on a correct archive, and two CI assertions that could only
ever fail open. In a repo whose product is verification, that is the part that had been
held to the lowest standard, because nothing verified it.

So the fixes come with the machinery that stops them recurring. `check-sigpipe.py` makes an
entire failure class mechanical. `chart on kind` runs the chart's own `helm test` on every
pull request instead of only at release time, which is how the defect in chart `0.2.0`
reached the registry. `docs/ci.md` gives the pipeline a page of its own, and `doc-claims`
now checks that page against the workflow that generates it.

MINOR by this file's table. Nothing an existing cluster can see moved: no metric, recording
rule, alert, dashboard `uid`, profile or Terraform input. `verify.sh` asserts exactly what
it asserted before; it just refuses to spend 24 minutes doing so when the answer is already
determined. The argument-parsing change is the only one a user can notice, and it rejects
input that was previously ignored.

### Added

- **`chart on kind (helm test, foreign Prometheus)` is a required check.** The chart's own
  verification now runs on every pull request against a kube-prometheus-stack installed
  under a name this repo would never choose, asserting both that `helm test` **fails** on a
  mismatched `releaseLabel` and that it passes when set. The steps live in a composite
  action shared with `publish-chart.yml`, so the local build and the published artefact are
  checked by one implementation rather than two copies.
- **`scripts/check-sigpipe.py`**, which finds pipes whose consumer stops reading before the
  producer finishes. Every hit is rewritten or carries a written justification.
- **[`docs/ci.md`](docs/ci.md)** — what CI proves, how `full` and `lite` differ, and why the
  check names are load-bearing.
- **`.github/required-checks.txt`** plus the `branch ruleset vs required-checks.txt` job,
  which together close a coupling that previously existed only in GitHub settings.

### Fixed

- **Nothing on a pull request ever installed the chart, so `helm test` only ran at release
  time.** The `helm chart` job lints and renders without touching a cluster; the
  `full stack on kind` job installs through `install.sh`, the *script* path. The chart's own
  `helm test` — the only check of the two silent-failure labels against a live cluster — ran
  solely in the publish workflow, on a tag. That is how `0.2.0` reached the registry
  reporting a passing chart as a failure. A new `chart on kind` job now runs it on every
  pull request, asserting **both** that `helm test` fails on a mismatched `releaseLabel` and
  that it passes when set. The steps live in a composite action shared with
  `publish-chart.yml`, so the two subjects (local build, published artefact) cannot drift.

- **A wrong `RELEASE_LABEL` took over ten minutes to report, against ~90 seconds to pass.**
  Every check burned its full poll before giving up. A wrong label is not a race — the
  objects carry the label they carry — so `verify.sh --byo` now compares it against the
  Prometheus selectors once, up front, and refuses to start. Measured: **1 second**, naming
  what the cluster actually selects. It fires only on positive evidence and stays silent
  when it cannot know, so a slow-but-healthy cluster is unaffected; the poll budgets are
  unchanged.

- **Two CI assertions could only ever fail open**, plus two in the chart's own test. Under
  `pipefail`, a consumer that stops reading early SIGPIPEs the producer and the pipeline
  returns 141 — so an `if` guarding an assertion was false *exactly when* the thing it
  looked for was present. In the changes filter the same shape would have read a large code
  change as "markdown only" and skipped the cluster jobs. ⚠️ It reproduces under `bash` and
  not under `zsh`, which is why it survived local testing. `scripts/check-sigpipe.py` now
  makes the class mechanical; every remaining hit carries a written justification.

- **`install.sh` and `teardown.sh` only ever checked their second argument**, so
  `install.sh local --skip-monitoring --lite` accepted `--lite` in silence and installed the
  full stack. `--lite` is the realistic typo, because `LITE` is an environment variable and
  not a flag. Both scripts now reject any unrecognised argument, and the usage message says
  where `LITE` really goes.

- **`helm test --logs` reported a passing chart as a failure — chart `0.2.0` → `0.2.1`.**
  The test's ServiceAccount, ClusterRole and ClusterRoleBinding carried
  `helm.sh/hook: test`, and `helm test --logs` fetches logs for *every* resource with that
  hook, by name, as though it were a Pod. They are named `<fullname>-test` while the Pod is
  `<fullname>-test-preconditions`, so Helm looked for a pod that never existed:

  ```
  Phase: Succeeded   (x4)
  Error: unable to get pod logs for rig-k8s-ai-observability-test: pods ... not found
  exit 1
  ```

  Every precondition had passed. This is the exact command the chart README tells people to
  run, so the first thing a new user would have seen is a red result on a working install.
  The three RBAC objects are now plain release resources, which is also the right lifecycle
  — `helm uninstall` removes them, where as hooks they leaked past uninstall.

  ⚠️ **`0.2.0` remains published and is not withdrawn.** Registry versions are immutable;
  it installs correctly and only mis-reports `helm test --logs`. `0.2.1` supersedes it.

  Found by the publish workflow's own verify job, which is the first thing in this repo's
  history to run `helm test` in CI. Every previous run of it was by hand, where the exit
  code was read by a human who had already seen `ALL PRECONDITIONS PASSED` scroll past.

- **The chart publish failed on its first real run, on an archive that was correct.** The
  step that opens the packaged `.tgz` to prove the dashboards are inside piped `tar tzf`
  into `head`, and under `set -o pipefail` the consumer exits first, SIGPIPEs `tar` and
  fails the step with exit 141 — before a single assertion ran. The `tar tzf | grep -q`
  form in the same step was worse: it is *racy*, and passed locally on the same 363-entry
  archive. The listing is now written to a file and read from there, which also runs `tar`
  once instead of once per dashboard.

  ⚠️ **The `v0.8.0` chart is therefore published from `main`, not from the tag.** The image
  published from the tag normally; only the chart job failed, and it failed before pushing
  anything. The chart content at `main` is identical to the tag — the commits between them
  touch only this workflow and `chart-build.py` — but the artefact was not built from the
  tagged commit, and that is recorded here rather than left to be inferred.

- **A publish that failed could not be retried without burning a chart version.** The
  version guard excluded only tags pointing at `HEAD`, so a retry dispatched against the
  same release tag from a branch that had moved on was refused for re-using a version the
  failed run never pushed. `chart-build.py --publishing-as <tag>` now names the release
  being published; a genuinely re-used version still fails.

## [0.8.0] — 2026-08-04

**This release makes the chart installable without cloning anything, and proves it by
consuming the published artefact rather than by pushing one.** A successful `helm push`
demonstrates that bytes moved; it says nothing about whether the chart installs. So the
publish workflow pulls its own output back **with no credentials** — which is also the only
way to establish that the package is public — renders it both ways, drives all nine
render-time assertions to failure, and installs it on kind against a foreign Prometheus
with `helm test` required to *fail* on the default `releaseLabel` and pass when it is set.

**Proving the path is what found the two bugs below, and neither was visible to CI.** The
BYO story had been documented, flagged and reasoned about since 0.4.0 without once being
run end to end under a foreign release name; doing that took `verify.sh --byo` from six
failures to 26 passes. The chart's `helm test` had been pointing at an image that no longer
exists, so the single check guarding the two silent-failure labels could not start — and
nothing noticed, because running it needs a cluster and CI never did.

MINOR by this file's table. Nothing an existing cluster can see moved: no metric, recording
rule, alert, dashboard `uid`, profile or Terraform input. The script fixes are strictly
more permissive — names that resolved before still resolve first, by the same construction
— and the chart changes are additive.

### Fixed

- **The BYO path could not work under any release name it was built for.** `KPS_RELEASE`
  exists so a cluster whose monitoring release is *not* called `kube-prometheus-stack` can
  be served, and the scripts built Service names from it — `${KPS_RELEASE}-prometheus`.
  Helm's fullname template collapses the prefix only when the release name already
  contains the chart name, so that construction resolves for `kube-prometheus-stack`
  (`kube-prometheus-stack-prometheus`) and for nothing else: release `acme-mon` produces
  `acme-mon-kube-prometheus-s-prometheus`. The flag therefore worked for exactly the one
  release name that never needed it.

  It failed **as a different bug**, which is why it survived. `verify.sh` port-forwarded a
  Service that does not exist, so every PromQL query returned nothing and every series
  check reported a *selector* problem, naming `RELEASE_LABEL` — which was correct all
  along. Following the suggested fix would not have helped.

  Service, Deployment and Secret names are now **resolved from the cluster** by
  `resolve_kps` in `scripts/config.sh`, trying the constructed name, then
  kube-prometheus-stack's `app=` labels, then the subcharts' `app.kubernetes.io/` labels,
  then the operator's own `prometheus-operated`. A miss is now fatal and prints every
  pattern it tried. Affects `verify.sh`, `grafana.sh`, `prometheus.sh` and `install.sh`
  — including a greenfield install under a custom `KPS_RELEASE`, where the operator
  readiness wait silently matched nothing and `|| true` swallowed it.

  Measured on a kind cluster with kube-prometheus-stack installed as **`acme-mon`**:
  `verify.sh --byo` went from six failures and a >10-minute run to **26 passed, 0 failed,
  3 skipped in ~90 seconds**, and `grafana.sh` opened both boards.

- **The chart's `helm test` image no longer existed.** `bitnami/kubectl:1.31` returns
  `not found` — Bitnami retired much of its public Docker Hub catalogue — so the pod sat
  in `ImagePullBackOff` and the only live check of the two silent-failure labels could not
  start. Nothing noticed because CI never runs `helm test`; it needs a cluster. Now
  `alpine/k8s`, which carries both kubectl and bash (`registry.k8s.io/kubectl` is
  distroless and exits 128 on the hook's `/bin/bash`). The pin was also five minors out of
  skew, and `check-doc-claims.py` now holds it inside Kubernetes' supported ±1 window.

### Added

- **The chart is published to `ghcr.io` as an OCI artefact** (`.github/workflows/publish-chart.yml`),
  packaged from `dist/` with its dependencies vendored, and then **verified by consuming
  it**: pulled back with no credentials — so "the package is public" is asserted rather
  than assumed — rendered both stack settings, all nine render-time assertions driven to
  failure, and installed on kind against a foreign kube-prometheus-stack with `helm test`
  required to *fail* on the default `releaseLabel` and pass when it is set correctly. No
  floating tag: Helm's OCI tag is the chart version, and a `latest` consumers can pin is
  the opposite of pinning.

- **`artifacthub-repo.yml`**, the ownership claim for the eventual Artifact Hub listing.
  For an OCI repository this is not a file Artifact Hub reads from the tree — it is a
  metadata artefact pushed to the registry beside the chart, which makes it a *workflow*
  step and therefore the easy one to miss when the rest of the setup is a browser form.
  The publish job pushes it, and skips loudly while the `repositoryID` is still a
  placeholder rather than pushing something that would claim nothing.

### Changed

- **Artifact Hub listing created** (repository settings, not in the tree — recorded here
  because nothing else would show it). Repository `k8s-ai-observability`, kind *Helm
  charts*, pointing at `oci://ghcr.io/chrisadkin8/charts/k8s-ai-observability`; its
  `repositoryID` is now in `artifacthub-repo.yml`. Named after the chart rather than the
  publisher because an OCI entry maps to **one** chart, so a publisher-style name would be
  spent on this one and the next chart would need a different scheme.

  ⚠️ **It is listed but not yet ownership-verified, and it will show as broken until the
  first release.** Artifact Hub is already trying to index that OCI reference and nothing
  has been published there — the chart lands with the next tag. Verification then needs the
  `artifacthub-repo.yml` artefact pushed beside it, which the publish job does
  automatically; running the `oras push` by hand once closes the gap a release earlier.
  The listing's display name is also unset, so it currently shows the bare slug.

- **Chart `version` 0.1.0 → 0.2.0, and the bump policy is written where the number is.**
  It had not moved through four releases because nothing said it had to. The rule that was
  missing: a release publishes the chart even when no template changed, so `version` must
  move anyway or the push is rejected — registry versions are immutable.
  `chart-build.py` now refuses a version any tag already shipped, checked against git
  rather than the registry so it fails locally instead of at `helm push`.
  `artifacthub.io/prerelease` stays `"true"` and now carries the condition for removing it.

## [0.7.1] — 2026-08-04

**This release makes the acceptance suite tell the truth about its own clock.** Every
timeout `verify.sh` printed was roughly half the one it granted, and ~60% of a run was
spent asleep — both because a port-forward that was written to be reused was rebuilt on
every single query. The rig that exists to catch instruments lying about the world had an
instrument lying about itself.

⚠️ **Nothing here changes what a cluster looks like.** No metric, recording rule, alert,
dashboard `uid`, profile, chart template or Terraform input moved; `manifests/`, `charts/`,
`helm/`, `terraform/` and `kind/` are untouched. `verify.sh` asserts exactly what it
asserted before, on the same objects, with the same expressions. It is a PATCH by this
file's own table.

### Fixed

- **`verify.sh` rebuilt its Prometheus port-forward on every single query, and slept 4s
  each time.** `prom_pf_ensure` set `PF_PID=$!`, but every caller reaches it through
  command substitution — `x="$(promql_count ...)"` — which runs in a **subshell**, so that
  assignment never reached the shell that reads it. `prom_pf_up` therefore saw an empty pid
  on every call and re-established the forward each time. The self-healing forward never
  healed; it only ever re-established, and leaked an orphaned `kubectl port-forward` per
  call.

  **Measured, not inferred.** On CI run 30867055387 consecutive single-shot checks land
  exactly 4.03s apart, run after run, while the two checks that issue no PromQL land 0.0s
  apart: ~37 calls, ~149s of a 247s `verify.sh`. Driving the real function text through a
  stub `kubectl`, six queries cost **24.1s before and 4.1s after** — one settle instead of
  six. The pid now lives in a file, because a file survives a subshell and a variable
  does not.

  ⚠️ **The Grafana forward beside it is the same design and always worked**, because
  `grafana_uid_check` is a plain function call rather than a substitution. Identical code,
  different call convention, opposite outcome. That contrast is now written down where the
  next helper that backgrounds something will be read.

- **Every poll budget in `verify.sh` was ~1.8x larger than it said**, as a direct
  consequence. `DCGM_POLL_ATTEMPTS=24` against a `sleep 5` reads as 120s and printed "120s"
  in two failure messages; the real budget was 216s, and 312s in check 3, which issues two
  queries per pass. `LLMHighTTFT` said "up to 5m" and granted 420s; `GPUHighUtilization`
  said 6m and granted 504s.

  ⚠️ **This is why the budgets moved first and the forward second.** Landing the forward fix
  alone would have cut every timeout in this file by ~45% in one commit, silently, while the
  diff read as a pure speed-up. Bounds are now wall-clock deadlines in seconds, so a stated
  budget and a real one cannot diverge again — including `grafana_uid_check`, whose count
  was already honest, so that "every poll here is bounded in seconds" is greppable rather
  than an audit.

- **L8's budget was the one that was genuinely too small.** Measured across three runs on
  2026-08-04: the `full` leg converges in 12.1s **every time**, to two decimal places, while
  `lite` took 12.1s, 129.5s and **201.7s** on the same commit range — 22 of its 24 attempts,
  roughly 18 seconds from a red leg on a rig that was working perfectly. Now 420s.

### Added

- **`verify.sh` reports how long L8 took to converge, and its residual while it waits.**
  A new `promql_value` helper reads the number inside a series rather than counting series;
  L8 prints the worst relative residual every ~30s while polling and appends the elapsed
  time to its PASS line. Diagnostic only — no check asserts on it.

  **Because the cause is not yet known, and the obvious ones are ruled out rather than
  assumed.** The 201.7s run's diagnostics show Prometheus at `RESTARTS 0`, no OOM or
  eviction events, rule-group `evaluationTime` of 2.4ms on a 30s interval, and llm-sim
  scrape targets identical to the `full` leg's — 15s interval, ~1.3ms per scrape, both
  healthy. A "converges at a fixed simulator age" hypothesis was tested against all six legs
  and **fails**: the age at convergence spreads 188s to 391s. What survives is that `lite`
  converges at 284-391s of simulator age against `full`'s 188-254s — consistently later,
  non-overlapping, across three runs, through a mechanism none of the instrument-level
  explanations covers.

  ⚠️ **The first post-fix run points at the leak itself, and that is one run, not a
  proof.** On run 30870290833 L8 converged in **0s on both legs** — it passed on the first
  poll — and `lite` got there at 177s of simulator age, below its own previous 284-391s
  band and below even `full`'s previous 188-254s. The mechanism that would explain it is
  the orphaned port-forward per query: ~37 abandoned SPDY streams accumulating against a
  Prometheus capped at 512Mi with a 100m CPU request is a load `full` (2Gi, 250m) would
  absorb and `lite` would not. Plausible, consistent, and **still unproven** on a single
  observation. The instrumentation stays, and the next slow leg settles it.

### Changed

- **CI: `fast` now gates `compose`, `chart`, `image` and `stack`.** It is 9-12s and covers
  the selftests, doc-claims, the rule tests and both syntax passes; when it is red the two
  kind legs are ~14 runner-minutes spent confirming something already known.

  ⚠️ **`stack` takes it as `needs: [changes, fast]` plus `if: always()`, and the `always()`
  is load-bearing.** A failed dependency SKIPS a job, and a skipped **matrix** job does not
  interpolate its name — so the two required checks would never report and the PR would sit
  pending, which is precisely the bug the step-level gate already exists to avoid. Running
  always and gating the steps on a single `RUN_STACK` expression keeps both names reporting
  whatever `fast` did, with its own `::notice::` saying which of the two reasons applied.

- **CI: diagnostics are no longer collected and uploaded on green runs.** `always()` was
  bundling the logs of a stack that worked, on both kind legs and the compose job, every
  run, retained 14 days. `!= 'success'` still covers failure, cancellation and timeout,
  which is the whole reason it is not `failure()`.

- **The CI timings in `CLAUDE.md` and `CONTRIBUTING.md` were wrong in the same direction.**
  Both said the two kind legs take ~5.5-6 minutes *each*, and `CONTRIBUTING` priced `chart`
  and `image` at "about a minute each" against a real 15s and 22s. The word doing the damage
  was **"each"**: it averaged away the fact that `lite` was the SLOWER leg at 6m39s-8m32s
  against `full`'s 5m19s-6m25s, which is backwards for a profile that drops Alertmanager,
  kube-state-metrics, node-exporter and ~100 rules. Nobody had noticed the trimmed profile
  was the critical path because no figure in the repo distinguished the two.

  Both files now carry the post-fix measurement from run 30870290833 with its run id, and
  the superseded claims are struck rather than deleted.

### Measured

The effect of the two `verify.sh` fixes, `full` / `lite`, before → after:

| | before | after |
|--|--|--|
| `verify.sh` duration | 247.0s / 383.3s | **215.4s / 160.0s** |
| L8 convergence | 12.1s / up to 201.7s | **0s / 0s** |
| stack job | 5m19s-6m25s / 6m39s-8m32s | **6m12s / 4m42s** |
| whole workflow | 7m02s-8m47s | **6m50s** |

Before is three runs (30866321783, 30867055387, 30867572482); after is one
(30870290833). `lite` stops being the critical path.

## [0.7.0] — 2026-08-04

**This release makes the repo check its own prose, and writes down the law it had been
carrying in its head.** 0.6.0 shipped an SLO and then corrected seven numbers by hand —
alert counts, the emitted-metric count, the `L1-Ln` range in two files, the PromQL query
count, the `${datasource}` count — because a fact stated in prose had forked from the code
that owned it. Twelve of this repo's first 76 commits were corrections of work already on
`main`, and that class dominated them.

Every other failure class here already had an executable check: metric drift has
`check-vllm-buckets.py`, DCGM parity has `tests/contracts/`, silent chart labels have
render-time `fail`s driven to failure in CI. Prose drift had hand-correction sweeps three
releases running. It has a checker now, `task preflight` runs it with every other
no-cluster gate in one command, and CI runs it on every push.

The rest follows from the same idea. `CLAUDE.md` collects the invariants that were
scattered across `config.sh` comments, file headers and the authoring briefs, so the briefs
can cite a rule instead of restating it. `task outstanding` derives the open-item list
rather than duplicating it into a TODO file. And the sizing claim that had been marked
unverified since 0.4.0 was finally measured — it was wrong, and the way it was wrong is
worth reading.

⚠️ **Nothing here changes what a cluster looks like.** No metric, recording rule, alert,
dashboard `uid`, profile or Terraform input moved. It is a MINOR by this file's own table
because it adds new **targets and checks**, not because anything breaks.

### Added

- **`CLAUDE.md` — the repo's standing law, in one file.** Sixteen rules, each traceable to
  the file that owns it or the commit that taught it: the fixtures that must never be
  driven, JSON profiles, `model_name` as an identity, tuning against `llmsim_capacity_rps`,
  the bucket rules and why an SLO threshold must **be** a boundary, polling anything that
  races its producer, writing expected values before running, `verify.sh` holding invariants
  only, pins living twice and cross-checked by name, dashboard `uid`/revision discipline,
  and how outstanding work is marked and struck.

  The invariants were all already written down. They were also **scattered**, and restated
  in full inside every authoring brief — which is how copies fork. This is pointers, not
  restatements: where a rule has an owning file it cites it. The two live briefs now cite
  rules by number, which removed about a hundred lines of duplicated preamble between them.

- **`scripts/check-doc-claims.py` and `task doc-claims` — prose numbers checked against the
  code that owns them.** Seven checks, ~20 claims, and **no copy of any truth**: every
  expected value is derived at run time. Dashboard ids are blessed from
  `manifests/dashboards/README.md` itself, so the external DCGM board 12239 needs no
  allowlist; the emitted-metric count comes from running `llm-sim.py --print`; the alert
  count from `- alert:` entries; the `L1-Ln` range from `verify.sh`'s own pass/fail calls
  rather than its section comments, because a comment is prose and could itself be what
  drifted; the query count from the numbered block in `observability.md`; the
  `${datasource}` count from the board JSON; and the pinned Kubernetes version from
  `kind/gpu-sim.yaml`, which the README badge had been asserting unchecked.

  ⚠️ **A checker that finds nothing must die, not pass.** Every check treats "no claims
  matched" as fatal and names itself, so a reworded claim fails loudly instead of quietly
  retiring. `--selftest` pins all six matchers to fixtures carrying three decoys — one of
  which is a regression test: on its first real run the `emits` matcher flagged *"Real vLLM
  only ever emits one surface"*, a true sentence about metric **surfaces** on a line
  mentioning vLLM. The context regex named the subject rather than the counted noun.

  Not checked, and the script says why: the drift-gap count. The only local upstream list is
  a fixture whose own header calls it *"a STUBBED upstream vLLM metric set — NOT a copy of
  the real one"*. Deriving from it would compare the prose against a fiction and pass.

- **`task preflight` — every no-cluster gate in one command.** `selftest`,
  `compose-selftest`, `drift-test`, `doc-claims`, `rule-tests`, `chart`, in cheap-first
  order. Deliberately excluded: `image` and `compose` need docker, `verify.sh` needs a
  cluster, and the live drift check needs the network, which its weekly job owns.

- **`task outstanding` — the open-item list, derived.** Items are already marked in the file
  that owns them and struck when done; what was missing was a way to see them without
  reading a 940-line append-only changelog. ⚠️ It matches **phrases**, not the `⚠️` glyph —
  there are 91 of those in tracked markdown and it is this repo's general warning marker.
  The phrase list is curated and therefore incomplete by construction, so every run says so:
  a clean run means the matchers found nothing, not that there is no outstanding work.

  A TODO file was the obvious answer and the wrong one — a second copy of every line it
  printed, unverifiable, and the first thing here to rot.

- **`docs/record-demo.md` — how to record the README demo GIF**, the one visual asset no
  script can re-render, because a human has to drive the board.

- **Three badges**, for the two things a stranger can consume without cloning: the published
  boards 25618 and 25620, and `ghcr.io/vllm-metrics-sim`. The board badges cost nothing to
  keep honest — `doc-claims` already blesses every grafana.com id in tracked markdown, and a
  badge URL is markdown.

- **`SECURITY.md`, `CODE_OF_CONDUCT.md`, a PR template and two issue forms**, taking GitHub's
  community profile from 57% to 100%. Written for this repo rather than pasted: the bug form
  asks which of six paths you were on and names the command that produces the evidence the
  next field wants, and there is a second form for **upstream drift** — the failure this rig
  exists to catch, and the one a stranger is most likely to spot first. `SECURITY.md` leads
  with what is true rather than with an address: this is a test rig, Grafana runs anonymous
  on purpose, and the Terraform bills real money.

- **The organisation logo for grafana.com is first available here**, not in 0.6.0. It is
  described under that release because the entry was written before the tag was cut, and
  `9a21f67` landed after it — so `v0.6.0`'s notes describe a file that release does not
  contain. Recorded in both directions rather than silently moved: the description stays
  where a reader has already found it, and both sections now say which tag it is in.

- **A "Contributing and support" section in the README**, closing the one gap that graded
  badly against every published README standard at once. `SECURITY.md`,
  `CODE_OF_CONDUCT.md` and both issue forms already existed and already scored on GitHub's
  community profile — but the profile checks that a file *exists*, not that a reader can
  find it, and none of the four was reachable from the page most people actually read.
  Grepping the README for `issue|discussion|support|help` returned nothing.

  The section routes the four requests that actually arrive — an empty panel, a bug or an
  upstream drift, a security report, a change — to the file that owns each, and says *not*
  to open a public issue for the third. Discussions are disabled on the repo, so it does
  not offer them.

  **No response-time commitment is stated**, deliberately. `SECURITY.md` sets one for
  security reports and that is a narrower promise than a general one; inventing a second
  would be a claim about spare time that nothing enforces.

### Changed

- **The LITE sizing block in the README keeps the instruction and delegates the mechanism.**
  0.6.0's `8ea005e` put the whole 3-vs-4 GiB finding in Prerequisites, which was right
  about *where it bites* and wrong about how much of it a reader needs there. What stops
  you cold is "allocate 4, do not try 3"; the arithmetic behind it — reported vs allocated
  GiB, `MemTotal` floored by integer division, 2.83 reading as `2` — is what you want
  *after* it has already bitten. That now lives in
  [docs/troubleshooting.md](docs/troubleshooting.md), with the measurement stamp, and the
  README links to it in one line.

  `scripts/config.sh` moved with it. Its comment beside `KIND_MIN_MEMORY_GIB` used to say
  the README "says why", which stopped being true — the pointer now names troubleshooting,
  because a stale cross-reference between the constant and its explanation is exactly the
  drift class that comment exists to prevent.

- **`CONTRIBUTING.md` records why the README leads with "Try it" before "Install".** The
  order inverts what [Standard Readme](https://github.com/RichardLitt/standard-readme)
  asks for, and it is deliberate: the compose path needs no cluster and no prerequisites,
  so the fastest honest answer to "what is this" is a board you can look at. Written down
  because it has now been questioned once, and an undocumented deviation reads as an
  oversight to the next person who checks.

- **The Kubernetes badge is derived rather than asserted.** `kubernetes-v1.36.1` sat
  hardcoded in an `img.shields.io` URL on line 4 of the README with nothing verifying it —
  one node-image bump from quietly lying, in the most prominent line in the repo. It is now
  checked against `kind/gpu-sim.yaml`'s `kindest/node` pin, along with the same version in
  `docs/versions.md`.

- **The `dependencies` label exists.** `.github/dependabot.yml` had been requesting it since
  0.4.0 and silently getting nothing, because the label was never created — a config doing
  exactly what `install.sh` warns about when `LITE=1` is ignored under `--skip-monitoring`.
  Backfilled onto the five dependency PRs merged before it existed.

- **Repository settings** (not in the tree, recorded here because nothing else would show
  it, as in 0.4.0): the `v0.5.0` GitHub Release was backfilled — the tag had existed since
  2026-08-01 with no release, and 0.5.0 is the chart, the published image and the phase
  breakdown.

- **Five SHA-pinned actions bumped** by Dependabot: `actions/checkout` 5.1.0 → 7.0.1,
  `actions/upload-artifact` 5.0.0 → 7.0.1, `docker/login-action` 3.7.0 → 4.6.0,
  `docker/setup-buildx-action` 3.11.1 → 4.2.0, `docker/setup-qemu-action` 3.6.0 → 4.2.0.

### Fixed

- ⚠️ **The LITE sizing floor was unreachable, and had been since 0.4.0 said so.** The claim
  that `LITE=1` "lowers the floor to 3 GiB" carried an *unverified* marker because CI cannot
  settle it — the runner has ~16 GB and nothing there is under memory pressure. Measured at
  last on a constrained runtime, colima/aarch64:

  | Allocated | Docker reports | `kind-up.sh` reads | Result |
  |--|--|--|--|
  | 3 GiB — the advertised floor | 2.83 GiB | **2 GiB** | **refused before anything started** |
  | 4 GiB | 3.81 GiB | 3 GiB | `ALL CHECKS PASSED` |

  **Consumption was never the problem.** At 4 GiB the trimmed stack used **2.197 GiB of
  3.813 — 57.6%**, with zero restarts, zero OOMKills and nothing Pending, through every GPU
  check and L1–L9 including the phase breakdown within 0.1% and all four SLO ratios.

  ⚠️ The **floor** was unreachable, from a unit mismatch nothing in the docs would have
  revealed: `kind-up.sh` computes `mem_bytes / 1024 / 1024 / 1024` — integer division — so a
  runtime is judged by what it **reports** while a user sets what it is **allocated**. VM
  overhead takes ~0.2 GiB and truncation the rest. Asking for exactly the documented 3 GiB
  produced a `2 GiB` reading and an error telling you to raise it to 4: the floor refusing
  itself.

  Fixed in the docs rather than the check. Rounding instead of truncating would make the
  guard accept 2.83 GiB as "3", looser than a check that exists because Prometheus alone
  limits at 2Gi.

- **`docs/record-demo.md` could not be followed end to end**, found by review before anyone
  tried. `cd compose && docker compose up -d` persists in the reader's shell, and the profile
  edit twenty lines later is written from the repo root — so `compose/.generated/…` resolved
  to `compose/compose/.generated/…` and did not exist. A page whose entire purpose is being
  followed step by step, broken at step three. Both compose commands now run in a subshell.

- **25620 is current again, and the record says which artefacts moved.** Revision 3 carries
  the TTFT error-budget burn-rate panel and the catalog page carries the SLO section; both
  boards' short descriptions are no longer the upload form's placeholder. Confirmed against
  grafana.com's API rather than taken on trust. ⚠️ 25618's catalog page still renders the
  board 12239 link as literal text — the `]` landed after the number instead of before the
  URL — and that marker is narrowed to the surviving half rather than cleared.

- **The grafana.com logo question is closed, and the answer is that there is no requirement
  to meet.** Grafana's publishing documentation states no dimension, aspect ratio, file-size
  or format limit, so the 512×512 assumption could never have been checked against a spec.
  What settles it is acceptance: the API reports `hasLogo: true` on both boards.

## [0.6.0] — 2026-08-02

**This release gives the rig an SLO, and then makes the docs tell the truth about it.**
0.5.0 finished the phase breakdown and left three separate warnings not to build an
objective on a percentile here, with no answer to what you should build instead. This one
answers it: a ratio evaluated *at* a bucket boundary interpolates nothing, so it carries
none of the caveats the rest of the board is full of. The bucket layout was never the
obstacle — it is the constraint that tells you where the threshold has to sit.

The rest is consequence. A metric that was always zero stopped being emitted, because a
flat zero is a claim and a false one; every count in the prose that those two changes
invalidated was found and corrected; and the board screenshot was retaken, which obsoleted
a caveat and exposed a second figure worth explaining rather than editing away.

### Added

- **A TTFT service-level objective, expressed as a ratio at a bucket boundary rather than
  a percentile** — `llm:ttft:slo_ratio5m` / `30m` / `1h` / `6h`, with the standard
  fast/slow burn-rate pair over them (`LLMTTFTErrorBudgetFastBurn`,
  `LLMTTFTErrorBudgetSlowBurn`) and a burn-rate panel on the LLM board.

  **This finishes an argument the repo had only half of.** Three separate places told you
  *not* to build an SLO on a percentile here — not on prefill p95 (3.03x overstated), not
  on ITL p95, not on the 2s threshold — and none said what to do instead. The resolution
  is that every one of those caveats is a property of `histogram_quantile` interpolating
  *inside* a bucket. A ratio evaluated *at* a boundary does not interpolate at all, so it
  inherits none of them. The bucket layout was never an obstacle to an SLO; it is the
  constraint that says how to build one, and the objective is **99% of requests reach a
  first token within 2.5s**.

  ⚠️ **2.5 rather than the existing alert's 2s, because `TTFT_BUCKETS` has no 2.0
  boundary** — it steps `1.0, 2.5, 5.0`. `le="2"` matches nothing, the ratio records
  empty, and both burn alerts stay green forever on rules that read correctly. Not 1.0
  either: the steady tenant measures 99.368% there, 0.37 points of headroom against a 99%
  target, so jitter alone would have the *healthy* tenant reporting a blown budget. At 2.5
  it measures 100% and the saturated tenant 0.32%.

  **`LLMHighTTFT` is untouched and stays at 2s.** The two are different instruments — one
  an interpolated percentile that separates the shipped tenants, one a boundary that makes
  a ratio exact — and reconciling them would re-derive the profile arithmetic, `verify.sh`
  L3b's headroom and every existing promtool expectation. Nothing existing moved.

  **Four limits ship with it**, on the catalog page rather than buried here: your threshold
  is constrained to the boundaries you have; "exact" holds only because numerator and
  denominator share a target and scrape timestamps; a total stall reads as *healthy*
  because a request that never reaches a first token contributes no observation, so this is
  a latency objective and not an availability one; and the 6h window never fills on a rig
  that lives minutes.

  **Upgrading needs no action** beyond re-running `install.sh` to apply the rules. The
  alerts carry **no `for:`** — a deliberate break from the other seven alerts here, since
  the long window already provides that smoothing — and a traffic guard whose window
  matches each alert's own short window, without which an idle tenant reads `0/1e-9 = 0`
  and both fire hardest on a model serving nothing at all. `verify.sh` gains **L9**,
  asserting the four ratios record against a live Prometheus, which is the only place a
  boundary that does not exist in your exposition shows up.

### Changed

- **An organisation logo for grafana.com**, `docs/logos/org-chrisadkin.png`, generated by
  the same `docs/dashboard-logos.py` as the two board marks so the set cannot drift.

  ⚠️ **This entry is mis-filed and is left in place rather than moved.** `9a21f67` landed
  *after* the `v0.6.0` tag, so the file is not in that release and its GitHub notes — cut
  from this section — describe something you cannot get by checking out `v0.6.0`. It is
  first available in **0.7.0**.

  It is not a third board mark. The two boards are a die (hardware) and a panel frame
  (serving); the org mark is those two **fused** — the die with the serving signal running
  through it, edge to edge — so it reads as the thing they both came from rather than as a
  sibling sitting beside them on the org page. The first attempt was a frame with a
  threshold and two tenants through it, which *is* the LLM mark, and was discarded for
  exactly that reason.

  ⚠️ **Its die is blue where the LLM mark's frame is `DIM`, and that is a legibility fix
  rather than a preference.** `DIM` keeps a container from competing with its contents,
  which is right at 512px and wrong at 64: it is too close in value to the tile to survive
  the downsample, so the die dissolved and the mark became a green squiggle. Caught by
  rendering the 64px version, which is the only size that settles it.

- **`What you get` tightened from 53 lines to 47**, by cutting justification the repo
  already tells properly elsewhere rather than by dropping claims — every one of the eight
  bullets survives, and a term-by-term check confirms nothing it referenced was lost. The
  weekly-drift bullet was ten lines, half of them re-telling the 0.1.0/0.2.0 story that
  [docs/versions.md](docs/versions.md#keeping-them-honest) tells in full and that the
  bullet did not even link to; it now makes the point in two clauses and links there. The
  dashboards bullet dropped a four-way enumeration of how the JSON is consumed, which is
  detail rather than value at that position.

- **`Bring your own Prometheus` moved out of the README into
  [docs/byo-prometheus.md](docs/byo-prometheus.md).** It was 44 lines — a seventh of the
  README, and the largest single block inside `Install` — covering the
  `--skip-monitoring` flow, the two labels that fail silently, and what `verify.sh --byo`
  relaxes.

  Nothing was cut: a line-by-line check confirms every substantive line survives, and the
  page gains what a README subsection could not carry — the choice between the script path
  and the Helm chart stated up front, and a note that the chart's `helm test` is the only
  thing here that can *verify* those two labels against a live Prometheus rather than
  merely document them. A short pointer keeps the trail from `Install`, and the README
  drops from 306 lines to 280.

### Removed

- **`vllm:num_preemptions_total` is no longer emitted.** It was set to `0` in the
  simulator's `__init__` and never incremented anywhere, so every scrape carried a
  permanent flat zero.

  ⚠️ **Removing an emitted metric name is a breaking change by the table at the top of
  this file**, which is why this bumps the MINOR while the version is `0.x`. **The real
  blast radius is much smaller than that classification suggests, and worth stating
  plainly:** nothing in this repo referenced the series — no panel, recording rule, alert,
  promtool case or `verify.sh` check. **No alert behaviour changes anywhere, for anyone**,
  because an alert on a permanently-zero counter could never have fired. The only
  observable difference is that a panel bound to it stops reading a flat zero and reads no
  data instead.

  That difference is the point rather than a regression. A blank panel says "not
  measured"; a flat zero says "no preemptions are happening", which is a claim, and a false
  one — on a rig whose saturated tenant exists to show a server under pressure, and via
  `ghcr.io/<owner>/vllm-metrics-sim` to people who cannot read the simulator to find out.
  The standard is already written down in
  [`manifests/llm/10-profiles.yaml`](manifests/llm/10-profiles.yaml): an invented number
  presented as a modelled one is the failure that file's arithmetic exists to prevent.
  Unlike the KV-cache ceiling — unreachable on the shipped profiles, and documented as such
  in four places — nothing recorded that this one was a stub.

  **Emitting it honestly would mean modelling KV pressure first, and there is none:**
  `_admit()` gates on `max_concurrency` alone, so `kv_cache_usage()` peaks near 0.43 even
  on the saturated profile. Preemption is what a real engine does when the KV cache runs
  out. That is a feature rather than a fix for one counter, and it would touch the most
  delicate code in the simulator, so it stays out until someone builds it.

  **Not deprecated first, deliberately.** Deprecation would keep a false zero on people's
  boards for a release cycle to be polite about it, which is the harm rather than a
  gentler path to fixing it.

  The metric is not lost so much as reclassified: `scripts/check-vllm-buckets.py` now lists
  `vllm:num_preemptions` among the upstream metrics this simulator does not emit — a list
  that grew from 22 to 23 — which is the honest backlog and where an unmodelled upstream
  metric belongs.

### Fixed

- **Counts in the prose that the two changes above had invalidated**, plus two that had
  been wrong for longer. Nothing here changes behaviour, and every one of them is a number
  a reader checks this repo's credibility against.

  Made stale by the SLO and the preemptions removal: `llm-simulation.md` described the LLM
  rule file as "Recording rules + **four** alerts" (six); it and `versions.md` both said
  the simulator emits **16** of upstream's names (15); `CONTRIBUTING.md` quoted the drift
  check's gap list at **22** (23); `architecture.md` enumerated the LLM alerts without
  either burn alert; the chart README and `verify.sh`'s own section header still read
  `L1–L8` while that script's file header had already moved to `L1–L9`; and
  `tests/README.md` listed the alerts the shipped workloads drive without
  `LLMTTFTErrorBudgetFastBurn`, which `llm-saturated` drives at ~100x.

  Wrong for longer, and surfaced by the same sweep: the README advertised **eight** PromQL
  queries in `observability.md`, which has carried nine since `ALERTS{alertstate="firing"}`
  was added, and `manifests/dashboards/README.md` said `dashboard-publish.py` repoints
  **22** `${datasource}` references on the LLM board — true when that board had nine
  panels, 33 now.

  ⚠️ ~~**25620 has fallen behind this repo in both of its artefacts**~~ **DONE — revision 3
  uploaded and the catalog page re-pasted; confirmed live via grafana.com's API on 2026-08-04.**
  Struck rather than deleted: the reasoning outlives the action, and the next panel will
  need it again. Recorded at the time in
  `manifests/dashboards/README.md` rather than left to be discovered. Published revision 2
  predates the burn-rate panel and the live catalog page predates the SLO section, so
  importing by id today gets neither. That file has always warned that the catalog does not
  update itself; this is the first time it has applied to a **panel** rather than to prose,
  which is the case someone actually notices.

- **`docs/llm-dashboard.png` retaken and optimised**, 3456x1988 RGBA down to 2400x1381 at a
  256-colour palette — **704K to 247K, 65% smaller** — through `docs/optimize-images.py`,
  which `gpu-dashboard.png` had already been through and this one never had.

  **The retake obsoleted a caveat, which is the part worth reading.** The README carried a
  note saying its screenshots predated the vLLM V1 bucket sync, so the saturated tenant
  read `1.20 mins` instead of 78s. The new capture reads **1.30 mins — which is 78s** — so
  the note now contradicted the paragraph above it, and is gone. The board's alt text went
  with it: it said "six panels" for a board that now shows twelve.

  **A second discrepancy the retake exposed is documented rather than reconciled.** The
  capture's *healthy* tenant reads a p95 of **~480 ms** where the README says `~120 ms`,
  with its `waiting` gauge flat at zero throughout. Both are right, and the new
  [Why an observed steady p95 runs higher than ~0.1s](docs/llm-simulation.md#why-an-observed-steady-p95-runs-higher-than-01s)
  says why: Little's Law puts steady's mean concurrency at `1.8 × 5.84` = 10.5 against a
  `max_concurrency` of 16, and arrivals are Poisson (`rng.expovariate`), so the batch
  reaches 16 regularly and those arrivals wait. A gauge sampled once per 15s scrape misses
  a queue that forms and drains between two of them; the TTFT histogram observes every
  request and does not. **Neither figure was edited to match the other** — `~120 ms` is what
  the profile arithmetic models, and `verify.sh` L3b and every promtool expectation are
  built on that arithmetic.

- **`docs/social-preview.py`'s crop re-tuned, and the comment justifying it corrected**,
  then `docs/social-preview.png` regenerated from the new screenshot.

  `FRAC` moves from `(0.1826, 0.1157, 0.9586, 0.4422)` to
  `(0.0908, 0.0565, 0.9792, 0.3968)`, and the band from 310px to 282px. The card now shows
  the top **two** rows — TTFT p95, ITL p95 and both running-vs-waiting repeats, four panels
  complete — rather than one row, because one row of the retaken board is a 10:1 strip that
  leaves the card mostly empty.

  ⚠️ **The old comment's reasoning was wrong, which is why this needed re-tuning at all.**
  It justified fractions-not-pixels by claiming "the top row occupies this proportion of the
  board whatever size the window was". Fractions survive a **resize** — the reason they are
  still fractions — but not a **re-capture**: the retake had a narrower Grafana sidebar and a
  board one panel taller, so every boundary moved, the old fractions landed mid-panel, and
  the card rendered cut through two rows.

  **Nothing failed.** The script exited 0 and wrote a plausible, wrong card — the repo's own
  worst category. The corrected comment now says to re-check the crop whenever
  `llm-dashboard.png` is retaken, and records how the boundaries were measured (scan for
  rows that are uniformly page-background; the gutters are `0.0565-0.2252` and
  `0.2288-0.3968` on the current capture).

  ⚠️ **The card now carries a rendering artefact from the capture itself**: both series in
  each running-vs-waiting panel are labelled `running` in the legend, where the shipped
  `llm-sim-overview.json` specifies `running` and `waiting` and git history shows it never
  held anything else. It is in the screenshot, not in the board — and the social card is the
  most public artefact here, so the next retake matters more than it usually would.

## [0.5.0] — 2026-08-01

**This release finishes the sentence 0.4.0 started.** That one gave a cluster with its own
Prometheus a route in; this one removes the two things still standing between a stranger
and a working board. The **Helm chart** means you no longer need a shell script with the
right flags — `helm install`, and your monitoring stack is untouched. The **published
simulator image** means you do not need this repo at all to point your own dashboards at a
realistic vLLM surface.

The third piece is the one the boards themselves needed. The vLLM board could say how long
a request took and not what it was *doing*: TTFT and queue time covered the waiting half,
and nothing covered the serving half. The **request phase breakdown** closes that, and it
is pure extraction — every term was already computed inside the simulator.

⚠️ **Everything here is additive.** No existing metric, recording rule, threshold, profile
or expected value moved, and no dashboard `uid` changed. An existing install upgrades by
re-running `install.sh`, which now rolls the simulator pods when the script changes — see
*Fixed*.

### Added

- **The request phase breakdown: `vllm:request_prefill_time_seconds`,
  `vllm:request_decode_time_seconds` and `vllm:request_inference_time_seconds`.** The
  board could say how long a request took and not what it was *doing*. TTFT and queue time
  covered the waiting half; the serving half had no series behind it at all, so there was
  nothing to build a prefill-versus-decode panel against — the first thing anyone working
  on disaggregated serving looks for.

  **Additive, so nothing is re-derived.** These are three new series and four new recording
  rules; no existing metric, rule, threshold, profile or expected value moved. Upgrading
  needs no action beyond re-running `install.sh` (which now rolls the simulator pods on a
  script change — see *Fixed*, below).

  This is **extraction, not modelling**. Every term already existed inside the simulator:
  prefill was assigned in `_admit()`, and decode was the `gen_tokens * itl` product
  `finish_at` was already formed from. They are now stored on the request and observed at
  the same point TTFT, e2e and queue time are observed. No invented numbers — that is the
  bar, and it is why the other 22 absent upstream metrics stay absent.

  All three reuse `E2E_BUCKETS`. Verified against `vllm/v1/metrics/loggers.py`: upstream
  declares one `request_latency_buckets` list and passes it to all five request-scoped
  histograms, so no bucket constant was added and the weekly drift check's existing entry
  already watches these boundaries on behalf of every one. That check's reported gap falls
  from 25 to 22, with all three matched rather than reported as drift.

- **A phase breakdown panel and a decode-p95 panel on the LLM board (dashboard 25620),
  plus four recording rules: `llm:queue:mean5m`, `llm:prefill:mean5m`, `llm:decode:mean5m`
  and `llm:e2e:mean5m`.** Both panels are appended below the existing ten rather than
  reflowing them, so an existing user's muscle memory survives.

  ⚠️ **The breakdown is MEANS rather than percentiles, and that is load-bearing.** Two
  measured reasons. Quantiles are not additive — on the steady tenant p95
  queue+prefill+decode is 7.473s against a p95 e2e of 7.468s, while the means sum to
  5.101s against 5.101s exactly — and a stack whose segments do not reach the total reads
  as a bug in the rig forever. And these buckets cannot resolve prefill at this operating
  point: the first boundary is 0.3s against a modelled 0.08s, so every observation lands
  in the first bucket and `histogram_quantile` interpolates from zero across it, **3.03x**
  overstated on both tenants. A histogram mean has no bucket dependence at all.

  ⚠️ **That second effect transfers to real vLLM** — the boundaries are upstream's, so a
  real deployment with sub-300ms prefill reads exactly as high. It is documented on the
  catalog page beside the existing inter-token-latency caveat (which is a 1.08x effect, for
  scale). **Do not build a prefill SLO on a p95 from these buckets.** The one recorded
  percentile is scoped to decode, the only phase these buckets resolve tolerably.

  `llm:e2e:mean5m` exists because the breakdown is asserted to **add up** — as a permanent
  promtool test and again on a live cluster by `verify.sh` **L8**, which also asserts all
  three histograms are receiving observations. An untested design decision is one that gets
  reverted by someone who does not know why it was made.

- **A Helm chart, `charts/k8s-ai-observability`, for clusters that already run
  Prometheus.** This is a new supported installation mode, not a replacement:
  `scripts/install.sh` stays the source of truth for install ordering and the
  wrong-context guard, and remains what CI exercises end to end.

  ```sh
  task chart
  helm install rig dist/charts/k8s-ai-observability --set releaseLabel=<your release>
  helm test rig --logs
  ```

  **Why.** Both boards are published, so people arrive from the catalog, import one, and
  find the panels blank for want of the `llm:*` recording rules. Their only route was a
  script that installs kube-prometheus-stack over the top of whatever they already run,
  which nobody with a production monitoring stack will accept. That was the single biggest
  structural blocker to adoption.

  ⚠️ **There is a build step, and `helm install ./charts/...` does not work.** Helm's
  `.Files.Get` cannot read outside the chart directory, so the chart cannot reference
  `manifests/dashboards/*.json` or `manifests/alerts/*.yaml` where they live. Of the three
  ways out — a build step, symlinks, or committed copies with a drift check — only the
  build step means the second copy **never exists in the tree**, which is what this repo
  refuses everywhere else. `task chart` assembles into gitignored `dist/`. The cost is
  real and the chart's own assertions state it rather than failing with a template error.

  **The simulator image above is what made this affordable.** `scripts/llm-sim.py` used to
  be the hardest item on that list — executable code, the one file a drifted copy of would
  be genuinely dangerous. A chart whose simulator Deployment references an image needs a
  *tag*, not the file, so it drops out entirely; what remains is static JSON and YAML.
  That is the one structural difference between the chart and `install.sh`, which still
  mounts the script from a ConfigMap.

  ⚠️ **Two values fail with NO ERROR AT ALL**, and this is the most likely way the chart
  appears broken. A wrong `releaseLabel` means your Prometheus never adopts the rules or
  ServiceMonitors — they never evaluate, the scrapes never happen, every derived panel is
  empty. A wrong `grafana.dashboardLabel` means the sidecar never imports the boards and
  `/d/<uid>` 404s. Every object reports itself successfully created either way.
  **`helm test` is what says so out loud, and it is opt-in** — a genuine weakness of the
  design, stated rather than hidden, which is why `NOTES.txt` tells you to run it in the
  imperative.

  **`install.sh`'s five assertions are ported rather than lost.** A `helm install` runs
  none of them, and a chart that installs cleanly and produces an empty dashboard is worse
  than no chart, because the failure arrives later and looks like the user's fault.
  Everything knowable at render time is a template-time `fail` caught by `--dry-run` — the
  dashboard filename/`uid` contract, that every board parses, distinct `model_name`s, the
  three-way naming invariant — and everything needing a cluster is a `helm test` hook. The
  chart README maps `CONTRIBUTING.md`'s invariants table onto which half covers each row,
  so the two cannot drift. **CI drives all thirteen to their failure**, because an
  assertion that has quietly stopped firing looks exactly like one that passes.

  One assertion has no `install.sh` counterpart: the capacity arithmetic. The script
  path's profiles are static files, so nobody can set an arrival rate that stops the two
  tenants straddling the 2s alert threshold. Templating them from `values.yaml` — needed
  so the numbers are genuinely reachable rather than frozen at their defaults — created
  that possibility, so the chart refuses to render it.

  ⚠️ **The chart's default image tag and the repo's release tag must agree, and nothing
  else enforces it.** A chart on a stale tag installs cleanly and runs an old simulator.
  `llm.image.tag` defaults to empty, meaning "use `Chart.appVersion`", and both
  `task chart` and the publish workflow cross-check it.

- **The simulator is published as a container image**, `ghcr.io/<owner>/vllm-metrics-sim`,
  for `linux/amd64` and `linux/arm64` on every release tag.

  ```sh
  docker run --rm -p 9401:9401 ghcr.io/chrisadkin8/vllm-metrics-sim:latest
  ```

  **This reverses a stated non-goal, and the reversal is narrower than it looks.** The old
  rule — *stdlib-only Python mounted into a stock image, so there is nothing to build, push
  or patch* — was reasoning about how **this rig** runs the simulator, and remains true of
  that path. It said nothing about how **anyone else** consumes it, which is the case an
  image serves: as a file inside this repo, `llm-sim.py` cannot be pointed at someone's own
  vLLM dashboards without cloning. The stdlib-only constraint is what makes the image
  trivial — a `FROM` and a `COPY` — and **`pip install` remains a non-goal**, unchanged.
  The two are habitually stated in one breath; only one of them moved.

  ⚠️ **Nothing about how the rig runs changes.** `install.sh` still builds the
  `llm-sim-script` ConfigMap from `scripts/llm-sim.py`, and the compose stack still mounts
  the same file. An image-based Deployment would pin a **tag**, so a local edit would stop
  reaching the cluster *silently* with the pod still `Running` — the exact failure the
  checksum annotation below was just added to fix, one layer up. The image is for external
  consumers, and both the Dockerfile and the docs say so, because someone will otherwise
  helpfully "simplify" the Deployment onto it.

  Built **from `scripts/llm-sim.py`** rather than a vendored copy, and CI fails if a second
  copy appears in the tree or if the image's payload stops matching the file byte for byte.
  A drifted copy of the simulator would be undetectable from the outside, which is precisely
  the property `tests/contracts/` guarantees for the DCGM surface.

  CI **runs** the image rather than only building it — building proves the Dockerfile
  parses and nothing more. ⚠️ Both architectures are **built**; only `linux/amd64` is
  **executed** on a GitHub runner, which is amd64. That is defensible while the payload is
  one architecture-independent `.py` file with no compiled extension — an arm64-only
  runtime failure would have to originate inside `python:3.12-slim` itself — and it stops
  being defensible the moment this image grows a native dependency. (`linux/arm64` was
  built and executed by hand during development, on Apple Silicon.)

  ⚠️ **The port override is `LLM_SIM_LISTEN_PORT`, not `LLM_SIM_PORT`.** kubelet injects a
  Docker-link-compatible `<SVCNAME>_PORT` for every Service in the namespace, so a Service
  named `llm-sim` sets `LLM_SIM_PORT=tcp://<ip>:9401`; reading that name meant `int()` got
  a URL and every pod died at startup. Verified against the built image: the correct name
  moves the listener and the wrong one is silently ignored rather than fatal. Documented in
  both directions, because someone will try the obvious name and conclude there is no
  override — and because the obvious name is the bug.

  `task image` builds and smoke-tests it locally. No new CI secrets: `GITHUB_TOKEN` is
  sufficient for `ghcr.io`, which matters because fork PRs never receive secrets at all.

- **A docs-only change no longer stands up two kind clusters and a compose stack.** A
  `changes` job diffs the push or the whole PR and gates the expensive jobs on the result:
  ~13 minutes down to seconds. Verified safe first — nothing under `scripts/`, `compose/`,
  `manifests/` or `tests/` *reads* a `.md` file; every match is prose in a comment.

  Deliberately **not** `paths-ignore:` on the triggers. A ruleset on `main` requires these
  checks, and a job that never *starts* leaves its check pending forever, so a docs-only PR
  would be unmergeable. It fails **open** — new branch, force-push, unreachable base, empty
  diff all run everything — because being wrong that way costs 13 minutes and the other way
  ships untested code. `schedule` and `workflow_dispatch` always run the lot: "does this
  still work" must not depend on what the last commit happened to touch.

### Fixed

- **`install.sh` rebuilt the simulator ConfigMap without rolling the pods, so an UPGRADE
  kept running the old script.** The 0.4.0 note below says to restart the simulator
  Deployments "(or let `install.sh` roll them)" — the second half was not true until now.

  A running pod serves the code it started with. kubelet eventually syncs the projected
  volume, but the Python process read `llm_sim.py` once at exec time and never looks
  again; and because only the ConfigMap's *contents* changed, nothing in the Deployment
  moved, so `kubectl apply` reported `unchanged` and no rollout happened. **The install
  went green while the cluster kept emitting the previous metric surface** — the
  silent-success failure this repo writes assertions against.

  Invisible on a fresh install, because there the first pods already have the current
  file, which is exactly why CI never caught it: CI always builds a new cluster. It
  surfaced as `verify.sh` L7 failing on an existing cluster with both prefix-cache
  counters absent while the ConfigMap plainly contained them.

  Fixed with a checksum annotation on the pod template, whose important property is that
  it is a **no-op when nothing changed** — a re-install with an unmodified script rolls
  nothing. An unconditional `rollout restart` would churn both tenants every install and
  reset the queue the saturated profile spends minutes building, briefly breaking the very
  checks this exists to keep passing. The Deployments are discovered by what they **mount**
  rather than by name, so the opt-in `llm-driven` tenant is covered too; hardcoding the two
  shipped names would have left extras users with the same bug, still silent.

  `python3` is now declared in `install.sh`'s `require_tools`. It was already a hard
  dependency via the dashboard JSON check, where a missing interpreter surfaced as
  "`<board>` is not valid JSON" — an error that sends you to inspect a file that is fine.

- **The first version of that gate blocked docs-only PRs outright**, which is the exact
  failure it was written to prevent. A job-level `if:` on the `stack` **matrix** meant that
  when it skipped, GitHub did not interpolate the job name — the check reported as the
  literal `full stack on kind (${{ matrix.profile }})`, so the two names the ruleset
  requires never reported. Caught on a real docs-only PR: every job green or skipped,
  `mergeable=MERGEABLE`, **`state=BLOCKED`**.

  The gate moved to the **steps**, so the job runs, the matrix expands and both names
  report. Two steps already carried a condition, where a blind second `if:` would have been
  a duplicate YAML key; merged, with `always()` ordered so the gate still holds — on a
  docs-only run there is no cluster to dump.

- **That fix cost the one honest signal**, so the job now says so. A skipped job reports
  `skipped`; a step-gated one reports `success` in three seconds without standing anything
  up, under a check named *"full stack on kind"*. The name cannot carry the caveat — it has
  to stay byte-identical or the ruleset stops matching — so a `::notice::` on the run
  summary states that the green means "nothing here could have been affected", not "the
  stack was verified".

- **The compose leg's readiness wait was tuned to the fast path**, and went red on a
  perfectly healthy stack: every container `Up`, no crashes, nothing in the logs. Prometheus
  was serving at `19:20:15` while Grafana was still running its first-run SQLite migrations
  at `19:21:15`, a full minute behind. 60s → 180s, and the failure now records when each
  service became ready and names whichever did not, instead of the useless "Prometheus
  and/or Grafana never became ready" that made this need a log download to diagnose.

  Also worth recording: the obvious one-liner for tracking both services,
  `[ -z "$x" ] || curl … && x=$i`, is wrong under `set -e` — the failing curl is the line's
  exit status, so the step aborts on the *first* poll, before anything can start. `if`.

- **Documentation sweep.** `docs/llm-simulation.md` contradicted itself on the saturated
  tenant's p95 — two places said `~60s` while a third, two lines below one of them, said
  `~78s`. `verify.sh` settles it: `~58s` is the true queue wait, `~78s` is what p95 *reports*
  once V1's `(40, 80]` bucket quantises it. Also documented `task load`, which appeared in
  `task --list` and nowhere else; linked `manifests/workloads/extras/README.md`, the last
  unreachable doc; and spelled out `GRAFANA_DASHBOARD_LABEL_VALUE` and the `KIND_MIN_*` /
  `KIND_WANT_*` overrides, which had behaviour documented but no names.

## [0.4.0] — 2026-07-31

**This release exists because both dashboards got published.** Once 25618 and 25620 were
in the grafana.com catalog, people arrived with a Prometheus of their own, imported a
board, and found panels blank for want of the `llm:*` recording rules — with no route
forward that did not involve installing a second monitoring stack over their existing one.
`--skip-monitoring` and `verify --byo` are that route. Everything else here is the
supporting cast: the metric families the boards now claim, the drift checks that keep
those claims honest, and the CI legs that stop the alternative paths rotting.

### Added

- **A supported installation mode: `./scripts/install.sh <target> --skip-monitoring`,
  for a cluster that already runs Prometheus.**

  ⚠️ **This changes what a cluster looks like after install** — on that path nothing
  installs `kube-prometheus-stack`, and the simulators, rules, dashboards and workloads
  land beside whatever monitoring stack is already there.

  Both boards are published, so people arrive from the catalog with their own Prometheus,
  import 25620, find four panels blank for want of the `llm:*` recording rules — and their
  only route was a script that installs a second monitoring stack over the top of theirs.
  Nobody with a production stack will do that, which made it the single biggest structural
  blocker to using any of this.

  Three variables carry the mode, and **all three fail silently when wrong**: no scrape, no
  rule evaluation, empty boards, every object reporting itself as successfully created.

  | | Default | If it is wrong |
  |--|--|--|
  | `KPS_RELEASE` | `kube-prometheus-stack` | `grafana.sh` / `prometheus.sh` port-forward to a Service that does not exist |
  | `RELEASE_LABEL` | follows `KPS_RELEASE` | your Prometheus never adopts the two ServiceMonitors or the two PrometheusRules |
  | `GRAFANA_DASHBOARD_LABEL` / `_VALUE` | `grafana_dashboard=1` | the sidecar never imports either board |

  `KPS_RELEASE` was a plain assignment in `config.sh`, so the environment was silently
  overwritten at source time — setting it did nothing at all. It is a `:-` default now,
  which is the whole story in one line: all four scripts source that file.

  The `release:` label is rewritten at apply time with `kubectl label --local` rather than
  templated, so the manifests stay the single source of truth. The comments on both
  ServiceMonitors used to call that label "harmless; picked up regardless" — true of *this
  repo's* `values.yaml`, which sets the three `SelectorNilUsesHelmValues` flags `false`.
  Upstream defaults them `true`, where the selector is `release=<their release>`. Both
  comments now say which half they were relying on, and a BYO user is told both available
  fixes, since setting those values `false` is often not theirs to change.

  `install.sh` refuses up front if the `monitoring` namespace or the Prometheus Operator
  CRDs are absent, **creating nothing**, and the message names the exact `helm install`
  rather than leaving `kubectl` to report "CRD not found" and send people looking for a
  broken manifest. `LITE=1` warns that it is ignored on this path — it is an overlay on
  the values of the install being skipped, and a flag that silently does nothing is what
  this repo writes assertions against.

- **`./scripts/verify.sh <target> --byo`**, and `task <prefix>:install -- --skip-monitoring`
  / `:verify -- --byo` now that both tasks pass `CLI_ARGS` through. Without the
  passthrough the flag was accepted from the front door and **silently dropped**.

  The mode relaxes exactly one claim: anonymous Grafana access, which follows from this
  repo's Helm values rather than from anything it installed, so `401`/`403` becomes a SKIP.
  Everything about the simulators, scrapes, rules and dashboards is still asserted —
  skipping those would make `--byo` prove nothing on the install that most needs proof,
  since they are precisely what a wrong selector label breaks. **A `404` on a board stays
  fatal**, and says so in BYO terms: Grafana having never heard of it means the sidecar
  never imported the ConfigMap, which is the most likely way a BYO install appears broken.

- **A DCGM surface contract, asserted from both producers**
  (`tests/contracts/dcgm-surface.json`).

  The compose path has a second implementation of the fake exporter's surface and nothing
  compared it against the first — the parity was prose in `gpu-metrics-sim.py`'s header.
  A chart bump renaming a series or a label would fail the kind path loudly in CI and let
  the compose path drift in silence, which is backwards: `docker compose up -d` is the
  first command in the README.

  | Side | Assertion | Why |
  |--|--|--|
  | `compose/gpu-metrics-sim.py --selftest` | **exact** equality | nothing sits between the producer and the check |
  | `verify.sh` check 3b | **subset** | scraped series carry `job`, `instance`, `namespace`, `pod`, `endpoint`, `service` from the target, and the exporter's own pod labels arrive as `exported_*` — an exact match would fail on day one |

  Both semantics are in the contract file's header so the asymmetry reads as a decision.
  The selftest parses the **rendered exposition** rather than the module's constants:
  asserting the constants would only prove the file is self-consistent, and the thing that
  has to be right is what a Prometheus scrapes, label spelling included.

  ⚠️ The negative case is a permanent test, not an experiment. `--selftest` feeds
  `tests/fixtures/dcgm-surface-wrong.json` — wrong in three independent ways, one per
  direction the checker must detect — and asserts each fault is named in the rejection. A
  checker that only ever runs against the truth is one nobody has watched fail, and one
  that accepts everything also accepts the truth. Proving it by editing the real contract
  and reverting does not count: nothing enforces the revert.

  `render()` in the compose producer now takes the clock as a parameter, defaulting to wall
  time, for the same reason `llm-sim.py`'s does — it lets the selftest demand byte-identical
  output across two renders instead of comparing sine samples with a tolerance, which is
  also how it asserts that a scrape moves nothing.

- **CI covers the compose path**, in a `compose` job that needs docker rather than kind and
  takes about a minute. It is not a cheaper duplicate of the kind job: it exercises
  Prometheus loading the rules `scripts/extract.sh` unwraps out of `manifests/alerts/`,
  Grafana provisioning both boards off disk instead of through the sidecar, and the
  compose-only GPU producer. A break in any of those showed up nowhere before.

  It asserts through Prometheus rather than by curling the simulators — the simulator
  containers publish no ports, and querying proves the scrape works, which raw exposition
  would not. An **empty** target list fails too, since "nothing is down" is otherwise
  satisfied by nothing being scraped.

- **`python3 -m py_compile` over every `*.py` in the repo**, in the `fast` job. Until now
  the only Python CI executed was `llm-sim.py` via its selftest, so `dashboard-publish.py`,
  `check-vllm-buckets.py`, `gpu-metrics-sim.py` and the `docs/` scripts could ship a
  `SyntaxError` green — and two of those are release tooling, where the first person to
  find out is whoever is trying to cut a release.

### Fixed

- **The inter-token-latency caveat was wrong by roughly 2x, on a published board.** It said
  a full batch's 22.5 ms ITL falls in the `(25ms, 50ms]` bucket and reports ~43 ms. It
  falls in `(10ms, 25ms]` — `TPOT_BUCKETS` starts `[0.01, 0.025, 0.05, …]` — so the
  interpolation spans a 15 ms gap and reports ~24 ms, which is what `scripts/llm-sim.py`
  and the promtool expectation have said since the V1 bucket sync. The catalog page was
  written afterwards and copied the pre-correction numbers, and so did the panel
  `description` inside `llm-sim-overview.json` — the tooltip a grafana.com visitor reads,
  on a board whose entire point is that the caveat transfers to real hardware.

- **Three more vLLM series: `vllm:prefix_cache_queries_total`,
  `vllm:prefix_cache_hits_total` and `vllm:request_queue_time_seconds`.**

  ⚠️ **This changes what a cluster exposes after install.** Nothing existing moved and no
  panel or alert changes meaning, but the simulator pods emit more than they did, so a
  re-apply is what makes the new series appear. `install.sh` rebuilds the `llm-sim-script`
  ConfigMap; a *running* pod keeps the old mount, so restart the simulator Deployments
  (or let `install.sh` roll them) if the new series do not turn up.

  This closes the gap that most limited the repo's central claim. A real vLLM operator's
  dashboard has a prefix-cache hit-rate panel, and there was nothing here to build one
  against — the simulator emitted 10 of upstream's ~38 V1 metrics and none of them said
  anything about cache reuse or queue wait.

  Notes worth having before you use them:

  - **Counted in tokens, not requests.** A per-request counter gives a ratio that does not
    respond to prompt length, so a panel built here would behave differently against a
    real deployment. Hits are quantised to whole KV blocks (`kv_block_tokens`, default 16)
    the way upstream quantises them — a partial trailing block is never a hit.
  - **⚠️ A cache hit shortens NO latency here, by construction.** Prefill in this
    simulator is flat, not token-proportional, so there is no per-token work a cached
    block could remove and any speedup would be invented. `--selftest` asserts the TTFT
    histogram is *identical* across hit rates. Changing that is a real modelling change
    and re-derives the service time, the 2.74 rps capacity figure, both profiles, the 2s
    threshold, `verify.sh`'s L3b bound and every promtool expectation.
  - **Queue time is extracted, not re-derived.** The simulator already built TTFT as
    `queue_wait + prefill`; the histogram observes that first term at the same point TTFT
    is observed, so `ttft == queue_time + prefill` is an identity and the selftest asserts
    it on every completed request rather than comparing a p95 to a p95 with a tolerance.
  - **No new bucket constant.** Upstream declares one `request_latency_buckets` list and
    passes it to both `e2e_request_latency_seconds` and `request_queue_time_seconds`, and
    `E2E_BUCKETS` already *is* that list — so the existing drift-check entry watches these
    boundaries on behalf of both.

- **`prefix_cache_hit_rate` on the shipped profiles: 0.35 steady, 0.15 saturated.**
  ⚠️ Unlike `capacity_rps`, these are **derived from nothing**. They are chosen so the
  panel draws two distinguishable lines, the lower one on the saturated tenant because a
  server under eviction pressure reuses less — and `manifests/llm/10-profiles.yaml` says
  exactly that where it sets them, because an invented number presented as a modelled one
  is the failure those profile comments exist to prevent. Shipping non-zero is safe only
  because the hit rate is decoupled from latency: no existing series changes.

- **`--vllm-surface` now covers a metric RESHAPE, not just renames.** v0 exposed prefix
  caching as a gauge of a ratio (`vllm:gpu_prefix_cache_hit_rate`); V1 replaced it with
  two counters. A panel bound to the old name cannot be repaired by substituting a name —
  the replacement is `rate(hits)/rate(queries)`. Neither of the two renames the repo
  already shipped makes that point, and it is the sharpest upgrade-rehearsal case here.
  The 1:1 `METRIC_SURFACES` map cannot express one-gauge-to-two-counters, so there is now
  a `METRIC_RESHAPES` table beside it with the same positional `(v0, v1)` shape; the drift
  check reads both.

- **A prefix-cache hit-ratio panel on the vLLM board, and the recording rule behind it**,
  `llm:prefix_cache:hit_ratio5m` = `rate(hits) / rate(queries)` by tenant. Recorded once
  rather than repeated per panel, so a dashboard and an alert can never disagree about what
  the ratio means — the same reasoning as the quantile rules beside it.

  The denominator is clamped like `GPUHighMemoryUsage`'s, but at an **epsilon rather than
  at 1**. `clamp_min(x, 1)` suits a byte count and not a rate: a low-traffic real
  deployment can genuinely sit below one queried token per second, and flooring there would
  quietly under-report the tenants least likely to notice. An idle tenant reads `0/1e-9`,
  which is a flat line at zero rather than a `NaN`.

  promtool covers both sides of the ratio and the no-traffic case. Its expected values are
  **exact by construction rather than by luck** — the counters sit in 2:1 and 4:1 ratios,
  so the division is exact on any architecture, which sidesteps the amd64/arm64 percentile
  trap documented in `tests/`.

  ⚠️ ~~**25620 needs re-submitting as a new revision.**~~ **DONE — uploaded 2026-07-31.**
  Struck rather than deleted, because the reasoning outlives the action: the board is
  published, so a merged panel reaches nobody who imported it by id until the catalog copy
  is uploaded again, and it must go up as a revision of the existing id, never as a new
  dashboard — a second upload mints a second id and everyone on the first silently stops
  receiving fixes. That applies to the next panel too, and is stated as standing guidance
  in [`manifests/dashboards/README.md`](manifests/dashboards/README.md).

- **A `verify.sh` L7.** The queue-time histogram is receiving observations and both
  prefix-cache counters are present, on a real cluster. Everything else covering these
  families is a selftest or a promtool assertion, and neither leaves the repo: the
  simulator proving it emits a series and Prometheus proving it receives one are different
  claims.

- **The weekly drift check now watches the metric SET, not just the bucket boundaries**
  (`scripts/check-vllm-buckets.py`). It ast-walks both files for string literals beginning
  `vllm:` and compares the sets, in the same structure-blind way the bucket check already
  worked.

  Buckets were only ever the narrower half of the risk that created that file. Two vLLM
  metrics were *renamed* by the V1 engine and this repo shipped the old spellings for two
  releases with every test green — a name we emit that upstream has dropped is a panel
  that goes blank against a real deployment, which is the same silent failure one level
  up. The check now covers it.

  The two directions are deliberately not treated alike:

  | | Means | Exit |
  |--|--|--|
  | We emit a name upstream no longer declares | drift — the case that cost two releases | **1** |
  | Upstream declares a name we do not emit | a gap, printed in full | **0** |

  Upstream declares ~38 `vllm:` metrics and this simulator emits 10, so the gap list is
  long by design and printing it is the point: it makes the distance **visible instead of
  silent**, which is what stops it growing back. Reddening a scheduled run every time vLLM
  adds a metric would just train everyone to ignore the job.

  ⚠️ The `_total` rule is the subtle part and it is now unit-tested rather than trusted.
  Upstream declares counters *without* the suffix and the Prometheus client appends it at
  exposition time, so our `vllm:prompt_tokens_total` is upstream's `vllm:prompt_tokens` —
  but `vllm:iteration_tokens_total` is declared *with* it, as a histogram. A blanket strip
  reports "in sync" on a name with no upstream counterpart at all, and the real run cannot
  reveal that because it prints a plausible answer either way. So the matching runs against
  a committed fixture (`tests/fixtures/upstream-vllm-metric-names.txt`) in the `fast` CI
  job, on every push, with no network:

  ```sh
  task drift-test    # python3 scripts/check-vllm-buckets.py --selftest
  ```

  The file keeps its name. It is cited by `Taskfile.yml`, `ci.yml`, `CONTRIBUTING.md`,
  `docs/versions.md` and `docs/llm-simulation.md`, and renaming it to touch five files
  buys a better noun and nothing else — the docstring is the specification. The CI job is
  renamed, since a job name costs nothing to move.

- **`CONTRIBUTING.md`.** The repo's invariants were already documented — scattered across
  `config.sh`, `manifests/dashboards/README.md`, `architecture.md` and several file
  headers. A contributor had to find them first. This collects the ones that, if broken,
  produce **a green install with something silently wrong** into one table: the
  filename-is-the-uid contract, the three-way naming invariant, what to re-verify before
  bumping either pinned chart, and what to re-derive after touching a bucket list. Also
  the traps that cost real debugging time here, including the architecture-dependent
  `histogram_quantile` result, and what is deliberately out of scope.

- **A `lite` leg in CI.** The `stack` job is now a matrix over `full` and `lite`, so the
  trimmed profile is exercised on every push rather than verified once by hand and left
  to rot at the next chart bump. `fail-fast: false` — if lite breaks while full passes,
  that difference is the signal.

  ⚠️ ~~It proves the trimmed stack **functions**, not that it fits 3 GiB~~ **MEASURED —
  2026-08-04, and the claim was wrong in a way worth stating precisely.** The CI runner has
  ~16 GB and nothing there is under memory pressure, so a constrained local run was the
  only thing that could settle it. Run at last, on colima/aarch64:

  | Runtime allocated | Docker reports | `kind-up.sh` reads | Result |
  |--|--|--|--|
  | 3 GiB — the advertised floor | 2.83 GiB | **2 GiB** | **refused before anything started** |
  | 4 GiB | 3.81 GiB | 3 GiB | `task local:up` green, **ALL CHECKS PASSED** |

  **The trimmed stack does fit — it used 2.197 GiB of 3.813**, 57.6%, with zero restarts,
  zero OOMKills and nothing Pending, through every GPU check and L1–L9. Consumption was
  never the problem.

  ⚠️ **The floor was unreachable, and the cause is a unit mismatch nobody would guess.**
  `scripts/kind-up.sh` computes `mem_bytes / 1024 / 1024 / 1024` — integer division — so a
  runtime is measured by what it REPORTS, while a user sets what it is ALLOCATED. VM
  overhead eats ~0.2 GiB and truncation eats the remainder, putting a full GiB between the
  two. Asking for exactly the documented 3 GiB produced a `2 GiB` reading and an error
  telling you to raise it to 4 — the floor refusing itself.

  Fixed in the docs rather than the check: the README now gives the LITE path its own
  `colima` line at `--memory 4` and says why 3 fails, since the full path always had a
  command to copy and the trimmed one — the only one where the gap bites — never did.
  Rounding instead of truncating was the alternative and was rejected: it would make the
  guard accept 2.83 GiB as "3", which is looser than the check was written to be.

- **`.github/dependabot.yml`** for the SHA-pinned actions. Pinning by SHA is right, but a
  SHA pin never moves on its own — and this repo has a live instance, with
  `actions/upload-artifact` warning on every run that it targets Node 20. It cannot cover
  the two Helm chart pins, which live in shell variables and stay a deliberate manual
  decision; the file says so and says why.

- **Both dashboards are published to the grafana.com catalog**: the GPU board is
  [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) and the vLLM board is
  [25620](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/). They can now be imported by id
  into any Grafana, without cloning this repo.

  The ids are recorded in three places on purpose, and all three are the ones the repo
  already told you to update: the table at the top of `manifests/dashboards/README.md`,
  `docs/versions.md`, and the README. A published board that cannot be traced back to the
  file it came from is how a catalog entry goes stale without anyone noticing.

  ⚠️ **Republish as a new revision, not a new dashboard.** A second upload would mint a
  second id, and the one people have already imported would quietly stop receiving fixes.

- **Publishing metadata for both dashboards**, ready to paste, in
  `manifests/dashboards/README.md`.

- **A catalog page for each board** — `manifests/dashboards/gpu-sim-dcgm.grafana-com.md`
  and `llm-sim-overview.grafana-com.md` — the long-form description to paste into the
  grafana.com listing, beside the one-paragraph blurbs above.

  Separate files rather than a section of the dashboards README, because the reader is
  different in a way that changes the writing: someone arriving from the catalog has their
  own Prometheus, no clone, and no reason to care what this rig is. So every link in them
  is **absolute**, and every caveat is stated in full rather than cross-referenced — the
  derived temperature/power rules are inlined on the GPU page rather than pointed at.

  ⚠️ The LLM page carries the heavier load, and that asymmetry is the reason both exist.
  The GPU board is import-and-go against a real `dcgm-exporter`. The LLM board is not:
  **four panels read `llm:*` recording rules and two more read simulator-only `llmsim_*`
  series**, so a bare import gives a mostly-empty board and no clue why. The rules are on
  the page ready to apply, with the two traps that come with them — `llm:tpot:p95_5m` must
  not be pointed at `vllm:request_time_per_output_token_seconds` (a per-request mean, not a
  per-token histogram), and `vllm:kv_cache_usage_perc` is a fraction, so `> 90` can never
  fire. Both fail by staying silent, which is why they are worth a stranger's screen space.

- **A logo for each board**, `docs/logos/*.png` at 512×512, generated by
  `docs/dashboard-logos.py` (needs `pillow`, like the other two image scripts in `docs/`).

  Generated rather than drawn for the reason the boards are files rather than clicks: it
  re-runs, it diffs, and the two marks cannot drift into a mismatched pair. Coordinates are
  fractions of the canvas, so changing one constant re-renders the same mark at a different
  size rather than a differently-proportioned one.

  The binding constraint is legibility at listing size, not detail at 512, and it decided
  the design: nothing thinner than 0.03 of the canvas, three utilisation bars rather than
  four (a fourth drops each under ~4px and they merge into a block), and no text, which
  turns to mud small and duplicates the title the catalog already prints. The tile is
  **solid dark rather than transparent** because grafana.com renders in both themes and a
  transparent mark disappears on one of them. Drawing is supersampled 4× and downsampled,
  since PIL has no antialiasing and a stepped diagonal is what a logo cannot afford.

  ⚠️ ~~**Unverified: grafana.com's own logo requirements.**~~ **RESOLVED — 2026-08-04, and the
  answer is that there is no requirement to meet.** Grafana's dashboard-publishing
  documentation states no dimension, aspect ratio, file-size or format limit for a logo —
  it lists the asset and specifies nothing about it. So the assumption could never have
  been checked against a spec, because none is published.

  What settles it instead is acceptance: `GET /api/dashboards/25618` and `/25620` both
  report **`hasLogo: true`**, so the 512×512 PNGs were taken without complaint. The choice
  stands on the reason it was made — it downsamples cleanly — and `SIZE` in the script is
  still the whole edit if that ever changes.

- **`scripts/dashboard-publish.py` and `task dashboards`**, deriving the grafana.com
  upload into a gitignored `dist/`.

  The boards were documented as uploadable as-is. They are not: the catalog rejects them
  with *"Old dashboard JSON format. Read about Importing & Sharing with Grafana 2.x or
  3.0."* The cause is the design working correctly. Every panel binds to a
  `datasource`-type template variable, which is right in-cluster — the sidecar provisions
  the board and the variable resolves to whatever Prometheus is there. The catalog wants
  the opposite: the `__inputs` block Grafana 3.0 introduced, so it can prompt the importer.

  Grafana's own **Export for sharing externally** does not bridge this, and the reason is
  worth recording because it inverts the obvious intuition: that exporter works by
  rewriting a *concrete* datasource uid into a placeholder. A datasource variable has
  already abstracted the uid away, so it finds nothing to rewrite and emits no `__inputs`
  at all. **The cleaner the repo file, the more certainly the catalog rejects it.**

  The script adds `__inputs` and `__requires`, repoints every `${datasource}` reference at
  `${DS_PROMETHEUS}` — 22 of them on the LLM board, several inside query-variable
  definitions that are easy to miss by hand — and drops the now-redundant variable.
  `__requires` collects panel plugins from the file rather than hardcoding them, so a new
  panel type cannot be silently omitted.

  `dist/` is gitignored on the same terms as `compose/.generated/`: one source of truth,
  several derived forms, none committed.

  It also absorbs the portability check that was a copy-paste snippet in
  `manifests/dashboards/README.md`. Rather than reporting the three properties, it **fails
  and emits nothing** when a panel carries a hardcoded datasource uid — what editing in
  the Grafana UI and pasting the JSON Model back produces. Running it inside the thing
  that consumes the result means it cannot be skipped.

### Changed

- **Repository settings** (not in the tree, recorded here because nothing else would show
  it): Dependabot vulnerability alerts and automated security fixes enabled; GitHub
  Releases backfilled for `v0.1.0` and `v0.2.0`, which existed only as bare tags; and a
  ruleset on `main` blocking force-pushes and deletion and requiring both CI jobs. The
  ruleset carries an **admin bypass**, so direct pushes by the owner still work — it gates
  contributions, not the maintainer.

### Fixed

- **`verify.sh` checks 3 and 4c could fail on a slow runner**, and did — on a commit that
  changed only two markdown files, which is what made it obviously a timing fault rather
  than a real one. Check 3 gave up waiting for the first DCGM scrape at 60s;
  `DCGM_FI_DEV_GPU_TEMP` — a recording rule that *cannot exist* without the metric check 3
  had just declared missing — passed 13 seconds later. The run took 8m34s against a
  typical 5m.

  Both now poll for 120s, and share one `DCGM_POLL_ATTEMPTS` constant rather than two
  matching literals and a comment asking future editors to keep them aligned. That
  coupling is not cosmetic: 4c asserts something *derived* from what 3 asserts, so if 3
  has the shorter window a slow runner reports "the input is missing but the value
  computed from it is present" — which reads as a ServiceMonitor selector fault when it
  is nothing of the kind.

  Still bounded, deliberately: a genuine selector mismatch never resolves, so these must
  fail rather than hang. 120s is ~8 scrape intervals at the 15s the ServiceMonitors set.
  Check 4d keeps its own 60s and says why — by the time it runs, the metric is known to
  exist, so it is budgeting annotation propagation, not a first scrape.

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

[Unreleased]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/releases/tag/v0.1.0
