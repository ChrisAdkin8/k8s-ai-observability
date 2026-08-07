# Prompt: CI that stands up the whole stack on every push

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.2.0–0.4.0**: the CI stack legs, the compose job and the lite matrix leg. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Add
GitHub Actions CI that executes the repo's advertised one-shot path — `task local:up` —
end to end on every push and pull request, including all of `scripts/verify.sh`'s
acceptance checks, and surfaces the result as a badge in the README.

**Why this matters more here than in a typical infra repo.** Most Kubernetes repos
cannot test themselves in CI, because they need real cloud accounts or real hardware.
This one's entire premise is "no GPU required", which means a free GitHub Actions runner
can prove the full claim on every commit. That badge is the strongest credibility signal
this repo can carry, and almost no comparable project can match it. It is also a
correctness net: the stack depends on two upstream Helm charts and a chain of naming
invariants that break silently.

End state: a green check on `main`, a badge at the top of the README that means "the
whole stack came up and passed every check, on a machine that has never seen a GPU", and
a weekly scheduled run that catches upstream drift before a stranger files an issue.

## Background / Technical Facts (verified — use these, do not re-derive)

These were checked against the repo on 2026-07-30. They are the answers to the questions
you would otherwise burn a CI run each discovering.

- **Runner sizing fits, with no warnings.** `scripts/config.sh:124-127` sets
  `KIND_MIN_MEMORY_GIB=5` / `KIND_MIN_CPUS=2` (below this, `kind-up.sh` refuses) and
  `KIND_WANT_MEMORY_GIB=8` / `KIND_WANT_CPUS=4` (below this, it warns). GitHub's
  `ubuntu-latest` runner is **4 vCPU / 16 GiB**, which clears the refusal threshold and
  meets the recommended values, so the sizing preflight passes silently.
- **The sizing preflight reads the runtime, and that works on a runner.**
  `scripts/kind-up.sh:36` runs `docker info --format '{{.MemTotal}} {{.NCPU}}'`. Docker is
  native and preinstalled on `ubuntu-latest`, so this returns real host values rather
  than a VM's. No colima/Docker Desktop/podman handling is needed.
- **The scripts are already Linux-clean.** Every script, the `Makefile` and both
  Taskfiles were scanned for BSD-only constructs (`sed -i ''`, `base64 -D`, `stat -f`,
  `date -v`/`-r`, `readlink -f`, `xargs -J`, `/usr/libexec`). **Zero hits.** Do not
  pre-emptively "port" anything; if something does break on Linux, fix that specific
  thing and note it.
- **The `up` chain never blocks on a TTY.** The only `prompt:` in
  `taskfiles/target.yml` is on `destroy` (line 195). `up` → `cluster` → `install` →
  `verify` is non-interactive. Do not pass a `--yes`-style flag to `up`; it takes none.
- **No credentials are required.** The `local` target reads none of the `.env` values,
  and `dotenv: ['.env']` in `Taskfile.yml` tolerates the file being absent.
- **Required tooling.** `kubectl`, `helm`, `curl`, `python3` are needed on every target;
  `kind` + a container runtime for `local` only. On `ubuntu-latest`, `curl`, `python3`
  and `docker` are preinstalled — you must install `kind`, `kubectl`, `helm` and `task`.
  `terraform`, `aws` and `gcloud` are **not** needed and must not be installed.
- **Image registries, for rate-limit purposes.** `fake-gpu-operator` publishes to
  `ghcr.io` (no anonymous limit worth worrying about). The Docker Hub images in play are
  `kindest/node:v1.36.1`, `python:3.12-slim`, `busybox:1.36`, and `grafana/grafana` from
  `kube-prometheus-stack`. Prometheus, Alertmanager and node-exporter come from
  `quay.io`; kube-state-metrics from `registry.k8s.io`.
- **Cost is zero.** Public repositories get unlimited standard-runner minutes.
- **⚠️ Kernel inotify limits are not handled anywhere in this repo.** Verified: nothing in
  `scripts/`, `kind/` or `docs/` touches `inotify`, `sysctl` or `ulimit`. Running kind in
  CI routinely exhausts `fs.inotify.max_user_watches` and `max_user_instances` — kind's own
  docs flag it — and this stack is a prime candidate, with the Prometheus Operator,
  Prometheus, Grafana's dashboard sidecar, kube-state-metrics, node-exporter, the device
  plugin, the DCGM exporter and two `llm-sim` deployments all establishing watches.
  **The symptom actively misleads, and the repo's own error message makes it worse:** pods
  crashloop with "too many open files", or the Grafana sidecar silently never imports the
  dashboard ConfigMaps. In that case **check 4 passes** (`verify.sh:78` — the ConfigMap
  exists regardless of whether the sidecar consumed it) while **check 4b 404s**
  (`verify.sh:107`). And 4b's failure text reads *"no dashboard with uid ... (sidecar not
  imported it yet?)"* — which points squarely at a timing problem. The reasonable response
  is to raise the polling loop, watch it fail again, and never suspect a kernel limit. If
  you see 4 pass and 4b 404, check inotify before you touch anything else. See C2 step 3.

### Wall-clock, derived — and why the job timeout must be large

Do not take a timeout number on trust, including from this document. Derive it, and
re-derive it if `install.sh` changes. The sum below is what the repo's own timeouts
permit:

| Stage | Worst case | Source |
|---|---|---|
| kind cluster create | ~90s | empirical |
| `helm` kube-prometheus-stack | 15 min | `install.sh:40` — `--wait --timeout 15m` |
| `rollout status` operator | 5 min | `install.sh:45` — `--timeout=5m` |
| `helm` fake-gpu-operator | 15 min | `install.sh:64` — `--wait --timeout 15m` |
| `rollout status` per DaemonSet | 5 min **× N** | `install.sh:80-81` — loops over `get ds -o name`, count unbounded |
| `rollout status` llm-steady, llm-saturated | 3 min each | `install.sh:98-99` |
| `verify.sh` polling loops | ~16 min | `(12×5s) + (20×2s) + (12×5s) + (36×10s) + (24×5s) + (30×10s)` at lines 98, 136, 163, 179, 222, 287 |
| `verify.sh` standalone sleeps | ~1 min | lines 101, 166, 182, 225, 290 |

**Healthy run: 15-25 min. Worst case: ~80 min at N=4.**

The gap is this wide because every `rollout status` in `install.sh` is `|| true`, so a
partially-broken cluster does not fail fast — it walks through each timeout in turn and
fails later, in `verify.sh`. That is deliberate upstream (a GPU pod created early is
merely Pending until capacity appears; `mig-faker` legitimately has 0 desired replicas),
and not yours to change. The consequence: a cap below the worst case guillotines the
*broken* run, which is the one whose output you need.

**Set `timeout-minutes: 90`, then verify that number against reality.** N is genuinely
unbounded in the source, and 90 only holds for N ≤ 4 — at five or six DaemonSets the cap
is breached again. On your first run, record `kubectl -n gpu-operator get ds` and confirm
the real N; if the arithmetic exceeds 90, raise the cap rather than trusting this figure.
Treat `90` as a starting estimate you are required to check, not a verified constant.

Optional, and authorised as a specific exception to the "no changes to `scripts/`"
non-goal: make `install.sh`'s Helm timeout overridable via an environment variable
defaulting to the current `15m`, and set a lower value in CI to get fail-fast. Keep it to
that — do not restructure the wait logic. A 90-minute cap without it is acceptable.

## Requirements

### C1 — Go through the repo's own entry point, not around it

The cluster-creation step **must** be `scripts/kind-up.sh`, invoked via
`task local:up`. Do **not** use `helm/kind-action` or any other action to create the
cluster, and do not inline `kind create cluster`.

This is not stylistic. `kind/gpu-sim.yaml` carries the
`run.ai/simulated-gpu-node-pool` node label that the entire stack selects on;
`kind-up.sh` owns the sizing preflight and the rename of kind's context to
`gpu-sim-local` that every downstream script expects. A workflow that creates its own
cluster stops testing the thing users actually run, and would pass while the real entry
point is broken.

Installing the `kind` **binary** via an action or a direct download is fine and expected.

### C2 — The full-stack job

One job on `ubuntu-latest`:

1. Checkout.
2. Install `kind`, `kubectl`, `helm`, `task`. **Pin every one to an exact version**, and
   pin any third-party action to a full commit SHA rather than a tag — an unpinned
   toolchain turns an upstream release into a mystery red run on an unrelated commit.
   `task` must be >= 3.32, per the note in `Taskfile.yml`. Prefer direct versioned
   downloads over actions where it is no more code; either is acceptable if pinned.
3. **Raise the inotify limits** before anything touches the cluster:
   `sudo sysctl -w fs.inotify.max_user_watches=524288 fs.inotify.max_user_instances=512`.
   See the warning in Background for why, and for the misleading symptom if you skip it.
   Comment the step with that symptom, so a future red run is diagnosable.
4. **Preflight in two parts, because `task tools` alone is not sufficient here.** Run
   `task tools`, then *additionally* assert `kind version` and `docker info` succeed.
   `task tools` marks `kubectl`/`helm`/`curl`/`python3` as `fatal` but `kind` and
   `docker` as **optional** — deliberately, so it is not red for someone who only uses a
   cloud target. Those two are exactly what this job depends on, so a silently failed
   `kind` install would let `task tools` exit 0 and surface 12 minutes later inside
   `kind-up.sh`.
5. Run `task local:up`.
6. `timeout-minutes: 90` — see the derivation above, including the requirement to check it
   against the real DaemonSet count.

### C3 — Failure diagnostics are mandatory, not optional

A red run that says only "verify.sh failed" is nearly useless, and debugging it by
pushing commits is miserable.

**Gate the step on `if: always()`, not `if: failure()`.** A job that exceeds
`timeout-minutes` is *cancelled*, and `failure()` is not guaranteed to fire on the
cancellation path — so the idiomatic-looking choice collects nothing on the run you most
need it for.

One caveat this creates, since C5 also mandates `cancel-in-progress: true`: `always()`
will fire on runs cancelled by supersession too, uploading artefacts nobody will read and
cluttering the list you are scanning during calibration. Skip the dump when the
cancellation came from supersession rather than a timeout.

Capture at minimum:

- `kubectl get pods -A -o wide` and `kubectl get events -A --sort-by=.lastTimestamp`
- `kubectl describe` on every pod not in `Running`/`Completed`
- Logs from the DCGM exporter, the device plugin DaemonSet, and both `llm-sim`
  deployments (`llm-steady`, `llm-saturated`)
- Prometheus `/api/v1/targets` and the `PrometheusRule` evaluation state — the
  most common real failure is a scrape target down or a rule not yet evaluated
- `kubectl get nodes -o json` showing `allocatable`, since a missing
  `nvidia.com/gpu` allocatable is the signature of the naming-invariant break

Upload it with `actions/upload-artifact`. Do not let this step fail the job itself
(`continue-on-error` or `|| true` on each command).

### C4 — A fast job that does not need a cluster

A second job, running in parallel, that finishes in seconds:

- `task selftest` (`python3 scripts/llm-sim.py --selftest`) — stdlib-only, no cluster,
  no network. It validates bucket monotonicity, `+Inf` consistency and HELP/TYPE
  correctness.
- `bash -n scripts/*.sh` — syntax only, and note `scripts/llm-sim.py` is Python, so the
  glob matters.

**Use `bash -n`, not `shellcheck`, and this is a deliberate choice rather than an
oversight.** `bash -n` catches only syntax errors; `shellcheck` finds real bugs and would
be more valuable. But pointing it at eight existing scripts will surface a backlog of
warnings, and triaging those is a separate piece of work that would swallow this one. If
you want `shellcheck`, add it in a follow-up where the warning triage is the actual task.

This gives contributors near-instant feedback on the most commonly edited file
(`scripts/llm-sim.py`) without waiting 20 minutes.

### C5 — Triggers and concurrency

- `push` to `main`, `pull_request`, and `workflow_dispatch`.
- A **weekly `schedule`** — the stack tracks two upstream Helm charts and a pinned
  `kindest/node` image, so the cron is what turns upstream drift into a failure you find
  rather than an issue a stranger files.
- **The cron cannot be verified before merge.** `schedule` runs only on the default
  branch. Have `workflow_dispatch` invoke the *same* job the cron invokes so dispatch
  proves the code path, and say in your summary that the trigger itself is verified on its
  first firing after merge. Do not claim it as tested pre-merge.
- A `concurrency` group keyed on the ref with `cancel-in-progress: true`, so pushes do
  not stack 20-minute runs.

### C6 — Docker Hub authentication

Anonymous Docker Hub pulls are capped per-IP and Actions runners share egress IPs; four
of the images above come from Docker Hub, so this is the most likely source of
intermittent reds.

Add `docker/login-action` using two repository secrets — name them
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a free account and a read-only access token
suffice) — and document both in the workflow header comment.

Two constraints make the gate mandatory rather than defensive:

- **Fork PRs never receive secrets**, by design. An ungated login step fails outright on
  every external contribution, making the first drive-by PR look like the contributor's
  fault.
- **The `secrets` context is unavailable in a job-level `if`.** Gate at step level, or
  surface the secret into `env` and test that. Unauthenticated is the correct fallback:
  flakier, but it runs.

### C7 — Badges

Three badges above the first heading in `README.md`, all required: **CI status**,
**license (MIT)**, and the **pinned Kubernetes version** (`v1.36.1`, from
`kind/gpu-sim.yaml:33`). The licence badge is not decoration — GitHub's detection has not
picked up the `LICENSE` file, so the sidebar may show nothing despite the repo being MIT.

### C8 — Do not weaken the checks to get green

**This is the requirement that matters most.** The value of this work is entirely in the
badge being truthful. If a check fails or flakes:

- Diagnose the actual cause first.
- If it is a genuine timing bound being marginal on a slower runner, raise the specific
  iteration count in `verify.sh` and say so in the commit message.
- **Never** delete or `|| true` an acceptance check, add a blanket retry around
  `verify.sh`, mark the job `continue-on-error`, or narrow what `local:up` runs.

A green badge over a hollowed-out verify is worse than no badge, because it actively
misleads. If a check cannot be made to pass honestly on a runner, stop and report that
rather than working around it.

Do not renumber `verify.sh`'s checks (`1, 2, 3, 4, 4b, 4c, 4d, 5`, `L1`-`L6`) — the
numbers are cited across the repo.

## Non-goals

- **No CI for the EKS or GKE targets.** They need real credentials and cost real money.
  The `local` target runs the same manifests, the same pinned charts and the same
  acceptance checks, so it is the honest thing to gate on. A `terraform validate` /
  `fmt -check` job is a reasonable optional addition; `terraform apply` is not.
- **No refactoring of `scripts/`, `manifests/` or the Taskfiles** beyond a specific,
  justified fix for something that genuinely breaks on Linux, plus the one optional Helm
  timeout override authorised above.
- **No caching.** Not Helm charts, not Docker or kind node images, not `~/.cache`. It is
  fiddly around kind, saves little off an acceptable 15-25 min run, and a stale cache can
  make CI pass against artefacts a user would no longer pull — defeating the weekly drift
  run. Revisit only if runtime becomes a real problem.
- **No new abstraction layer.** The workflow should be thin over `task local:up`, for the
  same reason `taskfiles/target.yml` is thin over `scripts/` — the moment CI
  reimplements the install logic, the two disagree.

## Acceptance criteria

1. A workflow run triggered from a branch completes green, having actually created a kind
   cluster, installed both Helm releases and passed every `verify.sh` check. Paste the run
   URL and the `verify.sh` output.
2. A healthy full-stack run lands in the 15-25 minute range and nowhere near the
   90-minute cap. Report the actual figure — if it exceeds ~35 minutes on a green run,
   something is waiting that should not be, so investigate rather than accept it.
3. The fast job (C4) completes in under two minutes.
4. Deliberately breaking something recoverable — e.g. temporarily pointing the node-pool
   label at a wrong value — produces a **red** run whose uploaded diagnostics are
   sufficient to identify the cause without re-running. **Do this on a scratch commit and
   revert it before the final diff**, so it does not collide with criterion 5. Report the
   red run's URL alongside the green one.
5. After the AC4 revert, `git diff` against `main` touches only `.github/`, `README.md`
   (badges), and — if and only if the escape hatches were genuinely needed — a specific
   iteration count in `verify.sh` and/or the authorised Helm timeout override in
   `install.sh`, each with a commit message explaining why.
6. No acceptance check was removed, skipped, retried wholesale, or made non-fatal.
7. The diagnostics step is gated on `always()`, and you have confirmed from a real run
   that the artefact uploads.
8. **The 90-minute cap has been checked against the real DaemonSet count, not assumed.**
   Report N from `kubectl -n gpu-operator get ds` and the resulting worst-case sum. If it
   exceeds 90, the cap was raised.
9. The inotify step is present. It is precautionary — do **not** run an A/B experiment to
   prove it was necessary, since the step stays either way and the run costs 25 minutes for
   information that changes nothing. Equally, do not drop it because the first run passed.

## Deliverables

- `.github/workflows/ci.yml` (or split workflows, if that is cleaner), commented in the
  house style: explain *why* each non-obvious step exists, particularly the C1 decision
  to route cluster creation through `kind-up.sh`.
- The three README badges.
- A short summary of: the green and red run URLs, actual wall-clock time, the real N and
  recomputed cap, whether inotify mattered, anything that broke on Linux and how it was
  fixed, and any flake with its diagnosis.

## Process

Work on a branch and iterate against **real runs** — `gh run watch` — until it is green.
Do not merge on the strength of a plausible-looking YAML file: workflow files are almost
never right first try, and the point of this task is a badge that has never been red on
`main`. Expect two to four iterations; budget for them rather than treating the first red
as a surprise.

One standing instruction, given how prescriptive this brief is: **where it states a
number, treat it as a starting estimate to verify, not a constant to obey.** The timeout
is the worked example — it was wrong once already.
