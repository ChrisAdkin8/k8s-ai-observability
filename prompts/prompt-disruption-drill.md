# Prompt: What an eviction does to an in-flight generation

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Four
pieces of work:

1. **W1** — a minimal streaming responder, so there is such a thing as an in-flight
   generation to interrupt.
2. **W2** — a harness that opens N streams through a Service, **keeps offering new
   connections through the disruption window**, disrupts one pod, and classifies **four**
   outcomes.
3. **W3** — the matrix: trigger × grace period × PDB × `preStop`.
4. **W4** — the finding, as a scenario script you invoke — **not** a `verify.sh` check.

**Why.** Kubernetes' disruption defaults were tuned for HTTP requests measured in
milliseconds. An LLM generation is measured in tens of seconds. `terminationGracePeriodSeconds`
defaults to **30**. Every deploy, node upgrade, consolidation event and spot reclamation
therefore has a window in which it truncates output mid-generation — and no dashboard shows
it, because the metric that would is on the pod that just died.

**The deliverable is the mechanics**: which disruption paths honour which policies, what a
`PodDisruptionBudget` does to a request already in flight, and how a client experiences each
failure. That is unmeasured, and it fits in a day and a half.

## ⚠️ What this work cannot show, and must not claim

The result this rig most wants is **load dependence** — that at 1.8 rps a request finishes in
~5.8s and a disruption is harmless, while at 6.0 rps the queue pins at 160, the same request
takes ~64s, and the same disruption truncates every stream. Same pod, same config, same
command, opposite outcome.

**That finding is out of reach of this instrument, and the reason is structural.** Load
dependence is an emergent property of the *queueing model* — capacity, arrival rate, Little's
Law — which lives in `scripts/llm-sim.py`. `stream-sim.py` (W1) has no queue: its timings are
parameters. Configuring a 58-second delay and observing that it exceeds a 30-second grace
period is arithmetic, not a measurement.

So this is **the first of two findings**:

| | Finding | Instrument | Status |
|--|--|--|--|
| **1** | The mechanics — outcome taxonomy, PDB behaviour, `preStop`, endpoint race | `stream-sim.py` | **this prompt** |
| **2** | Load dependence — the same config safe at one rate and unsafe at another | `llm-sim.py` + ingest path | after `prompt-keda-testbed.md` W1 |

⚠️ **Finding 2 now has an owner.** `prompt-keda-testbed.md` W1.3 explicitly includes
`stream: true` on the ingest path, with the SSE mechanics **ported from this file's
responder** — per-event flush, `SIGTERM` drain, `[DONE]` terminator. This responder is the
donor, not the vehicle. (An earlier revision of the two files each deferred streaming to the
other, which orphaned finding 2 entirely; if you touch either file, keep the ownership
statement in both.) **Do not write the load-dependence claim into the finding produced
here** — W2.1's structural rule is what makes the sequel a selector change rather than a
rewrite.

### ⚠️ Correction to a claim that motivated this work

It was previously asserted that consolidation, rolling update, spot interruption and node
upgrade "all route through the same eviction path". **That is wrong, and the error is
load-bearing** — it is the difference between a PDB applying and not.

| Trigger | Path | PDB consulted? | Grace period honoured? |
|--|--|--|--|
| Eviction API — `kubectl drain`, node upgrade, Karpenter consolidation, descheduler | **Eviction subresource** | **yes** | yes |
| Rolling update | ReplicaSet deletes pods **directly** | **no** — `maxUnavailable` governs instead | yes |
| Spot / preemption with a handler | handler evicts → Eviction API | yes, if the handler gets there | yes, within the reclaim deadline |
| Node failure | pod vanishes with the node | no | **no** |

**The grace period is common to all but the last. The PDB is not.** W3 therefore measures an
eviction *and* a deletion, or a conclusion about PDBs gets drawn from one path and stated as
if it held for both.

### ⚠️ Evict a pod. Do not drain a node.

An earlier draft said `kubectl drain`. On this rig that is wrong and expensive:
`kind/gpu-sim.yaml` declares a **single** `control-plane` node and no workers, so draining it
evicts Prometheus, Grafana, the fake-GPU operator and both tenants — destroying the
observability you are measuring against and leaving a cordoned cluster that every later check
trips over.

**The Eviction API is a subresource on the pod.** POST an `Eviction` for one pod and you get
the full PDB-admission path with no cordon and no cleanup trap. It is the same code path a
drain uses; a drain is a loop over it. `kubectl delete pod` covers the rollout column. Neither
touches the node.

⚠️ **There is no `kubectl evict` — name the actual command, or the implementer falls back to
the one command everyone knows, which is the forbidden one.** The working invocation is:

```bash
kubectl create --raw "/api/v1/namespaces/${NS}/pods/${POD}/eviction" -f - <<EOF
{"apiVersion": "policy/v1", "kind": "Eviction",
 "metadata": {"name": "${POD}", "namespace": "${NS}"}}
EOF
```

A PDB that refuses the eviction surfaces as an HTTP **429** on this call — which is itself a
matrix result (see W3), not an error in the drill.

### Effort

| | Estimate |
|--|--|
| W1 responder — server, SSE, `SIGTERM`, `--selftest`, manifests | ~4–5 hours |
| W2 harness + outcome classifier + background connect loop | ~3 hours |
| W3 matrix | ~2 hours |
| W4 scenario script + write-up | ~2 hours |

**~1.5 days, depending on nothing.** ⚠️ Estimates derived from reading the code rather than
from doing the work. **Treat the ordering as firmer than the numbers** — and treat W1's number
as the softest of the four, because it is the largest and it has already been re-priced twice,
both times upward. Re-derive it before planning around it.

⚠️ The reason it moved is worth stating because it will apply to whoever picks this up, and
**W1 is an instrument, not a feature.** An HTTP server that streams tokens is an hour. One
whose output you would publish a number from needs verified per-event flushing (the failure is
silent), a `SIGTERM` path that distinguishes *drained* from *killed*, and a timing selftest
whose tolerance is tight enough to catch a regression and loose enough not to flake on a busy
runner. That last item is the most likely thing to overrun.

## ⚠️ On adding a second simulator to a repo that refuses second copies

`scripts/stream-sim.py` is **not** a second LLM simulator and must not grow into one. It
models nothing about LLM serving except **response timing** — a delay, then tokens at an
interval. It emits no `vllm:` metrics, no histograms, no phase decomposition, and it is not
scraped. Its timings are inputs, not a model.

**Retirement is structural, not aspirational** — see W2.1. The harness targets a Service by
name and never an implementation, so replacing this responder with the real simulator is a
change to that Service's selector. A header comment promising future deletion would not
survive first contact with five consumers; a selector will.

## Background / Facts

Read directly in the file cited, on 2026-08-03. Where one is wrong anyway, correct it in your
commit message.

⚠️ **Read `CLAUDE.md` first — it carries the repo's standing law and this file does not
repeat it.** Only facts specific to *this task* are below, with governing rules cited by
number rather than restated.

### ⚠️ Nothing in this repo sets a disruption policy — VERIFIED (grep over `manifests/`, `charts/`)

No `PodDisruptionBudget`, no `preStop`, no `terminationGracePeriodSeconds`, no
`maxUnavailable` anywhere. **Every pod runs on the Kubernetes default of 30 seconds.** The
baseline reading — default everything, no policy objects — is therefore available the moment
**W1 and W2** land: the baseline *is* a classified outcome table, and W2 is the classifier.
Take it before any W3 policy exists and record it — it is the control for every later row.

### ⚠️ Where the timing parameters come from — VERIFIED (`manifests/llm/10-profiles.yaml`)

```
itl_full   = 0.015 x 1.5   = 0.0225 s/token
generation = 256 x 0.0225  = 5.76 s
steady     TTFT ~0.08 s  -> total ~5.8 s
saturated  TTFT ~58 s    -> total ~64 s
```

Use these as the responder's **defaults**, so the drill reproduces the rig's own figures rather
than inventing new ones, and cite the file in the responder's header. ⚠️ **They are parameters
here, not results** — the 58s is derived from a queue this responder does not have. See *What
this work cannot show*.

### ⚠️ The real simulator serves no requests — VERIFIED (`manifests/llm/20-simulators.yaml:183`, `scripts/llm-sim.py:229`)

One Service port, `llm-metrics` 9401. Load comes from `arrival_rate_rps` in the profile
ConfigMap, not from clients. This is why W1 exists.

## W1 — The streaming responder

**W1.1** `scripts/stream-sim.py`, standard-library only (`CLAUDE.md` rule 14). `POST /v1/completions`
with `stream: true` responds `text/event-stream`: first event after `ttft_seconds`, then one
event per token every `itl_seconds`, terminated by `data: [DONE]`. Parameters from environment
variables, defaulting to the steady figures above.

**W1.2** ⚠️ **The stream must survive its own headers.** A generation that emitted a first token
and was then cut off is the failure under measurement. An implementation that buffers and
writes once at the end makes every disruption look like a connection refusal, and the
interesting case never appears.

**W1.3** ⚠️ **Flush per event.** `wfile` buffers. Without an explicit flush, tokens arrive in
blocks and the measured truncation point is an artefact of the buffer size rather than of the
disruption. Assert this in the selftest — the failure is silent.

**W1.4** Handle `SIGTERM`: stop accepting new connections, keep serving open streams, exit when
the last closes. **Log which way the process ended** — drained cleanly, or killed with streams
open. The harness cannot see that from outside, and it is the distinction between outcomes B
and C.

**W1.5** A `--selftest` mode, as `llm-sim.py` has: first event at ≥ `ttft_seconds`, N events,
`[DONE]` last, per-event flushing observable. ⚠️ Pick the timing tolerance deliberately and say
why in a comment — too tight and it flakes on a loaded CI runner, too loose and it stops
catching the regression it exists for.

**W1.6 Manifests live in `manifests/disruption/`** — a new sibling of `alerts/`, `dashboards/`,
`llm/` and `workloads/`, organised by concern like the rest. Nothing in `install.sh` applies it,
so it is opt-in by construction, the same way `manifests/llm/extras/` is.

⚠️ **`stream-sim` carries its own labels — `app.kubernetes.io/name: stream-sim`, never
`llm-sim`.** `install.sh` discovers LLM tenants by label and waits on them; a responder wearing
`llm-sim`'s labels gets swept into that discovery and into the shared Service's selector.
Distinct name, distinct component, its own `stream-target` Service.

⚠️ **Replica count is a matrix variable, not a constant** — `minAvailable: 1` behaves completely
differently at one replica than at two, and both are rows in W3.

## W2 — The harness

**W2.1 ⚠️ Target the Service `stream-target` by name. Never an implementation, never a pod IP
by default.** Two reasons, and both are structural:

- kube-proxy's endpoint removal is asynchronous with `SIGTERM`, which is the entire cause of
  outcome D. A harness that dials pod IPs cannot produce D, and the `preStop` row then measures
  a mitigation for a failure the test cannot generate.
- **This is what makes retirement a two-line change.** When the real ingest path lands
  (`prompt-keda-testbed.md` W1.2–W1.3, which includes streaming), point `stream-target`'s
  selector at the driven simulator's pods and the harness is unchanged.

Pod-IP mode is worth having as a *second* mode — the difference between the two paths **is** the
endpoint race — but it must be labelled as excluding outcome D.

**W2.2 Four outcomes, not two.**

| | Outcome | Meaning |
|--|--|--|
| **A** | completed before the disruption was issued | not under test — the disruption missed it |
| **B** | completed after the disruption, within grace | **graceful drain worked** |
| **C** | **truncated mid-stream** | **the failure — partial output already delivered to a client** |
| **D** | refused / reset at connect | endpoint-removal race — a *different* bug, with a different fix |

⚠️ **A and B are separated by the timestamp at which the harness issued the eviction or
deletion — not by `SIGTERM`,** which the client cannot observe. Admission, deletion and signal
delivery each add latency, so the boundary carries a small known skew. **Record the issue
timestamp and say in the write-up that the boundary is approximate**; the alternative is
someone correlating against pod logs and discovering the clocks disagree.

C and D have different causes and different remedies. Collapsing them into "failed" is how
someone lengthens the grace period, sees no improvement, and concludes grace periods don't
matter — when the residue was D all along.

**W2.3 ⚠️ Confirm the streams are mid-generation before triggering.** Wait until every client has
received at least one token and none has finished. Firing on a timer measures whatever the rig
happened to be doing.

**W2.4 ⚠️ Keep offering new connections through the disruption window — without this, outcome D
cannot occur at all.** Every pre-opened stream predates the termination, and D is a failure of
**new** connections racing endpoint removal. A harness that only watches its N pre-opened
streams has defined D and then built a procedure that excludes it — and the `preStop` row of
the matrix silently measures nothing. Run a low-rate background connect loop (one attempt per
second is plenty) from before the trigger until the pod is gone, and classify each attempt: a
completed short stream is a non-event, a refusal or reset is D. **A run in which no D-eligible
attempt was made is recorded as such**, not as "no D observed".

**W2.5** Record per stream and per connect attempt: tokens received, time to first token, time
to last, outcome class, and the disruption-issue timestamp. JSON lines. The per-stream detail
distinguishes "all streams died together" from "streams died as pods were killed in sequence".

## W3 — The matrix

Vary one axis at a time against the baseline:

- **Trigger** — the `Eviction` subresource (see the command above) **and** `kubectl delete
  pod`. Both, per the correction table.
- **Grace period** — default 30s, then a value above the configured request duration.
- **PDB** — absent; `minAvailable: 1` at 2 replicas; `minAvailable: 1` at 1 replica.
- **`preStop`** — absent, then a sleep sized to cover endpoint propagation.

Four traps, each otherwise discovered as a confusing result:

⚠️ **`preStop` runs inside the grace period, not before it.** `preStop: sleep 30` with
`terminationGracePeriodSeconds: 30` leaves the application **zero** seconds. The grace period
must exceed the sleep plus the longest request.

⚠️ **A PDB gates eviction *admission*, not request *completion*.** It answers "may this pod be
disrupted right now", and once the answer is yes the in-flight generation is subject to the
grace period like any other. **Measure what it does; do not predict it here.** If the result
surprises you, check the harness because every three-hour-old instrument deserves one pass of
suspicion — not because the answer was supposed to come out a particular way.

⚠️ **`minAvailable: 1` at a single replica refuses eviction indefinitely** (HTTP 429 on the
eviction call). Not a bug in the test — a configuration people ship. Its own row: the eviction
neither succeeds nor truncates, and a drain built on it retries until it hangs.

⚠️ **Endpoint removal is asynchronous with `SIGTERM`.** A terminating pod can still receive new
connections — outcome D, and the reason `preStop` sleeps exist. W2.4's connect loop is what
makes D observable; without it the `preStop` row is uninterpretable.

## W4 — The finding

**W4.1 A scenario script, `scripts/disruption-drill.sh`**, invoked deliberately — the same shape
as `drive-llm-load.sh`. It applies a matrix row, runs the harness, prints the outcome table.

⚠️ **Do not add a `verify.sh` check asserting that a disruption truncates generations.**
`CLAUDE.md` rule 7 draws the line; this work sits squarely on the wrong side of it. Asserted
as an invariant, the defect fails the day someone improves the defaults — failing by being
right — and it would run a disruption on every CI leg. The only thing CI should assert here
is `stream-sim.py --selftest`.

**W4.2** Document the result where a reader meets it before configuring a deployment, and state
the structural tension rather than recommending a number: **a grace period long enough to
protect a long generation is long enough to make every rollout slow, and may exceed a
spot-reclamation deadline.** No single setting is correct at every request duration. That is the
finding — not "set it to 600".

**W4.3 Honest weight.** The mechanism is textbook: `SIGTERM` → grace period → `SIGKILL` is in the
pod lifecycle docs, and PDB semantics are documented. What is new is the **measurement** — the
four-outcome taxonomy, a quantified answer to what a PDB does to an in-flight request, and the
`preStop`-inside-grace interaction. Expect "well, obviously" from part of the audience. ⚠️ **Do
not reach for the load-dependence framing to make it land harder** — that claim belongs to
finding 2 and is not supported by this instrument.

## Non-goals

- **Draining a node.** See the correction above. Evict pods.
- **Adding a worker node to `kind/gpu-sim.yaml`.** Recovery and rescheduling are a different
  measurement, and the node-label topology guard is not worth disturbing for them.
- **Any autoscaler.** No KEDA, no kwok, no Karpenter. The two triggers cover the eviction path;
  an autoscaler inserts a scheduler between you and the thing being measured.
- **A queueing model in `stream-sim.py`.** That is the line between a timing fixture and a second
  simulator, and crossing it is how this becomes a fork.
- **Wiring the harness to `llm-sim.py`.** Deferred until the ingest path exists — owned by
  `prompt-keda-testbed.md` W1.3, including its streaming mode — and made cheap by W2.1 rather
  than by intention.
- **Changing any chart or manifest default.** Publish the measurement first. A default chosen
  before the matrix exists is a guess with a version number.

## Acceptance criteria

1. `stream-sim.py` states at the top that it models timing only, and that it is replaced by
   repointing the `stream-target` Service when the real ingest path lands.
2. The baseline reading — default 30s, no PDB, no `preStop` — is recorded **after W2 exists and
   before any W3 policy is applied**.
3. Disruption is by pod eviction (via the documented `kubectl create --raw` invocation) and pod
   deletion. Nothing in this work drains or cordons a node.
4. The harness resolves `stream-target` by name; pod-IP mode exists as a second mode, labelled as
   excluding outcome D.
5. Four outcomes are classified, C and D are never merged, and the A/B boundary is the recorded
   disruption-issue timestamp with its approximation stated.
6. The harness confirms streams are mid-generation before triggering, **and** runs the W2.4
   connect loop through the disruption window. A run with no D-eligible attempts is recorded as
   such.
7. Both triggers are measured, and any PDB conclusion is stated only for the path that consults
   PDBs.
8. `minAvailable: 1` at one replica is its own recorded row, including that eviction is refused
   with 429.
9. `stream-sim` manifests live in `manifests/disruption/`, carry their own labels, and are
   applied by nothing in `install.sh`.
10. No `verify.sh` check asserts the defect. CI runs `stream-sim.py --selftest` and nothing else
    new.
11. **The write-up claims the mechanics only.** No load-dependence claim appears anywhere in it.

## Process

**W1 → W2 → baseline reading → W3 → W4.**

**Take the baseline as soon as the classifier exists and before any policy is applied.** It is
the control for everything after, and it is the number most likely to be the headline.

⚠️ `CLAUDE.md`'s closing motto applies with unusual force here: the instrument will be three
hours old when it produces its first number.
