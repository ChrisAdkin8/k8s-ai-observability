# The CI pipeline

Most Kubernetes infrastructure repos cannot test themselves. They need hardware, or a
cloud account, or quota someone has to approve, so CI ends up linting YAML and dry-running
Helm and hoping. This repo's whole premise is *no GPU hardware, quota, drivers or model
weights required*, and that has a happy consequence: **it can prove its own claim on a free
runner.**

So CI does not check that the manifests parse. It stands up a real Kubernetes cluster,
installs everything, and runs every acceptance check in `scripts/verify.sh` against it. If
the README says `task local:up` works, a machine that has never seen a GPU proves it on
every pull request.

Everything below lives in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). That
file is heavily commented and is the authority; this page is the map, and points at it
rather than repeating it.

---

## The shape of a run

```mermaid
graph LR
  A["what changed"] --> C["compose stack"]
  A --> D["helm chart"]
  A --> E["simulator image"]
  A --> F["full stack on kind"]
  B["selftest + rule tests"] --> C
  B --> D
  B --> E
  B --> F
  F --> F1["(full)"]
  F --> F2["(lite)"]
  G["weekly upstream drift<br/>schedule + dispatch only"]
```

Two jobs open every run and gate everything else:

**`what changed`** works out whether anything a cluster could possibly test has moved. If a
pull request touches nothing but `.md` files, there is no point standing up two clusters and
a compose stack, so the expensive jobs are skipped.

**`selftest + rule tests + shell syntax`** is the cheap one. It takes seconds, needs no
cluster, and it runs on *every* run without exception. The reasoning is worth knowing: a wall
of green skips on a docs-only pull request tells you nothing, so at least one check should
always have genuinely executed.

⚠️ It does need the **network**, and this page said otherwise for as long as `promtool` has
been installed there. It fetches two small release archives, `promtool` and `actionlint`,
both checksummed. That matters for one reason: "no network" is the property someone would
lean on when deciding this job cannot be flaky, and it can be, if a release CDN is.

Everything else hangs off those two.

---

## The jobs, one at a time

| Job | What it proves | Timeout |
|--|--|--|
| `what changed` | whether this diff can affect anything a cluster tests | 5 min |
| `selftest + rule tests + shell syntax` | the simulator's own exposition, the alert and recording rules, prose vs code, shell and Python syntax | 5 min |
| `compose stack (no Kubernetes)` | the Docker Compose path works, boards load, targets are up | 10 min |
| `helm chart (lint, render, assertions fire)` | the chart renders both ways, and every render-time assertion still *fires* | 15 min |
| `simulator image (build both arches, smoke-test amd64)` | the image builds for amd64 and arm64 and actually serves metrics | 15 min |
| `chart on kind (helm test, foreign Prometheus)` | the chart installs, and `helm test` both fails and passes for the right reasons | 60 min |
| `full stack on kind (full)` / `(lite)` | the real thing, end to end, twice | 90 min |
| `weekly upstream drift (vLLM + tool pins)` | upstream vLLM has not moved under us, and every pinned tool checksum still matches its publisher | 5 min, weekly |
| `branch ruleset vs required-checks.txt` | the branch protection settings still match what the repo records | 5 min, weekly |
| `open an issue when a weekly check goes red` | a weekly failure reaches a human, not just the Actions tab | 5 min, weekly |

### selftest + rule tests + shell syntax

The fast gate, and the one that catches most mistakes. It runs the simulator's selftest
(histogram bucket monotonicity, `+Inf` consistency, `HELP`/`TYPE` correctness), the compose
GPU producer against the DCGM surface contract, `promtool` tests for the alert and recording
rules, and `scripts/check-doc-claims.py`, which compares prose in the markdown against the
code it describes.

It also lints the shell, in three layers, because one glob does not reach all of it:

| What | Covered by |
|--|--|
| `scripts/*.sh` | `shellcheck -S warning`, the whole directory in one invocation |
| `.github/workflows/*.yml` | `actionlint`, which hands every `run:` body to shellcheck |
| `.github/actions/*/action.yml` | `scripts/check-action-shell.py` |

⚠️ The second and third rows are new, and they closed a real gap: 763 lines of bash lived
inside `.github/` and nothing read any of it. `codeql.yml`'s header names the first row as
the gate for the half CodeQL cannot read, which was true of `scripts/` and not of the
workflows — and the SIGPIPE bug of iron rule 17 lived in exactly that gap, in this
workflow's own changes filter. The third row exists because `actionlint` structurally
cannot read a composite action: pointed at one it reports `"on" section is missing in
workflow` and stops, which would have left this repo's most-shared verification as the
only unlinted shell in the tree.

It also runs `check-sigpipe.py`, which finds pipes whose consumer stops reading
before the producer finishes. Under `pipefail` that turns a correct result into exit
141, and it has bitten twice: it failed a chart publish on an archive that contained
everything it was asked to prove, and in this very workflow's changes filter it would
have reported a large code change as "markdown only" and skipped the cluster jobs.
⚠️ Neither reproduces under `zsh`, so a laptop will not find them — test that class
with `bash -c`.

The doc-claims check exists because documentation drift here is a *known failure class*. Dashboard
ids, "emits N metrics" claims and version numbers have all been wrong in prose while being
right in code. The checker derives every expected value at run time and holds no copy of any
truth, so it cannot drift itself.

`task preflight` runs the same gates locally. Running it before you push is the single
highest-value habit in this repo.

### compose stack

Not everyone wants a Kubernetes cluster to look at a dashboard. The compose path mounts the
same simulator, the same dashboards and the same rules, and this job proves it: brings the
stack up, waits for Prometheus and Grafana, asserts both producers are visible *through
Prometheus*, checks every scrape target is up, and fetches both boards anonymously by `uid`.

It uploads its logs if anything fails, and tears the stack down either way.

### helm chart

Lints the chart, then renders it **both ways round**: with `kubePrometheusStack.enabled`
false (the BYO case the chart exists for) and true (greenfield, where the subchart's own
templates render too, which is a genuinely different code path).

The interesting half is the next step. The chart carries render-time assertions, and this
job drives every one of them to its *failure*, deliberately feeding in broken values to
confirm each still refuses. An assertion that has quietly stopped firing looks exactly like
one that passes, which is the regression this catches.

### simulator image

Builds `linux/amd64` and `linux/arm64`, loads the amd64 build into the runner, runs it, and
scrapes `/metrics`. It also asserts that the port override is `LLM_SIM_LISTEN_PORT` and that
`LLM_SIM_PORT` is ignored, because that was a real trap.

Only amd64 is executed: the runner is amd64. Both architectures are built.

### chart on kind

⚠️ **Until this job existed, nothing on a pull request ever installed the chart.** The
`helm chart` job lints and renders and never touches a cluster; the `full stack on kind`
job installs through `install.sh`, the *script* path. So the chart's own `helm test` — the
only thing that checks the two silent-failure labels against a live cluster — ran solely in
the publish workflow, on a tag.

That is how chart `0.2.0` reached the registry with `helm test --logs` exiting 1 on a chart
where every hook had succeeded: a green result reported as red, on the exact command the
chart README tells people to run. Registry versions are immutable, so it is still there.

This job creates its own cluster, installs kube-prometheus-stack under a release name this
repo would never choose, and then asserts **both** directions: `helm test` must **fail**
with the default `releaseLabel`, and pass once it is set. An assertion that only ever
passes is not an assertion.

It needs its own cluster rather than a step on `full stack on kind`, because the chart's
default namespaces are the same ones `install.sh` uses and Helm will not adopt resources it
does not own.

The steps live in a composite action, [`.github/actions/verify-chart`](../.github/actions/verify-chart/action.yml),
because `publish-chart.yml` runs the identical sequence against the **published** artefact.
Same procedure, two subjects, one implementation — a second copy would be free to drift.

### full stack on kind

The real test. A kind cluster, both Helm releases, all the wiring, and every acceptance
check. This is `task local:up` run exactly as a user would run it.

It runs **twice**, and the difference is covered in its own section below.

### weekly upstream drift

Weekly, on a schedule, plus manual dispatch. The rig transcribes vLLM's histogram bucket
boundaries verbatim, and if upstream changes them this repo is quietly wrong until someone
notices. This job is the someone.

It only runs on `schedule` and `workflow_dispatch`, so you will see it as "skipping" on
ordinary pull requests. That is correct, not broken.

### open an issue when a weekly check goes red

Both weekly jobs run only on `schedule`, which fires only on the default branch. Neither
has a pull request to redden and neither blocks anything, so until this job existed their
entire output was a red square on the Actions tab. Iron rule 18 applies to CI itself: a
red run nobody is told about is a check that never fails.

It opens one issue, labelled `upstream-drift`, and de-duplicates on that label rather than
on the title — a title carries the run id, so matching on it would file a fresh issue every
Monday for a drift nobody has got to yet. An open issue means the finding is already on the
board.

---

## `full` and `lite`: the same test, two environments

The `stack` job uses a matrix with two values:

```yaml
strategy:
  fail-fast: false
  matrix:
    profile: [full, lite]
```

**The checks are identical.** No step is gated on the profile, and `verify.sh` never reads
`LITE` at all. Both legs run the same assertions. The only thing that differs is the
monitoring stack underneath them, via one line:

```yaml
LITE: ${{ matrix.profile == 'lite' && '1' || '' }}
```

`scripts/config.sh` turns that into an extra `-f values-lite.yaml` overlay, which switches
things off:

| | `full` | `lite` |
|--|--|--|
| Prometheus | 1Gi request / 2Gi limit | 256Mi / 512Mi |
| retention | default | 2h |
| Alertmanager | on | **off** |
| kube-state-metrics | on | **off** |
| node-exporter | on | **off** |
| upstream `defaultRules` | created | **not created** |

So `full` answers *does the rig work on a normal stack*, and `lite` answers *does the rig
work when nothing else is there*. That second question matters more than it looks. With
upstream's default rules and exporters absent, `lite` is the only thing proving that every
panel, alert and recording rule depends solely on series **this repo produces**. Add a panel
that quietly leans on a `node_*` or `kube_*` series and `full` will pass while `lite` fails.

It is also the closest CI gets to a BYO user's cluster, which is the audience the chart is
built for.

`fail-fast` is off on purpose. If `lite` breaks while `full` passes, that difference *is*
the diagnosis; cancelling the healthy leg to save a few minutes throws away the comparison.

There is a real example recorded at `scripts/verify.sh:533`: a check that failed on `lite`
while `full` passed on identical logs, because the leaner, faster leg hit a timing window
where a GPU binding existed but the join to the exporter had not resolved yet. The fix was
to poll rather than single-shot, which is now iron rule 5.

> The `lite` leg is still named "full stack on kind (lite)", which reads oddly. "Full stack"
> means the whole end-to-end path, not the profile.

---

## The bit that will bite you

**The matrix value is part of the check name**, and that is load-bearing:

```yaml
name: full stack on kind (${{ matrix.profile }})
```

This produces two separate status checks, and the branch ruleset on `main` requires four
things by name:

- `selftest + rule tests + shell syntax`
- `full stack on kind (full)`
- `full stack on kind (lite)`
- `chart on kind (helm test, foreign Prometheus)`

Two consequences follow, and both have teeth.

**Rename the job or change the matrix values and every pull request blocks forever.** A
required check that never reports is not treated as passed; it sits at "waiting for status to
be reported". The ruleset lives in GitHub settings, outside this repository, so nothing here
version-controls it.

That coupling *is* checked now, in two halves, because no single check could cover it:

| Half | Where | When | Catches |
|--|--|--|--|
| every recorded requirement is a name `ci.yml` can produce | `check-doc-claims.py` | every run, offline | a rename, in the pull request that made it |
| the recorded list matches the **live** ruleset | `check-required-checks.py`, via the `branch ruleset vs required-checks.txt` job | weekly, needs network | someone editing the ruleset in a browser, and a ruleset that has stopped enforcing |

⚠️ That second half was forty lines of inline Python in `ci.yml` until 2026-08-06, and it
did not do what its own comment said. It claimed to find the ruleset targeting `main` and
in fact unioned the required checks of *every* branch-target ruleset — no name filter, no
`conditions.ref_name` filter — so an organisation-level ruleset arriving through
`includes_parents` would have been reported as drift against a correct configuration. It
also never read `enforcement`, so a ruleset switched to `disabled` or `evaluate` still
listed its required checks and the job printed that everything agreed over a branch
protected by nothing. Both faults are now fixtures in `--selftest`, which `fast` runs
offline on every push: the rules are unit-tested, and only the network half waits for the
weekly cron.

The shared anchor is [`.github/required-checks.txt`](../.github/required-checks.txt), which
is the only record in this repository of a fact that otherwise exists solely in GitHub
settings. It is a *record*, not a control plane: adding a line does not make a check
required. Change the ruleset first, then record it, or the weekly job will report that they
disagree, which is precisely its job.

A third check falls out of the same derivation: **every check name `ci.yml` produces must
appear on this page.** Add a job without documenting it, or rename one and leave the prose
behind, and `doc-claims` fails. That is not hypothetical politeness. The
`branch ruleset vs required-checks.txt` row in the table above exists because adding that job
failed this check, which is how it should work.

**This is also why the `stack` job is `if: ${{ !cancelled() }}`** rather than gated like its
siblings. A *skipped* matrix job does not interpolate its name, so it would report as the
literal string `full stack on kind (${{ matrix.profile }})` and the two required names would
never appear. Instead the job starts whenever the run has not been cancelled, the matrix
expands, both names report, and every *step* inside is gated on a single `RUN_STACK`
condition. When nothing was actually stood up the job says so out loud with a `::notice::`,
because "this green means nothing ran" is something the reader deserves to be told.

⚠️ It was `always()` until 2026-08-06, and the difference is not cosmetic: `always()` returns
true **even when the run has been cancelled**, so a superseded push left both legs running to
completion and quietly defeated `cancel-in-progress`. `!cancelled()` keeps every property
above — when `fast` fails it is still true, so the job still runs and both names still
report — and stops only on genuine cancellation, where the superseding run is the one whose
checks anyone is waiting on. GitHub's own guidance names `!cancelled()` as the form to prefer.

The four gated jobs (`compose`, `chart`, `image`, `chart-cluster`) use the same
`!cancelled() && …` shape. A plain `if:` would be *safe* for a non-matrix job, since a skipped
one still reports under its own name — but it carries an implicit `success()` over `needs`,
which is what used to let a failed `changes` job skip them into a green ruleset.

For the same reason, none of this uses `paths-ignore:` on the triggers. A job that never
*starts* leaves its check pending forever; a job that starts and is skipped reports as
successful. Docs-only pull requests need the second behaviour.

---

## Timeouts, and why the big one is so big

The `stack` job allows 90 minutes, which looks generous until you add up the repo's own
waits: two Helm installs at `--wait --timeout 15m`, rollout waits at 5 minutes each across an
unbounded number of DaemonSets, three minutes per simulator, and roughly twenty minutes of
polling in `verify.sh`. Measured, that is a worst case around 75 minutes.

The reason the cap sits above the worst case rather than near the healthy case is the
important part. Every rollout wait is `|| true`, so a partially broken cluster does **not**
fail fast: it walks every timeout in turn and then fails in `verify.sh`. A tighter cap would
guillotine exactly the run whose diagnostics you need.

A healthy run is nowhere near it. If a green run ever approaches 35 minutes, something is
waiting that should not be.

### Current measured timings

⚠️ **This page owns these numbers.** They used to live in `CLAUDE.md`, which is loaded into
every session, and this page pointed there. That was backwards twice over: timings are
reference data rather than standing law, and the pointer did not stop `CONTRIBUTING.md`
keeping a second copy anyway. Both copies then drifted from the runs they named. State them
once, here, and point at this section.

Measured on run [`30998470446`](https://github.com/ChrisAdkin8/k8s-ai-observability/actions/runs/30998470446)
(2026-08-05, a single run, so read every total as one sample of a variable quantity):

| Job | |
|--|--|
| `what changed` | 5s |
| `helm chart (lint, render, assertions fire)` | 12s |
| `selftest + rule tests + shell syntax` (`fast`) | 17s |
| `simulator image (build both arches, smoke-test amd64)` | 19s |
| `compose stack (no Kubernetes)` | 60s |
| `chart on kind (helm test, foreign Prometheus)` | 4m09s |
| `full stack on kind (lite)` | 4m38s |
| `full stack on kind (full)` | 5m00s |
| **whole workflow** | **5m27s** |

`verify.sh` itself accounts for 160s of `full` and 150s of `lite`.

⚠️ ~~The `fast` row is a true measurement of a job that no longer exists.~~ **DONE —
re-measured 2026-08-07 on run `31177224542`, and the prediction it carried was wrong.**
Four steps had been added on 2026-08-06 — installing and running `actionlint`,
`check-action-shell.py` and `check-required-checks.py --selftest` — and the note expected
roughly a 5 MB download and nine extra shellcheck invocations to show. They did not.

Three runs on `main` that day, all after those steps landed, and `fast` has no conditional
steps or job-level `if:`, so it does identical work on every one of them:

| Run | `fast` |
|--|--|
| `31171716804` | 21s |
| `31176137834` | 15s |
| `31177224542` | 17s |

The old 16s sits inside that spread. What the table was recording as staleness was runner
variance, and the added work is not visible above it — which is the answer only a
measurement could give, and the reason the row was left standing rather than adjusted by
guess. **A single figure here is worth ±3s at best**; treat it as an order of magnitude,
and re-measure rather than reason if a change to `fast` ever needs defending.

⚠️ ~~These predate the early ServiceMonitor apply.~~ **DONE — re-measured 2026-08-05 on run
`30998470446`, the first run after it.** The pre-change figures, on run `30870290833`, were
`full` 6m12s, `lite` 4m42s, workflow 6m27s, and `verify.sh` 215s and 160s. Read the totals
as single samples; the unambiguous evidence is **check 3, which now lands in 4s (`full`) and
5s (`lite`)** rather than waiting out a 0-180s config-reload poll.

### The timings have been wrong twice

⚠️ ~~The two legs run about 5.5 minutes each.~~ **Wrong twice over, and kept here rather
than deleted because the reasoning outlives the correction.** They were never equal, and
until the port-forward fix `lite` was the *slower* leg — 6m39s to 8m32s against `full`'s
5m19s to 6m25s — despite installing less. Both figures were re-derived on 2026-08-04 from
run `30870290833`, where `verify.sh` on `lite` went 383s to 160s.

The lesson is the habit, not the numbers: **quote the run id with any timing you take from
a CI page.** A figure with no run behind it cannot be checked later, and both wrong versions
of this claim looked perfectly plausible.

---

## Optional secrets

CI works with no secrets configured. If you set **both** `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` (a free account and a read-only token), the jobs that pull from Docker Hub
log in first.

Four images come from Docker Hub, anonymous pulls are rate-limited per IP, and Actions
runners share egress IPs, so unauthenticated runs are simply flakier. The login step is
gated on the secrets being present, and that gate matters: **pull requests from forks never
receive secrets at all**, so an ungated login would fail every external contribution and look
like the contributor's fault.

---

## When something fails

The `stack`, `compose` and chart-on-kind paths collect diagnostics and upload them as
artifacts on failure. The stack artifact name includes the matrix profile, because both
legs upload and two artifacts of the same name collide.

⚠️ Two of those three were broken, in opposite ways, and both are worth knowing about
because the shape recurs. `compose` gated its collection on
`steps.up.outcome != 'success'` — the outcome of `docker compose up -d`, which is step 2 of
6 and succeeds essentially always. So the bundle was collected *only* when compose failed
to start, and never on any failure the job actually exists to catch. It read as correct
because the `stack` job's identical-looking guard **is** correct: there `up` is
`task local:up`, which is the whole job. Both now key on `job.status`. And the chart-on-kind
path collected nothing at all — the required check that touches a cluster, and the one that
also gates a release, gave you whatever `helm test --logs` printed and no pod list, events
or describes. That now lives in the composite action, so both callers get it.

The pattern under all three: **a guard that only ever runs on green runs has never been
observed doing anything**, which is iron rule 18 pointed at CI itself.

⚠️ The chart one proved that twice. Its first version was `if: always() && job.status !=
'success'`, copied from the compose fix — and **`job.status` is not updated inside a
composite action**: it reads `success` even after a step there has exited 1
([actions/runner#1682](https://github.com/actions/runner/issues/1682)). So the replacement
guard was constant-false and collected nothing, in the same change that fixed the original.
Nothing was watching, because `actionlint` cannot parse a composite action and
`check-action-shell.py` read only the shell, not the expression above it. It reads both now:
a `job.` reference inside a composite action is a hard error there, with a selftest, and the
real guard keys on `steps.<id>.outcome`, which does work.

`check-action-shell.py` now covers the **general** form of that bug as well as the
specific one. A guard can die two ways, and both are silent because GitHub resolves an
unknown context member to the empty string rather than failing the run:

| Written | Why it never fires |
|--|--|
| `job.status != 'success'` | the context is inert inside a composite action |
| `steps.clustr.outcome == 'failure'` | one letter out; no step declares that `id:` |

Both are hard errors there, with selftests, and both are driven to failure against the
real file. Closing the second one exposed a third: `bad_contexts` matched `^\s*if:` line
by line, so a **folded** `if: >-` hid everything on its continuation lines — and the
guard being protected is written exactly that way, six terms over six lines. Expressions
are joined before either check reads them now.

⚠️ **That guard is still not yet verified against a real run.** The mechanical checks
stop both known-wrong constructs returning; neither proves the right one fires. Nothing
in `task preflight` executes a composite action, so the first real evidence will be a red
`chart on kind` that uploads a bundle. Until then this is reasoned, not observed.

Reproduce locally with the same commands CI uses:

```sh
task preflight        # the fast gates: selftest, compose-selftest, drift, doc-claims, rules, chart
task local:up         # the whole thing, exactly as the stack job runs it
LITE=1 task local:up  # the lite leg
```

---

## Pinned versions

The toolchain is pinned in the workflow's `env:` block: `kind`, `kubectl`, `helm`, `task`,
`actionlint` and Prometheus (for `promtool`). An unpinned toolchain turns someone else's
release into a mystery red run on an unrelated commit.

**Every one of them is checksummed too.** A version pin fixes the *name* of an artefact,
not its bytes: `curl -fsSL <url>` trusts whatever the far end serves under that name. The
SHA-256 sums sit beside the versions in the same `env:` block and come from each
publisher's own checksum file, so bumping a version fails loudly until the sum moves with
it. The actions are pinned by commit SHA and tended by Dependabot; the binaries had the
weaker half of that guarantee and none of the second.

**The runner is pinned too**, to `ubuntu-24.04` rather than `ubuntu-latest`. It was the
largest unpinned dependency in the repo — a runner-image migration moves shellcheck,
python3, docker, buildx and the preinstalled `kubectl` at once, on a date nobody here
chooses. The cost is one obligation in the other direction: GitHub deprecates an image
about a year before removing it, so that number has to move deliberately, roughly annually.

Three of those pins are coupled to things outside the workflow. `kind` ships a default node
image that must match what `kind/gpu-sim.yaml` pins, and `kubectl` tracks that node image's
minor version. ⚠️ The kind pin is written in **three** places, not two — `ci.yml`,
`publish-chart.yml` and the `kind-version` input default in
[`.github/actions/verify-chart`](../.github/actions/verify-chart/action.yml) — and the
third had drifted two minors behind while both callers fell through to it.
`check-doc-claims.py` now asserts all three agree. Helm is deliberately held on the v3
line: v4 is a major this repo has not validated, and CI should exercise what people
actually run.

`kube-prometheus-stack` is pinned as well, in the composite action, **derived from
`scripts/config.sh` rather than restated**. It used to install whatever was newest, which
meant the required `chart on kind` check was not reproducible and an upstream release could
redden an unrelated pull request — the exact thing `weekly upstream drift` is
schedule-only to avoid.

Every pin in the repo, and the single place each one is set, is in
[docs/versions.md](versions.md).

---

## Publishing is separate

Release artifacts are not built by this workflow. Two others fire on a `v*` tag:

- [`publish-image.yml`](../.github/workflows/publish-image.yml) builds and pushes the
  simulator image, then pulls it back and scrapes it.
- [`publish-chart.yml`](../.github/workflows/publish-chart.yml) packages the chart from
  `dist/`, pushes it, then pulls it back **anonymously** and installs it against a foreign
  Prometheus on a throwaway cluster.

Both verify by consuming what they published, rather than trusting that the push succeeded.
A successful push only proves bytes moved.

⚠️ **Both now refuse to publish from a commit CI has not passed.** The ruleset on `main`
gates pull requests and says nothing about a tag, so `git tag` on any commit and
`git push origin vX.Y.Z` used to publish from whatever it contained — and a registry
version is immutable. [`.github/actions/require-green-ci`](../.github/actions/require-green-ci/action.yml)
is a thin wrapper over `scripts/check-green-ci.py`, which reads
`.github/required-checks.txt` and polls the check runs on that commit until every one has
concluded. It polls rather than reads once because `docs/releasing.md` pushes the commit
and the tag in a single line, so CI cannot have finished. A `workflow_dispatch` input,
`allow_red_ci`, is the documented escape hatch for the case where a dispatch rebuilds a
fixed workflow from a different ref.

⚠️ **The logic is a script and not a heredoc, for the reason `settings-drift` is.** It
began as eighty lines of shell and inline Python inside the action — pagination, newest
attempt per name, the classification, two timing branches — none of it lintable, none of
it testable, all of it deciding whether an immutable artefact ships. "Verified by hand"
there means running a *copy* in a scratch directory, which is the drifting second copy
this repo refuses everywhere else. Extracting it is what made
`tests/fixtures/check-runs.json` possible, and that fixture pins the branches a live run
touches least: a re-run whose newest attempt failed, a required check that fell onto page
two, a truncated read, a commit with no run at all, and a `neutral` conclusion that must
not read as a pass. It also dropped `gh` — an unpinned binary from the runner image, in
the one path that gates an immutable release. The script is stdlib `urllib`.

## Static analysis is separate too

[`codeql.yml`](../.github/workflows/codeql.yml) runs CodeQL over **Python and GitHub
Actions workflows**, on pushes to `main`, on pull requests, and weekly.

⚠️ **It does not read Shell, and Shell is 40% of this repository.** CodeQL supports neither
Bash nor HCL nor YAML, so `verify.sh`, `install.sh` and `config.sh` — where most of the
install logic lives — are invisible to it. Two of the bugs found on 2026-08-04 were shell,
and neither would have been caught by it. That gap is now covered separately: the `fast` job runs
**`shellcheck -S warning scripts/*.sh`**, on the whole directory in one invocation rather
than file by file. That matters — `verify.sh` sources `config.sh`, and the only real clash
shellcheck has found here (a name that is an array in one and a string in the other) is
invisible when they are linted apart.

It is **advisory**, not a required check: findings need triage, and a scanner that blocks
merges on a false positive costs more than it returns. The `actions` language is the half
that earns its keep, because it finds `${{ }}` expressions interpolated into `run:` bodies —
script injection when the value is attacker-controlled.
