# Prompt: Verify the verifiers

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.9.0**. All four items are done: the chart's own
> verification runs on every pull request, both scripts reject unknown arguments, a wrong
> `RELEASE_LABEL` is reported in one second instead of ten minutes, and the
> `pipefail`-plus-early-exit class is fixed and mechanised. Nothing here is outstanding.
>
> ⚠️ **Its `Background` describes the tree BEFORE this work landed** and is stale by
> construction — this prompt is what changed it.
>
> **Two things it got wrong, kept because a record's value is being what was written.**
> The W3 estimate of half a day was too high: the label-selector comparison turned out to
> be deterministic, exactly as W3.2 suspected, so it was closer to an hour. And the sweep
> in W4.2 was scoped to `scripts/` and `.github/workflows/` — the mechanical checker it
> asked for immediately found three more hits in `charts/`, which the human sweep had not
> thought to look at.

## Role & Objective

Four defects found in a review on **2026-08-04**, all of them in code whose job is to
*check* other code. Fix them, and close the gap that let them survive.

The through-line matters more than any single item. On 2026-08-04 four bugs surfaced in one
day — `helm test` inverting pass into fail, the chart-version guard colliding with the
release it gated, an archive check SIGPIPEing before it asserted anything, and a skew check
asserting equality where the truth is a window. **All four were in verification code.** In a
repo whose stated product is verification, the verifiers are the least-verified part,
because nothing verifies them.

⚠️ **That through-line is the motivation, not a work item, and this prompt does not claim
to fix it.** W1 closes it for the chart and W4.2 closes it for one failure class; the
tendency itself — verification code being held to a lower standard than what it verifies —
is not something one prompt fixes. Do not read the four items as a structural remedy. If a
structural item emerges while doing them, it belongs in the *next* prompt, which is the
convention this one is otherwise departing from.

`CLAUDE.md` is the standing law; this prompt cites it by rule number and does not restate
it. Rules 5, 6, 7, 10, 11 and 16 all bear on the work below.

⚠️ **This covers four items, and the house convention is one prompt per work item** —
*"implementing Wn is what makes Wn+1's prompt honest"*. The departure is deliberate: W2 and
W4 are an hour each and fully independent, and holding them behind W1 would delay two fixes
that are ready for one that is not. **W1 and W3 are the parts that could invalidate what
follows**, and both are written to be re-derived rather than followed. If W1 turns out to be
a day of CI plumbing, split it back out; nothing else here depends on it.

---

## Background: verified facts

Read from the tree on **2026-08-04**, at the commit that published chart `0.2.1`.

**The chart is never installed by a pull request.**

- `.github/workflows/ci.yml:575` — the `chart` job is `helm lint`, `helm template` both ways,
  and driving the render-time assertions to failure. It never contacts a cluster.
- `.github/workflows/ci.yml:887` + `:1060` — the `stack` job runs `task local:up`, which is
  the **script** path (`install.sh`), not the chart.
- `.github/workflows/publish-chart.yml:426`, `:443`, `:448` — the only `helm install` and
  `helm test` of this chart anywhere in the repo, in a job that runs **on a tag**.
- Consequence, observed: chart `0.2.0` was published with `helm test --logs` exiting 1 on a
  chart where all four hooks reported `Phase: Succeeded`. Registry versions are immutable,
  so it is still there. `0.2.1` supersedes it.

**`install.sh` inspects one argument.**

- `scripts/install.sh:21` — `TARGET="${1:?...}"`.
- `scripts/install.sh:30` — `case "${2:-}" in`, which is the whole of the validation.
- Verified by running it: `./scripts/install.sh local --skip-monitoring --bogus` does not
  reject `--bogus`; it proceeds and fails later for an unrelated reason.
- `scripts/teardown.sh:11` — `DESTROY="${2:-}"` compared against the literal, so it has the
  same hole, and `install.sh`'s own comment already describes teardown's laxity as the
  behaviour it deliberately does *not* copy.

**`verify.sh` polls for a deterministic misconfiguration.**

- `scripts/verify.sh:167,347,404,425,566,714` — poll budgets of 240, 40, 120, 360, 300 and
  420 seconds. **~1480s ≈ 24 minutes** if every one is exhausted.
- `scripts/verify.sh:234` — the first check to fail on a wrong `RELEASE_LABEL`, and it
  already carries the correct diagnosis via `byo_hint()`.
- `scripts/verify.sh:191` — the BYO banner, printed once before any check runs.
- Measured 2026-08-04 against a foreign Prometheus (release `acme-mon`): **≥10 minutes to
  report a wrong label** (the run was stopped there), against **~90 seconds to pass** once
  the label was right. Failing is slower than succeeding, by roughly 7x.

**One assertion can only fail open.**

- `.github/workflows/ci.yml:834` —
  `if grep '^vllm:' /tmp/scrape.txt | grep -q 'source="'; then`
  under `set -euo pipefail`. When a `source=` label **is** present, `grep -q` exits at the
  first match, may SIGPIPE the producer, and the pipeline returns 141 — so the `if` is false
  and the violation goes unreported. When nothing is wrong, it behaves correctly.
- This is the same class as the bug that broke the first chart publish, where
  `tar tzf | head` returned 141 and failed a step on a correct archive.

---

## W1 — Run the chart's own verification on every pull request, without a second copy of it

**W1.1 ⚠️ THE JOB YOU ARE ASKED TO WRITE ALREADY EXISTS. DO NOT COPY IT.**
`.github/workflows/publish-chart.yml:382-450` creates a kind cluster, labels a node,
installs kube-prometheus-stack under a foreign release, asserts `helm test` **fails** on the
default `releaseLabel`, asserts it passes when set, and checks both boards. That is W1,
against a different subject.

Copying those ~70 lines into `ci.yml` would put **two copies of this repo's most important
verification** in the tree, free to drift, and a drifted copy is invisible from the outside.
This repo has refused that trade everywhere else: `chart-build.py` exists so no dashboard is
committed twice, and `publish-image.yml` delegates its version check to `chart-build.py`
rather than re-implementing it — the first attempt at re-implementing it was wrong in a way
that would have failed every correctly configured release.

**So the first question is not "where does the new job go" but "can this be one
implementation on two paths"** — a composite action or reusable workflow taking the chart
source (local `dist/` vs published `oci://`) and the release/namespace names as inputs, and
called by both. If it cannot, say concretely what blocked it. "It was easier to copy" is not
an answer this repo accepts elsewhere.

The negative case is the one with teeth: an assertion that only ever passes is not an
assertion.

**W1.2 Placement falls out of W1.1, and is not a free choice.** Whatever calls the factored
steps needs **its own cluster**: the chart's default namespaces are `monitoring`,
`gpu-operator` and `llm-sim` (`charts/k8s-ai-observability/values.yaml:81-83`), the same ones
`install.sh` uses, so installing the chart into the `stack` job's cluster after
`task local:up` collides — Helm will not adopt resources it does not own. That rules out
adding a step to `stack` and rules out a third matrix leg on it. Record the choice where it
lives; the cost is in the Effort table and is already measured.

**W1.3 ⚠️ Do not make it a required check without updating both records.** Required check
names live in the branch ruleset, outside this repository, with
`.github/required-checks.txt` and the `settings-drift` job as the two halves that keep them
honest. A new job is *not* required until the ruleset says so — and `doc-claims`'s `ci-jobs`
check will fail until the new name appears in `docs/ci.md`, which is intended.

**W1.4 State what this does NOT cover.** `publish-chart.yml` tests the *published* artefact;
this tests the *locally built* one. Both are worth having and neither replaces the other —
that distinction is already written down in `prompt-chart-distribution.md` (W5 vs W3.4) and
should not be re-litigated, only referenced.

## W2 — Reject every unknown argument, not just the second

**W2.1** `install.sh` must reject a third argument, and any argument it does not recognise,
with the same loud failure it already gives for a bad `$2`. Rule 10 states the invariant; the
code implements it for one position only.

**W2.2 ⚠️ The realistic typo is `--lite`, not `--bogus`.** `LITE` is an environment variable,
not a flag, and someone who has read the README could easily reach for
`./scripts/install.sh local --lite`. Today that installs the full stack and says nothing. Use
it as the test case.

**W2.3 Fix `teardown.sh` the same way, and delete the comment that will become false.**
It has the identical hole (`scripts/teardown.sh:11`, `DESTROY="${2:-}"` compared against the
literal). `scripts/install.sh:25-28` currently reads *"UNLIKE teardown.sh, an unrecognised
value is rejected"* and explains teardown's laxity as the contrast — the moment teardown is
fixed, that paragraph describes a state that no longer exists, which is the drift class rule
12 names. The fix is cheap and identical; there is no interesting decision here, only the
comment to retire.

⚠️ `teardown.sh --destroy` deletes clusters. A typo'd flag silently doing the *non*-flag
thing is the safe direction there, which is presumably why it was never fixed — but the
reverse, a typo'd flag being ignored so the user thinks they destroyed something they did
not, is the one that costs money on a cloud target.

## W3 — Cut the time to diagnosis, by whatever means survives scrutiny

**The goal is the outcome, not the mechanism.** A BYO user with a wrong `RELEASE_LABEL`
currently waits ≥10 minutes to be told something the run could have suspected in seconds.
Fix *that*. Early exit is the obvious candidate and it is **not** a foregone conclusion —
see W3.2.

**W3.1 A wrong label is not a race.** Rule 5 exists because checks that race their producer
must poll. The objects carry the label they carry, and waiting does not change it. That is
the asymmetry worth exploiting.

**W3.2 ⚠️ THE OBVIOUS FIX MAY NOT WORK, AND THIS IS THE MAIN RISK IN W3.** *Adoption is
itself asynchronous.* Observed 2026-08-04: after relabelling the objects, the targets took
time to appear, because the operator regenerates Prometheus config and Prometheus reloads.
So a pre-flight that waits for adoption is doing **the same job as the checks it would
short-circuit, only sooner** — and if it concludes too early it turns a slow-but-healthy
cluster into a hard failure, which is strictly worse than today.

Before building a pre-flight, establish whether "adopted" can be distinguished from "not yet
adopted" by anything other than waiting. Candidates, in increasing order of confidence:

- the ServiceMonitors and PrometheusRules **exist and carry** `release=$RELEASE_LABEL`, and
  the Prometheus CR's selector **does not match that value** — a pure object comparison,
  decidable with no waiting at all, and the actual root cause;
- Prometheus reports zero rule groups **and** zero targets from this install after a short
  budget of its own.

The first is deterministic and is probably the whole answer. If it is, W3 is a comparison of
two label selectors and not a polling problem at all.

**W3.3 A better message may be the honest fix, and is an acceptable outcome.** If the
distinction cannot be made safely, then surfacing `byo_hint()` at the *first* failure rather
than the last — "this has now failed for N seconds; if `RELEASE_LABEL` is wrong, that is
why" — costs nothing, risks nothing, and still cuts time-to-diagnosis for the human reading
the output. Landing that and recording why early exit was rejected is a complete W3, not a
failed one.

**W3.4 ⚠️ Do not reduce the poll budgets.** Each was derived from a measured failure and rule
5 was written *"after the fourth time it bit"*. Whatever is built must leave them untouched
for the case they exist for.

**W3.5 Prove both directions.** A correctly configured but slow cluster must still pass.
Whatever is built, demonstrate it does not fire on a healthy install — not only that it fires
on a broken one. This is the criterion the work can most easily fail.

**W3.6 Only `--byo` mode, unless there is a reason otherwise.** The repo's own installs
cannot have a label mismatch, because `install.sh` applies the label it later checks.

## W4 — Fix the fail-open assertion, and sweep the class

**W4.1** `.github/workflows/ci.yml:834` must report a `source=` label when one is present.
A single `grep`/`awk`, or materialising to a file, removes the pipe entirely.

**W4.2 ⚠️ Sweep the class rather than the line.** `pipefail` plus a consumer that exits early
(`head`, `grep -q`, `grep -m1`) is a pattern, and it has now cost this repo twice: a failed
chart publish on a correct archive, and this. Search the shell in `scripts/`,
`.github/workflows/` and the Taskfiles. Judge each hit — many are safe because the producer's
output fits the pipe buffer — and say which were judged safe and why, rather than silently
fixing some and leaving others.

**W4.3 Consider whether this is checkable.** A `grep -q` after a pipe is mechanically
detectable. Whether it belongs in a lint step or a comment is a judgement call; make it
deliberately rather than by omission.

---

## Effort

Estimates from reading the code — **treat the ordering as firmer than the numbers**, and
re-derive the largest line before committing to it. Instruments are priced as code plus
25–35% verification.

| Item | Estimate | Independent? |
|--|--|--|
| W4 fix + class sweep | ~1 hour | yes |
| W2 `install.sh` + `teardown.sh` | ~1 hour | yes |
| W3 label-selector comparison, or the message fallback | ~half a day | yes |
| W1 factor the chart-verify job and call it from CI | **~1 day** | yes |

⚠️ **W1 is the line to re-derive**, and the estimate assumes **factoring, not writing**. If
it becomes a new job written from scratch it is smaller *today* and permanently more
expensive, which is the trade W1.2 refuses.

**The CI cost is already measured, so do not guess it.** The equivalent job in
`publish-chart.yml` took **3m39s** (run `30898366900`, 2026-08-04), against the longest
existing leg `full stack on kind (full)` at ~6-7 min. Jobs run in parallel, so the expected
effect is **~0 wall-clock and ~4 runner-minutes per run**. If the implementation lands
materially above that, something is being done differently and it is worth knowing what.

## Non-goals

- **Reducing any poll budget in `verify.sh`.** See W3.4.
- **Making the new chart job a required check** in the same change that creates it. Land it,
  watch it, then require it.
- **Withdrawing chart `0.2.0`.** Registry versions are immutable; it is superseded and
  recorded.
- **Re-testing the published chart per pull request.** That is `publish-chart.yml`'s job and
  it needs a published artefact to exist.
- **Fixing `teardown.sh` beyond argument handling.**
- **A second copy of the chart-verification steps.** If factoring them proves genuinely
  impractical, that is a finding to record and discuss, not a licence to duplicate quietly.
  See W1.1.
- **Early exit in `verify.sh` at any cost.** If a wrong label cannot be told from a slow
  cluster safely, the message-only fix is the right answer, not a forced one. See W3.3.

## Acceptance criteria

1. A pull request that breaks the chart's `helm test` fails **on that pull request**, not at
   the next release. Demonstrated by breaking it deliberately and showing the red run.
2. The chart-verification steps exist **once**, called by both the pull-request path and
   `publish-chart.yml`. If they exist twice, the reason is written down and is not "it was
   easier".
3. Both cases are asserted: `helm test` **fails** on a mismatched `releaseLabel` with a
   message naming `releaseLabel`, and passes when it is set.
4. `./scripts/install.sh local --skip-monitoring --lite` exits non-zero, names the offending
   argument, and creates nothing.
5. `teardown.sh` rejects an unrecognised argument the same way, and
   `scripts/install.sh:25-28` no longer claims a contrast that has stopped existing.
6. **`verify.sh --byo` against a wrong `RELEASE_LABEL` reports the label diagnosis in under
   `<T>`, where `<T>` is written down BEFORE the work starts** (rule 6) and derived from
   what a healthy install actually takes to be adopted — not chosen after seeing a result.
   If W3 lands as the message-only fallback, the criterion is instead that the diagnosis
   appears at the *first* failing check rather than the last, and the poll budgets are
   unchanged either way.
7. `verify.sh --byo` against a *correct* cluster still passes, demonstrated on one that is
   slow to adopt. Whatever W3 builds must be shown not to fire on a healthy install.
8. `ci.yml`'s `source=` assertion fails when a `source` label is present. Demonstrated by
   adding one and watching it go red.
9. Every `pipefail`-plus-early-exit-consumer hit is fixed or judged safe, and **each
   judgement is recorded in the commit body, naming the file and line and why the producer's
   output cannot exceed the pipe buffer**. A sweep whose conclusions live only in someone's
   head is not a sweep.
10. `task preflight` passes, and `doc-claims`'s `ci-jobs` check is satisfied — any new job
    name appears in `docs/ci.md`.
11. `CHANGELOG.md` under `[Unreleased]`, saying *why* in each case.

## Process

One logical change per commit (rule 11). **W4 → W2 → W3 → W1**, cheapest and most
independent first, so the expensive item is the only one left carrying risk.

⚠️ **W1 last, deliberately.** It is the item most likely to overrun, and the other three are
worth landing regardless of how it goes. Landing them first means an overrun costs a
follow-up rather than blocking three fixes that were ready.
