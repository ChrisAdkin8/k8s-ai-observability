# Prompt: Fault injection — fire the alert set against real broken states

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo,
implementing **ROADMAP.md item 1**. Twelve pieces of work — one configuration surface, the
roadmap's seven modes, the harness they share, and **three faults in the observability path
that the roadmap's list does not contain** (finding 6):

0. **W0** — **one JSON config surface that works unchanged on Kubernetes and Docker.** Do it
   first; everything below writes through it.

1. **W1** — the drill harness: one script, expectation-first, polled, self-restoring.
2. **W2** — target loss, and the finding that `absent()` is a **global** operator.
3. **W3** — **stale but up**: a freeze knob in the simulator, the drill, and the detector
   that catches what absence cannot.
4. **W4** — partial outage, used to grade the LLM dashboard rather than the alerts.
5. **W5** — counter reset across a pod restart, with the prediction written first.
6. **W6** — KV cache exhaustion, isolated from saturation.
7. **W7** — request-failure surge, against the simulator's own account (the honest half).
8. **W8** — cardinality explosion, bounded, last, and never in CI.
9. **W9** — rules and ServiceMonitors that were **never adopted**: the repo's most
   documented failure, and self-concealing.
10. **W10** — the **metric surface moves** under the queries (the v0/v1 rename, injected).
11. **W11** — **two replicas of one `model_name`**, against an SLO caveat that is written
    down and unmeasured.

**Why.** Every alert in both rule files is asserted in `tests/rules/`, where promtool feeds
a hand-written series and checks the alert fires. That proves the PromQL. It does not prove
that a real failure produces the series shape those tests assume. This is `CLAUDE.md`
rule 18 aimed at the alert set rather than at the checks: an alert that has only ever fired
against a synthetic series is a guess about the world.

**The deliverable is a graded alert set** — for each failure mode, what fired, what stayed
silent, and whether the silence was correct. ⚠️ **ROADMAP.md says two of its seven modes
trip nothing. On the ten below the ratio inverts: only two are expected to fire anything at
all** against the tree as it stands — target loss on the GPU side (W2.2) and KV exhaustion
(W6) — and W3 earns a third by building the detector it needs. Everywhere else "there is no
alert for this" is the result, which is a legitimate one to hand out and which this rig can
hand out with evidence. Size the harness for that: silence is the common path here, not the
exception.

---

## What a spike already settled, on 2026-08-06

A half-day spike ran the cheap empirical version of every question in this file that did not
need a cluster (`ROADMAP.md`'s own warning: ask whether the cheap empirical version exists
before planning around an estimate). **The numbers below are measured, not modelled**, and
two of them changed the work. The scripts are **tracked at `spike/`** (`0d426e5`, and
`spike/README.md` records why they are on `main` rather than on a branch); rerun them rather
than trusting the transcription.

| Question | Answer | Where it lands |
|--|--|--|
| Can single-tenant loss fire `LLMMetricsAbsent`? | **No** — 15m of absence, no alert. Total loss fires at 5m. Both proven in one promtool run, so neither is vacuous | finding 1, W2 |
| Does the stale detector's `and` match across two metric names? | **Yes.** And `sum by (model_name)` on one side gives `got:[]` — green forever | W3.5 |
| Does the idle-tenant negative hold? | **Yes**, and dropping the `running > 0` guard makes the idle tenant fire | W3.6 |
| Is the thaw burst real? | **Yes: 10x a normal scrape interval.** The clock-offset fix reduces it to exactly zero | W3.2 |
| Can KV exhaustion be isolated at 2.4 rps? | **No.** p95 TTFT reads 4.29s there, over the 2s threshold | W6 |
| Does a new alert turn `task doc-claims` red? | **Yes**, at `docs/llm-simulation.md:43`, by name and line | finding 5 |
| Does a sourced EXIT trap really clobber the caller's? | **Yes, silently** — the caller's restore never runs at all | W1.6 |

## What a SECOND spike settled, on 2026-08-07

The first spike proved the freeze **design** on a scratch harness. This one built it inside
the shipped `worker()` and ran the whole mode end to end — four simulators and a real
Prometheus, no Kubernetes — because the design was the only part reading could reach.
Evidence: `spike/worker_freeze.py` and `spike/stale_e2e.sh`. ⚠️ **Both need W0 and W3 to
exist**; they ran green against a spike implementation that was deliberately not kept
(`docs/development-method.md` stage 3).

**It changed W3 substantially, and none of it was visible from the design.**

| Question | Answer | Where it lands |
|--|--|--|
| Does the clock-offset fix work inside the real `worker()`? | **Only after two more fixes.** The design is right and the wiring around it is not | W3.2 |
| What does the profile poll key off? | ⚠️ **The SIMULATED clock — so the freeze is permanent.** The poll is the only way a thaw arrives, and freezing the clock freezes the poll with it | W3.2 |
| What does the event sleep compare? | ⚠️ **A simulated `due` against `time.monotonic()`** — after a freeze that is permanently negative and the loop busy-spins. **70 passes/s against 2/s** | W3.2 |
| Is the thaw burst real inside `worker()`? | **Yes: 9.0x a normal scrape gap** naive, **0.9x** with the offset. Confirms the scratch harness at a different rate | W3.2 |
| Does `validate_profile` reject an unknown key? | ⚠️ **No.** `p.update(raw)` carries anything through, so `{"freze": true}` validates, applies, bumps the generation counter and injects **nothing** | W0.1 |
| How long from freeze to `LLMMetricsStale` firing? | ⚠️ **The rate() window AND the `for:`, in series.** Measured 56s + 30s = **87s** on a `[1m]`/`30s` twin. Shipped `[10m]`/`5m` means **~15 minutes** | W3.5, Background |
| Can the drill freeze `llm-driven` as it ships? | ⚠️ **Not reliably.** At 0.4 rps the population hits zero, `running > 0` excludes it, and the drill reports "no alert" about a state it never created. **One run held 6 requests, the next held 0** | W3.4 |
| Does the alert fire against a real frozen simulator? | **Yes** — driven tenant only, fixtures untouched, and the idle-tenant negative shown against **live data** (guardless 2 series, guarded 1) | W3.5, W3.6 |
| Does `extract.sh` need new logic for the driven profile? | **No** — one extra filename, exactly as W0.7 predicted | W0.7 |
| Is there a third `zsh` vs `bash` class? | ⚠️ **Yes, and nothing in the repo catches it.** See the Background item below | W1, rule 17 |

---

## ⚠️ Six things the roadmap says about this item that are wrong or incomplete

These were found by reading the tree on **2026-08-06**, after ROADMAP.md was written.
Correct ROADMAP.md in the commit that acts on each one (`CLAUDE.md` rule 12), and
**do not introduce em dashes there** — rule 13 covers that file.

### 1. `kubectl delete pod` cannot fire `LLMMetricsAbsent`. Not "trivially", not at all.

The roadmap's table says target loss is producible "**Yes, trivially.** `kubectl delete
pod`". Two independent reasons it is not:

- `absent(vllm:num_requests_running)` (`manifests/alerts/llm-prometheusrule.yaml:332`) is a
  **global** operator. It returns a series only when the vector is empty **cluster-wide**.
  With `llm-steady` and `llm-saturated` both running, no single tenant's disappearance can
  make it true. And the fixtures may not be scaled to arrange it (rule 1).
- The alert carries `for: 5m`. A pod deleted from a Deployment is back in seconds, so even a
  total outage arranged by deletion never accrues the duration.

**The GPU side is the opposite case, and the asymmetry is the finding.** There is one
DCGM exporter, so `absent(DCGM_FI_DEV_GPU_UTIL)` (`gpu-prometheusrule.yaml:94`) genuinely
does go true when it is scaled to zero. Same expression shape, opposite outcome, and the
difference is only how many things emit the series.

### 2. Two instructions in the tree tell you to test it in a way that cannot work.

Both say to scale `llm-steady` to zero:

- `manifests/alerts/llm-prometheusrule.yaml:330`
- `docs/llm-simulation.md:409`

Neither can fire the alert, for reason 1 above, and following either also violates rule 1.
**Correcting both is part of W2**, and `docs/architecture.md:128` lists the alert set, so
check it too.

### 3. Two LLM alerts fire permanently on this rig, so "the alert is silent" is never a
valid assertion.

`LLMHighTTFT` and `LLMQueueBacklog` are kept firing by `llm-saturated` by design
(`llm-prometheusrule.yaml:283`, `:302`). `GPUHighMemoryUsage` is always firing by
construction (`gpu-prometheusrule.yaml:62`). **Every expectation in every drill is scoped
by `model_name`**, and an unscoped `ALERTS{alertname="LLMHighTTFT"}` check would pass
whatever the drill did. This is the same failure `verify.sh` guards against by selecting
alerts by exact name rather than by wildcard (`llm-prometheusrule.yaml:3`).

### 4. Stale-but-up is not producible against the tree as it stands.

The roadmap says five of seven modes need nothing but `kubectl` and the profile ConfigMap,
and lists stale-but-up among the producible ones. It is not, and the reason is structural:
the worker advances the simulation and the handler serves `/metrics`
(`scripts/llm-sim.py:843`, `:878`), both in one process. Anything that stops the worker
from outside stops the server too, which produces target loss, not staleness. **W3 needs a
simulator change**, priced accordingly below.

### 5. A new alert is not one file's worth of work.

Adding one touches seven places, and the last one fails `task preflight`:

| Place | Why |
|--|--|
| `manifests/alerts/llm-prometheusrule.yaml` | the rule |
| `tests/rules/llm-rules_test.yaml` | promtool cases, both sides (rule 18) |
| `docs/llm-simulation.md:404-411` | the alert table has a row per alert |
| `docs/architecture.md:128` | enumerates the alert names |
| `manifests/dashboards/llm-sim-overview.grafana-com.md:241-244` | a **second** alert table, on the page that ships to grafana.com. It is in `EM_DASH_FREE` (rule 13) and republished as a revision of id 25620 (rule 9), which makes it the most expensive row here |
| `CHANGELOG.md` | its own table names a new **alert** as MINOR-worthy (`:12`) |
| **`docs/llm-simulation.md:43`** | **the alert count, derived and checked** |

`derive_llm_alerts()` (`scripts/check-doc-claims.py:140-143`, registered at `:423-425`)
counts `- alert:` entries in the LLM rule file and compares against the prose number.
**Measured in the spike** by appending a seventh alert:

```
  docs/llm-simulation.md:43: claims 6; derived 7 (alerts in the LLM rule file)
check-doc-claims: FAIL [alerts]: prose drifted from code
```

That is the check working, and it names the file and line for you. Budget for it rather
than being surprised by it.

⚠️ **A tracked prompt is inside that same scan, and this file was in it.** `check-doc-claims.py`
walks `git ls-files '*.md'` (`:506`), so `prompts/` has been in scope since the briefs were
tracked. This file restated the alert count twice and was **two of the three claims** the
check was comparing — so the seventh alert would have turned `doc-claims` red here as well
as in the doc that owns the number. The phrase is gone from it now, on the rule the working
loop already states: reference the file that owns a number, never restate it. **Do not put
it back**, in this file or in the next prompt.

The chart ships the rules too (`charts/k8s-ai-observability/templates/prometheusrules.yaml`
templates only the wrapper and `Files.Get`s the extracted groups), so a new alert is
published on the next chart release. That is fine and intended — just know it is a
published surface, not a local experiment.

### 6. All seven modes are WORKLOAD faults. The observability path is unexercised.

Every row in the roadmap's table breaks the thing being watched. Not one breaks **the
watching** — and this rig's product is verification, so that is the stranger omission. Three
faults belong on the list and are not on it, each testing a claim the repo **states in prose
and has never exercised**:

| Fault | The prose it tests |
|--|--|
| Rules or ServiceMonitors never adopted | "THE SILENT ONE" in the chart template, `verify.sh`'s whole BYO preflight, and `RELEASE_LABEL` in `config.sh` |
| The metric surface moves (v0/v1 rename) | `METRIC_SURFACES` in `llm-sim.py`, the `v1` pin in `config.sh:234`, and `--vllm-surface both` described as existing "for upgrade testing" |
| Two replicas of one `model_name` | the SLO's exactness condition, `llm-prometheusrule.yaml:230-234` and the catalog page, both of which say the cancellation stops holding across replicas |

They are W9, W10 and W11, roughly an hour each because the machinery already exists.
**Add them to ROADMAP.md's mode table** in the commit that lands them: the page presents
seven modes as this item's definition, and that is the thing being corrected.

The tier below them — Prometheus's own health (rule-evaluation failure, WAL replay, scrape
timeout), alert *delivery* through Alertmanager, and pod OOMKill — is real and deliberately
**not** here. See Non-goals.

---

## Background / Facts

Read directly in the file cited, on **2026-08-06**. Where one is wrong anyway, correct it
in your commit message.

⚠️ **Read `CLAUDE.md` first.** It carries the standing law and this file does not repeat
it. Governing rules are cited by number below.

### The tenant this work targets — VERIFIED (`manifests/llm/extras/llm-driven.yaml`)

`llm-driven`, model_name `sim-llama-3-8b-driven` (`scripts/config.sh:243`), one replica,
profile in ConfigMap `llm-profile-driven`, applied by nothing in `install.sh` because
`kubectl apply -f manifests/llm/` is non-recursive. It exists so nothing dynamic ever
touches the fixtures (rule 1). **Every drill targets it, and the GPU exporter is the single
exception (W2).**

### The profile is hot-reloaded, and that is the whole delivery mechanism — VERIFIED (`llm-sim.py:843-871`)

The worker polls the profile file every 10s and calls `apply_profile()`
(`llm-sim.py:509`), which swaps the profile **without disturbing in-flight requests or
counters**. `llmsim_profile_generation` increments so you can confirm a change landed.
Kubernetes takes up to ~60s to propagate a ConfigMap into a running pod; `drive-llm-load.sh`
documents that at `:13-18`. Two consequences for drills:

- Changing a profile is not a restart, so no counter reset is involved — except in W5,
  where a reset is the point and a restart is therefore required.
- **A drill that rewrites the ConfigMap must write the whole profile.** `set_rate()`
  (`drive-llm-load.sh:60`) replaces the entire object, and the comment at `:55-59` records
  what happened the first time a field was omitted: it reverted to the simulator default
  and a panel flatlined mid-demo.

### `kv_cache_tokens_capacity` moves the gauge and nothing else — VERIFIED (`llm-sim.py:236`, `:283`, `:691-693`)

Three occurrences: the default, the positive-number validation, and

```python
def kv_cache_usage(self):
    active = sum(r.prompt_tokens + r.gen_tokens for r in self.running)
    return min(1.0, active / self.profile["kv_cache_tokens_capacity"])
```

It appears in no latency expression, so lowering it isolates `LLMKVCacheSaturated` from
`LLMHighTTFT` and `LLMQueueBacklog` **by construction** rather than by tuning. That is what
makes W6 cheap.

⚠️ **And it is why W6 must not claim to model KV pressure.** `llm-sim.py:731-740` records
that `vllm:num_preemptions_total` is deliberately not emitted, because `_admit()` gates on
`max_concurrency` alone and nothing here creates KV pressure. Lowering the capacity moves a
**gauge**; it does not make the engine behave differently. The drill grades the alert that
watches that gauge, which is a real result. Saying the simulator "ran out of KV cache" would
not be. **Do not emit `num_preemptions_total` as part of this work.**

### `min(1.0, ...)` clamps, and the drill has to live with it — VERIFIED (`llm-sim.py:693`)

Any setting that holds above 0.9 for a continuous 5m against a fluctuating request
population sits far enough above the threshold that the gauge reads a flat 1.0. See W6:
the crossing itself is not robustly observable, and pretending otherwise produces a flappy
drill.

### Prompt length is a second, more faithful KV lever — VERIFIED (`llm-sim.py:596`, `:656-658`)

`prefill = p["base_ttft_seconds"] * self._jitter()` is **flat**, and decode is
`gen_tokens * itl`. So `prompt_tokens.mean` affects `kv_cache_usage()` and the prefix-cache
counters and **no latency at all**. Raising it is the mechanism a real deployment fills its
KV cache with, and it is available here without disturbing the queue.

### Concurrency at a sub-capacity arrival rate — DERIVED, RE-DERIVE IT BEFORE USING IT

Capacity is 2.74 rps — **read it off `llmsim_capacity_rps` (`llm-sim.py:813`), not off the
base-latency comment at `drive-llm-load.sh:49-53`**, which is what rule 3 and the incident
in `10-profiles.yaml:34` are about. Below it, mean concurrency is
`arrival_rate x service_time`, and service time itself depends on load through
`CONGESTION_AT_FULL_LOAD` (`llm-sim.py:225`), so it needs one fixed-point iteration. At
1.8 rps it converges near **9 concurrent requests**, so mean active tokens are about
`9 x (512 + 256) = 6900`.

⚠️ **`llm-driven` ships at 0.4 rps, not 1.8** (`manifests/llm/extras/llm-driven.yaml:37`) —
it starts idle because a load driver walks it upwards. At 0.4 rps mean concurrency is under
two requests, the population is small enough that it hits zero regularly, and no fixed KV
capacity holds a gauge above a threshold for a continuous 5m against it. **So the KV drill
sets the arrival rate as well**, to something comfortably under 2.74, and its isolation
claim covers that rate rather than the shipped one.

⚠️ **A mean is not what the alert needs; it needs a 5m continuous hold**, and the gap
between the two is not a detail. **MEASURED in the spike** (`spike/kv_profile.py`) by
advancing the real `Simulator` offline, sampling `kv_cache_usage()` every 30s over ten
simulated minutes after a five-minute warmup, worst case over eight seeds:

| rps | kv capacity | prompt mean | usage mean | usage **min** | holds > 0.9 |
|--|--|--|--|--|--|
| 0.4 | 32768 | 512 | 0.042 | 0.000 | no — this is the shipped `llm-driven` profile |
| 1.8 | 6144 | 512 | 0.939 | **0.358** | no |
| 1.8 | 4096 | 512 | 0.987 | **0.538** | no |
| 1.2 | 4096 | 3072 | 0.974 | **0.000** | no |

A capacity chosen from the mean produces an alert that never fires. At 1.2 rps the request
population empties completely inside the window, so the gauge visits zero while its mean
reads 0.974.

⚠️ **And one seed is one sample.** The same profile (1.8 rps, kv 6144) gave a minimum
between **0.358 and 0.757** across eight seeds. Both fixtures ship `"seed": null`
(`10-profiles.yaml:74`, `:102`) and `llm-sim.py:445` leaves the RNG unseeded, so the live
tenant draws a fresh seed on every start. **Sweep seeds, take the worst, and leave margin.**

Everything above came from `llm-sim.py` running headless, with no cluster and no Prometheus
— its selftest already contains a `drive(rate, seed)` helper (`:1228`) for the same purpose.
Rerun the derivation rather than trusting this table, and write the numbers you get into the
drill's comment before you run it (rule 6).

### Alert timings you are paying for — VERIFIED (`llm-prometheusrule.yaml`)

| Alert | `for:` | Line |
|--|--|--|
| `LLMHighTTFT` | 2m | `:293` |
| `LLMQueueBacklog` | 5m | `:305` |
| `LLMKVCacheSaturated` | 5m | `:322` |
| `LLMMetricsAbsent` | 5m | `:333` |
| `GPUMetricsAbsent` | 5m | `gpu-prometheusrule.yaml:95` |
| the two burn alerts | **none, deliberately** | `:366-370` |

Scrapes land every **15s** (`manifests/servicemonitor/llm-sim-servicemonitor.yaml:29`), and
rules evaluate at the kube-prometheus-stack default — read it off the running Prometheus
rather than taking a number from here. So a drill that waits for a 5m alert plus scrape and
evaluation latency is a **seven to eight minute** run at best, and five of the ten modes have
one.

⚠️ **A `for:` is the floor, not the wait, whenever the expression carries a range vector.**
`LLMMetricsStale` is the case: its `rate(...[10m])` has to drain before the `for: 5m` starts,
so the real figure is about **fifteen minutes** (measured, W3.5). Every alert above whose
expression reads a window inherits the same arithmetic — `LLMHighTTFT` sits on a `[5m]`
quantile, so a tenant that becomes slow is a 5m window plus a 2m `for:`, not 2m. **Add the
window to the `for:` before quoting a duration**, and take the totals in this table as the
smaller half of the answer. That is not a coffee break; sequence them so a single cluster session covers several,
and never make CI pay for any of them (rule 7). W9, W10 and W11 are the exception — each
observes something *ceasing to exist*, which needs one evaluation interval, not five minutes.

### The scrape job, and how the GPU exporter must be found — VERIFIED (`manifests/servicemonitor/`)

One ServiceMonitor `llm-sim` selects Services labelled `app.kubernetes.io/name: llm-sim` on
port `llm-metrics` at a 15s interval (`llm-sim-servicemonitor.yaml:19-29`), and one Service
`llm-sim` (`manifests/llm/20-simulators.yaml:171-183`) fronts all three tenants. So all
tenants share the job `llm-sim`, which is the job `verify.sh:522` asserts on, and a tenant
scaled to zero removes one target from it.

⚠️ **The DCGM exporter has no name you may hardcode.** `fake-gpu-servicemonitor.yaml:32`
selects `app: nvidia-dcgm-exporter` and its own comment warns the labels and port move
between chart versions; the alert comment that tells you to scale it
(`gpu-prometheusrule.yaml:93`) writes the workload as the placeholder `<dcgm-exporter>`.
W2.2 must **discover the workload by label** and fail loudly if it finds zero or more than
one, rather than embed a name that a chart bump silently invalidates.

### The PromQL helper you need already exists, inside `verify.sh` — VERIFIED (`verify.sh:121-199`)

`promql_count`, `promql_value` and `promql_label_keys` run instant queries through a
self-healing port-forward whose **PID lives in a file rather than a variable**, because
every caller reaches them through command substitution and a subshell's variable
assignment is discarded (`:123-146`). That was a measured 149s of a 247s run.

**Extract them into a new `scripts/promql.sh`, sourced by both `verify.sh` and the drill.**
Not into `config.sh`, which owns version pins, names, labels and cross-file invariants, and
not copied into the drill: this repo refuses second copies, and two divergent PromQL helpers
is exactly the drift `extract.sh` exists to prevent elsewhere. The contract to document at
the top of the new file: the caller must have sourced `config.sh`, set `KUBECTL`, and
installed its own cleanup.

⚠️ **Extract the functions and NOT the trap — this one is a correctness bug, not a style
note.** `verify.sh:171` installs `trap 'prom_pf_stop; rm -f "$PF_PIDFILE"' EXIT` at file
scope, and `PF_PIDFILE="$(mktemp)"` at `:147`. A shell has **one** EXIT trap. **Demonstrated
in the spike under `bash -c`**: a script that installs a restore trap and then sources a file
installing its own runs only the sourced one, and its own restore never fires and says
nothing. In this harness the caller's EXIT trap is what puts the cluster back. Leave the
`mktemp` and the `trap` to each caller.

⚠️ **Price the extraction honestly: it edits the repo's most load-bearing script, and
proving it still works needs a full cluster `verify.sh` run** (160s of checks on `full` and
150s on `lite` after a cluster exists — `docs/ci.md:366`, run `30998470446`, which owns
every timing in this repo — and it is the gate everything else is measured against). Note
that this is the *post* port-forward-fix figure; the 247s a reader may remember is the
number that fix was measured against. If that run is not
available in the same sitting, ship the drill with its own copy behind a ⚠️ marked in the
file that owns it (rule 16) and land the extraction as its own commit afterwards — but do
not leave the duplication unmarked.

⚠️ **And test it with `bash -c`, never interactively** (rule 17). Two bugs on 2026-08-04
were invisible under zsh.

### ⚠️ A THIRD `zsh` vs `bash` class, and nothing in this repo catches it — MEASURED 2026-08-07

Rule 17 says `zsh` is not `bash`, and CI is `bash`. ⚠️ **There is a third shell on this
desk, and it is also called `bash`:** macOS ships `/bin/bash` **3.2.57**, released in 2007
and kept at that version for licensing reasons. `bash -c` on a Mac is therefore *not* the
shell CI runs, and rule 17's own instruction — test with `bash -c` — silently means the
wrong thing here.

This construct is what surfaced it:

```sh
[ "$(qn "ALERTS{alertname=\"LLMMetricsStaleFast\",alertstate=\"firing\"}")" != "0" ]
```

**Measured across five bash versions, 2026-08-08:**

| bash | verdict |
|--|--|
| 3.2 (macOS `/bin/bash`) | ⚠️ `[: too many arguments` |
| 4.0 | ⚠️ `[: too many arguments` |
| 4.4, 5.0, **5.2** (ubuntu-24.04, **what CI runs**) | fine |
| zsh | fine |

⚠️ **So this is NOT a CI hazard, and the first version of this section said it was.** It is
a local false failure: the construct is correct, `ubuntu-24.04` runs it correctly, and only
an obsolete local shell rejects it. **Do not write a check for it** — a check would flag
correct code, which is the failure `check-word-splitting.py`'s first draft already made
here (29 findings on 12 correct scripts) and the reason rule 18 gained its note about
selftests that only ever confirm the author's belief.

**What to take from it is about the instrument, not the construct.** The spike lost a 300s
measurement loop to this: under `set -e` the failing test neither passes nor fails, it
**errors past**, so a polling loop reads "not yet" forever while the alert it was waiting
for had been firing for four minutes. The time went on suspecting the alert, the rule and
the simulator, in that order, before the shell. **When a shell result is surprising on a
Mac, check `bash --version` before you check anything else** — and reach for
`docker run --rm bash:5.2` when the answer matters, which is the cheap empirical version of
this whole question.

Writing the harness in `verify.sh`'s existing style — assign the query result to a variable,
then test the variable (`:640`) — sidesteps it anyway, and is worth copying for legibility
rather than for portability.

### What `verify.sh` already asserts, which your drills must not break — VERIFIED

L1 `up{job="llm-sim"} == 1` (`:522`), L3 steady p95 under 2s scoped to
`LLM_STEADY_MODEL` (`:544`), L6 `LLMHighTTFT` firing (`:640`), and — the two easiest to miss,
because their labels say LLM while their inputs are DCGM — **L4 cross-domain tokens-per-watt
(`:578`) and L4b the GPU binding joined on pod (`:615`)**. A drill that leaves the
driven profile in a broken state, or leaves the GPU exporter scaled to zero, breaks the
next `verify.sh` run — **which is why W1's restore is a trap and not a closing line.**

---

## W0 — One JSON config surface, identical on Kubernetes and Docker

**Do this first. Every mode below depends on it, and it is the cheapest part of the item.**

**W0.1 One file, one schema: the profile gains a `faults` block.** No second object, no second
flag, no new transport.

```json
{ "model_name": "sim-llama-3-8b-driven",
  "arrival_rate_rps": 1.8,
  "faults": { "freeze": false,
              "surface": "v1",
              "label_bomb": { "series": 0 } } }
```

⚠️ **VALIDATE THE FAULTS BLOCK STRICTLY, WHICH IS NOT HOW THE REST OF THIS PROFILE WORKS.**
`validate_profile` builds `p = dict(DEFAULT_PROFILE); p.update(raw)` (`llm-sim.py:275-276`),
so **unknown keys are carried through in silence** — verified 2026-08-07. For load shaping
that is harmless inheritance. For a fault it is the worst available default: `{"freze": true}`
validates, applies, increments `llmsim_profile_generation` so the drill's own "did it land"
check goes green, and injects nothing. The drill then grades an alert that was never given
anything to see, and reports it as a property of the alert set.

So the `faults` block rejects unknown keys and wrong types by name, listing what it does
know. It is the one place in this file where a typo must be louder than a default, because
**a fault surface that ignores a typo is the exact failure this whole item exists to
catch, arriving through the injection mechanism itself.** Keep `_note` exempt (W0.4).

**W0.2 It is portable because a ConfigMap volume is a file and a bind mount is a file.**
`llm-sim.py` polls the path it was given and neither knows nor cares what wrote it. The
compose stack already mounts a profiles directory (`compose/compose.yaml:60`) and names the
file on the command line (`:54`, `--profile /etc/llm-sim/steady.json`); the cluster mounts a
ConfigMap directory (`llm-driven.yaml:103-109`) and names `/etc/llm-sim/profile.json` (`:85`).
**The filename differs per platform and the mechanism does not** — which is the part that
matters here, and the part the helper has to take as an argument rather than assume. **So the simulator needs no platform code at all** —
which is the whole reason to choose this over a fault API, a sidecar, or a CRD.

Everything the delivery path needs already exists: the 10s poll, `apply_profile` swapping the
profile without disturbing counters, "keep the last good profile" on a parse error, and
`llmsim_profile_generation` as the confirmation that a change landed. **Write no new
delivery mechanism.**

**W0.3 ⚠️ Merge, never rewrite.** One helper, `fault_set <target> <tenant> '<json fragment>'`:
read, deep-merge, write. Per target that is two accessors and nothing else:

| | read | write |
|--|--|--|
| Kubernetes | `kubectl get cm -o jsonpath=...` | `kubectl patch cm --type merge` |
| Docker | `cat compose/.generated/profiles/driven.json` | `tee` the same path |

The merge is not fastidiousness. `drive-llm-load.sh:55-59` records what a whole-object rewrite
costs: a field omitted silently reverts to the simulator's default, and the panel flatlines
mid-demo. With a merge, the load driver and the fault driver write the same file and neither
can revoke the other's fields.

**W0.4 JSON, and the reason in rule 2 is now weaker than the real one.** Rule 2 says JSON
because Python has no stdlib YAML. True, but TOML would pass that test today — `tomllib` has
been in the stdlib since 3.11 and the image is `python:3.12-slim`. **The decisive point is
that `tomllib` is read-only.** W0.3 has to serialise back out, and `json` does both directions
in the stdlib. Record that in the commit, because the stated reason no longer carries the
decision on its own.

⚠️ JSON has no comments, and this repo already pays for it explicitly — `llm-driven.yaml:33`
says "JSON below, so the comments have to live up here". Keep doing that, and allow a `"_note"`
key the validator ignores for anything that must travel with a fault document. One line in
`validate_profile`, no dependency.

**W0.5 Default-empty, and prove it.** `validate_profile` (`llm-sim.py:264`) validates the
block and defaults it to inert. `--selftest` asserts that with no `faults` key the render is
**byte-identical** to today's. ✅ **Verified in the spike**: with the block added and every
fault off, a rendered scrape matched a no-`faults` render byte for byte, and
`llmsim_fault_active` was absent from it and present the moment a fault was held. Emitting
the provenance gauge **only while a fault is active** is what makes both halves true at once. That is what protects rule 1 (fixtures unaffected) and the
fidelity claim at the same time: real vLLM emits no fault machinery, and the moment any leaks
into the default surface the repo's central promise is contaminated.

**W0.6 Emit what is injected.** `llmsim_fault_active{fault="freeze"} 1`, so a run is
falsifiable afterwards. ⚠️ It is provenance, never a detector — W3.5's rule stands, and a rule
keyed on `llmsim_*` transfers to nothing.

**W0.7 Compose needs the driven tenant, which it does not have.** It runs `llm-steady` and
`llm-saturated` only (`compose.yaml:51-74`), and those are fixtures. Add a third service, and
extend `extract.sh`'s `profiles` case — which today hardcodes `manifests/llm/10-profiles.yaml`
— to read `extras/llm-driven.yaml` as well. The awk keys on `name: llm-profile-<x>` and writes
`<x>.json`, so `llm-profile-driven` becomes `driven.json` with no new logic. **Do not
hand-write a second copy of the driven profile into `compose/`**; that is the drift the whole
extraction exists to prevent.

✅ **Verified 2026-08-07.** Adding `manifests/llm/extras/llm-driven.yaml` as a second input
file to the existing `awk` (`extract.sh:55`) produced `driven.json` correctly on the first
try, with no change to the program. This line is as cheap as it says.

⚠️ **`.generated/` is regenerated on every `up`, so compose restores itself and Kubernetes
does not.** Both tenants gate on the `generate` service
(`depends_on: { generate: { condition: service_completed_successfully } }`), which reruns
`extract.sh`, so a fault written into `.generated/profiles/` survives exactly until the next
`docker compose up`. That is a free restore on one backend and no restore at all on the
other. **Say which in the mode's banner** rather than letting W1.6's trap imply the two
behave alike — and do not lean on it: a drill that only restores because something else
happened to overwrite its file is not restoring.

**W0.8 What cannot live in the file, and must not be forced into it.** Target loss, replica
count, adoption labels, exporter scaling. These are faults in Kubernetes objects, and
expressing them in the profile would mean the simulator reaching out to change the cluster —
an operator, in a repo with no reconcile loop and a stdlib-only rule. Two tiers, stated
plainly in the script header:

> **In-process faults are data. Structural faults are `kubectl` or `docker compose`.**

---

## W1 — The harness

**W1.1 One script, `scripts/fault-drill.sh <mode>`, in the `drive-llm-load.sh` mould.**

⚠️ **A deliberate deviation from the roadmap, which says "a scenario script" per mode.**
Ten scripts would each need the polling harness, the expectation printer and the restore
trap, which means one copy each or a fourth shell library. One script with positional modes
matches the existing pattern (`drive-llm-load.sh <idle|steady|burst|saturation|ramp>`) and
keeps rule 7's line exactly where the roadmap wants it: invoked deliberately, never in
`verify.sh`. State the deviation in the script header.

Modes: `target-loss`, `stale`, `partial`, `counter-reset`, `kv-exhaustion`,
`failure-surge`, `not-adopted`, `surface-flip`, `replicas`, `cardinality`, and `list`.

⚠️ **The last three of those break the WATCHING rather than the workload** (W9, W10, W11), so
two harness assumptions do not hold for them and the harness must not assume otherwise: their
expectations are about series and alerts **disappearing**, and `not-adopted` deliberately
produces a state in which the alert set itself is not evaluating. A mode that asserts "N
alerts firing" as a health check would misread all three.

**W1.2 Positional, and every unrecognised argument rejected** (rule 10). Checking only `$2` is
how `--lite` was accepted and ignored. Copy `verify.sh:17-24`'s shape.

⚠️ **It takes a TARGET as well as a mode** — `fault-drill.sh <local|eks|gke|compose> <mode>` —
because W0 made the config portable and the modes should follow it. `config.sh` already
resolves `eks|gke|local` positionally through `ensure_context`, so `compose` is a fourth value
in an existing convention rather than a new one. **kind, EKS and GKE are one backend**: same
manifests, same verbs, differing only in cost and node count. Compose is the second.

A mode with no analogue on a target reports **SKIP with the reason** — `verify.sh:45-48`
already has that vocabulary, and its rule that SKIP must never paper over a real mismatch. The
clearest case is W9: compose has no ServiceMonitor and no `PrometheusRule` CR, so non-adoption
is not a fault that exists there.

⚠️ **Expect the same fault to produce a different observable per backend, and record it rather
than normalising it.** Target loss on Kubernetes removes the endpoint, so `up{job="llm-sim"}`
**vanishes**; on compose's static scrape config the target stays defined and `up` goes to
**0**. An absence rule written against one does not necessarily catch the other, which turns
this harness into a portability grader for the alert set — the repo's premise pointed at a
new axis.

**W1.3 The expectation table is data, printed before the drill runs.** Rule 6 says the
expected values are written before the run; make that structural rather than a habit. Each
mode declares a list of `(PromQL, expected series count, why)` rows, the script prints them
with a banner, and only then acts. `--dry-run` prints the table and exits without touching
the cluster, so the expectations can be reviewed in a diff and in a terminal.

**W1.4 Every expectation is scoped by `model_name`** where the series carries one. See
finding 3 above. A row whose query has no label scope needs a comment saying why it is
correct unscoped.

**W1.5 Poll, with a budget in SECONDS** (rule 5, and `verify.sh:201-215` for why seconds
and not attempts: an attempt count read as 120s was really 216s). Print the residual while
waiting, as L8 does with `promql_value` (`verify.sh:178-182`) — a drill that waits eight
minutes in silence is indistinguishable from a hung one.

**W1.6 Restore in a trap, covering INT and TERM as well as EXIT.** The base restore is
`kubectl apply -f manifests/llm/extras/`, which returns **both** the profile and
`replicas: 1` to their shipped values, since the manifest declares the replica count
(`llm-driven.yaml:60`). Per-mode extras (the GPU exporter in W2, the freeze flag in W3) are
additional. It must be idempotent, and it must run when you Ctrl-C the eight-minute poll —
which you will.

⚠️ `drive-llm-load.sh` deliberately does **not** restore (`:115-116` prints the reset
command instead), because leaving the profile at its last value is what lets you watch the
board afterwards. A fault drill is the opposite case: leaving `kv_cache_tokens_capacity` at
4096 poisons every later run, silently.

**W1.7 The output is a table, and it is the deliverable.** Mode, each expectation, observed,
and PASS/FAIL/SILENT. `SILENT` is a real outcome and must not be spelled `FAIL`: **most of
these ten modes are expected to trip nothing** (see the header — only W2's GPU half and W6
fire against the tree as it stands), so `SILENT` is the ordinary path through this table
rather than an edge case, and a drill that reports its correct result as a failure is a
drill nobody runs twice.

**W1.8 Preconditions checked up front**: the driven tenant exists (`drive-llm-load.sh:43-47`
is the message to copy), Prometheus is reachable, and `kubectl` context is printed. The
context line matters — every one of these drills is destructive to somebody's cluster if
pointed at the wrong one.

---

## W2 — Target loss, and what `absent()` can actually see

**W2.1 Produce it on the LLM side**: `kubectl -n llm-sim scale deploy/llm-driven
--replicas=0`. Expectations, written first:

| Query (scoped) | Expected | Why |
|--|--|--|
| `up{job="llm-sim"}` count | drops by one | the target goes away with the pod |
| `vllm:num_requests_running{model_name="sim-llama-3-8b-driven"}` | 0 series after ~1 scrape | series stop |
| `ALERTS{alertname="LLMMetricsAbsent",alertstate="firing"}` | **0, forever** | `absent()` is global; two fixtures still emit |
| `ALERTS{alertname="LLMHighTTFT",alertstate="firing",model_name="sim-llama-3-8b-saturated"}` | 1 | unchanged, and the control for the scoping in W1.4 |

Hold it for at least `for: 5m` plus one evaluation interval before concluding the absence
alert did not fire, or the result is "we did not wait", not "it does not fire".

The third row is **already proven in promtool** (`spike/spike_test.yaml`): one tenant of two
absent for 15m produces no alert, while both absent produces it at 5m — asserted in the same
run, so the negative cannot be passing vacuously. Lift both cases into
`tests/rules/llm-rules_test.yaml`. The cluster drill is then confirming that the live path
behaves as the expression says, which is the whole point of this item.

**W2.2 Produce it on the GPU side**, where the same expression shape does fire: scale the
fake DCGM exporter to zero, per `gpu-prometheusrule.yaml:92-93`. Expect
`GPUMetricsAbsent` firing after 5m, and expect the derived `DCGM_FI_DEV_GPU_TEMP` /
`_POWER_USAGE` series to disappear with it (`gpu-prometheusrule.yaml:37-49` computes them
from `DCGM_FI_DEV_GPU_UTIL`).

⚠️ **This is the one drill that touches a shared component, and its blast radius reaches
past the GPU checks into the LLM ones.** Every GPU-side check goes red, and so do **L4**
(`verify.sh:578` — `llm:tokens_per_watt:5m` divides by `DCGM_FI_DEV_POWER_USAGE`, which the
recording rules synthesise from `DCGM_FI_DEV_GPU_UTIL`) and **L4b** (`:615`, the GPU binding
joined on pod). Note also that the GPU-side checks are numbered only in `verify.sh`'s
comments while the LLM ones print their labels, so what you watch fail and what you read in
the script do not line up. Restore is mandatory, the mode must say so in its banner, and it
must never run concurrently with `verify.sh`.

**W2.3 The decision this mode forces.** A per-tenant absence detector needs to know which
tenants are supposed to exist, and PromQL cannot know that from the metric alone. Enumerate
the options, land **one** with the reasoning in the rule comment, or write down the decision
not to have one:

- `absent(vllm:num_requests_running)` — what exists. Fires only on total LLM outage.
- `absent_over_time(vllm:num_requests_running{model_name="X"}[10m])` — per tenant, but
  hardcodes a name into the rules, which then drift from the profiles.
- `up{job="llm-sim"} == 0` — catches a failing scrape, **not** a removed target: scale to
  zero and the `up` series disappears too.
- `kube_deployment_status_replicas_available{deployment=~"llm-.*"} == 0` — kube-state-metrics
  ships with the stack. It is an infrastructure alert wearing an LLM name; say so if you
  pick it. ⚠️ **And it does not exist under `LITE=1`**: `helm/kube-prometheus-stack/values-lite.yaml:62`
  disables kube-state-metrics outright, so a rule built on `kube_*` is green-by-absence on
  one of the two kind legs. That alone probably disqualifies it here.

**W2.4 Correct the two wrong instructions** (finding 2), and `docs/architecture.md:128` if
the alert set changes.

---

## W3 — Stale but up

The sharp one. The scrape succeeds, every series is present, and the counters do not move.
`absent()` cannot see it. `rate()` reads zero, which is exactly what an idle tenant reads.

**W3.1 A `freeze` knob in `llm-sim.py`, delivered through the profile.** Boolean, default
false, validated like every other key (`validate_profile`, `llm-sim.py:264`). When true,
the worker stops advancing the simulation and the handler keeps serving the last state.
Delivered through the ConfigMap poll, so freezing is not a restart and the counters stay
continuous right up to the moment they stop — which is the shape a real wedged engine has.

**W3.2 ⚠️ The naive freeze produces a thaw burst, and it will be blamed on Prometheus.**
`worker()` calls `advance_to(now)` with `now = time.monotonic()` (`llm-sim.py:847-849`), and
`advance_to` processes **every event scheduled at or before the target** (`:545-560`). Skip the call
while frozen and the first unfrozen call replays the entire frozen interval in one pass:
minutes of arrivals and completions land in a single scrape gap, every histogram takes a
step change, and `rate()` reports a spike that never happened.

⚠️ **AND THE FIX IS THREE CHANGES, NOT ONE. The other two are in `worker()`'s two
comparisons, and both were invisible until the design was wired in** (`spike/worker_freeze.py`,
2026-08-07). Once the simulated clock can differ from the wall clock, every comparison in
that loop has to pick one deliberately, and today both pick wrong:

| `worker()` line | Reads | Consequence once frozen |
|--|--|--|
| the profile poll cadence, `now - last_poll >= poll_seconds` | the **simulated** clock | ⚠️ **The freeze is permanent.** `now` stops, so the poll stops, and the poll is the only path a thaw can arrive by. Nothing errors, the pod stays Ready, `/metrics` serves the last state forever |
| the event sleep, `due - time.monotonic()` | `due` is **simulated**, `monotonic()` is **wall** | Permanently negative by `frozen_seconds`, so every pass takes the `0.01` floor. **Measured 70 passes/s against 2/s** — a core burned for the life of the pod, with correct output |

The poll must key off wall clock; the sleep must compare `due` against
`time.monotonic() - frozen_seconds`. Both are one-line changes and neither is findable by
reading the design, which is the whole argument for building it before pricing it.

⚠️ **The spin only shows at a realistic arrival rate.** At 100 rps the next event is ~0.01s
away regardless, so both arms sleep the floor and the bug is invisible — the spike's own
check reported no difference until it was re-pointed at the shipped 1.8 rps. A performance
assertion needs the operating point it is meant to protect.

**MEASURED, not predicted** (`spike/thaw_burst.py`, a 5 minute freeze at 1.8 rps):

| | Tokens in the first post-thaw scrape gap |
|--|--|
| a normal 30s interval, for scale | 13,781 |
| naive freeze, first `advance_to` after the thaw | **135,636 — 10x** |
| clock-offset fix, first call | **0**, then 13,781 in the next 30s (1.00x) |

Fix it in the clock, not the event loop: keep a `frozen_seconds` accumulator and advance to
`time.monotonic() - frozen_seconds`. The simulated clock then resumes where it stopped and
lags wall clock by the frozen duration forever after, which is invisible in the exposition
because everything emitted is a duration or a count, never a timestamp. **Assert the absence
of the burst in `--selftest`**: freeze, advance wall clock, thaw, and check that no more
than one event's worth of counters moved. The spike's freeze also held **8 requests
running**, which is the gauge W3.5's detector needs on the other side of the conjunction.

**W3.3 `--selftest` coverage, driven red first** (rule 18). At minimum: frozen renders are
byte-identical across a wall-clock interval except for nothing at all; `num_requests_running`
holds its pre-freeze value rather than draining to zero; and the thaw test above. Break each
one deliberately and watch it fail before you trust it.

⚠️ **Three of these have to run the real `worker()` in a thread, not a scratch model of it**,
because the bugs W3.2 records live in `worker()`'s comparisons and a harness that reimplements
the loop reimplements them away. `spike/worker_freeze.py` is the shape: a real `State`, a real
profile file on disk, the freeze delivered through the real poll. Add to the list above:

- **the thaw is deliverable at all** — freeze, thaw through the profile file, and assert the
  counters resume. This is the one that catches the permanent-freeze bug, and it needs a
  **control arm** (the fixed loop) beside it or "counters did not move" passes for the wrong
  reason.
- **the loop does not busy-spin after a thaw** — pass count per second, against the shipped
  arrival rate and not a fast one (W3.2).
- **an unknown fault key is refused** (W0.1).

⚠️ **Watch the counter you assert on actually move.** The spike's first run asserted a frozen
`generation_tokens_total` over a 2s window against the shipped profile, where one request
takes ~5s to complete — so it read `0 -> 0` and passed while proving only that nothing had
finished yet. Either drive a fast profile or make the window longer than a request.

⚠️ `render()` is a pure read and `--selftest` already asserts that via the `observations`
counter (`llm-sim.py:506`). Freezing must not disturb that property.

**W3.4 The drill.** Freeze the driven tenant, then assert. Note before you start that the
readiness probe is an HTTP GET on `/metrics` (`llm-driven.yaml:91-94`), so a frozen pod
stays **Ready**: Kubernetes cannot see this failure either, which is half of why the mode
is worth building.

⚠️ **RAISE THE ARRIVAL RATE FIRST, AND WAIT FOR THE POPULATION BEFORE INJECTING.** This is
the same sentence W6 already carries, for the same reason, and W3 needs it just as much.
`llm-driven` ships at 0.4 rps, where mean concurrency is under two and **the population hits
zero regularly** (Background). Freeze on one of those moments and `num_requests_running` is
0, so the detector's `running > 0` guard excludes the tenant — correctly, since a tenant
with nothing in flight is idle rather than wedged. The drill then produces no detectable
state and reports **"the alert did not fire"**, which is a true sentence about a state that
was never created, and the most dangerous possible output from a rig whose product is
grading alerts.

**Measured, and it is a coin flip at the shipped rate**: two runs of `spike/stale_e2e.sh`
minutes apart, one held **6** requests at the freeze and the next held **0**. So the drill
sets `arrival_rate_rps` to something that holds a population — 1.8 works — and then **polls
`vllm:num_requests_running > 0` as a precondition** before it writes the freeze. A drill that
cannot confirm the state it depends on has no business reporting on the alert that watches it.

| Query | Expected | Why |
|--|--|--|
| `up{job="llm-sim"} == 1` | unchanged | the target is healthy; that is the point |
| `ALERTS{alertname="LLMMetricsAbsent"}` | 0 | absence cannot see this |
| `rate(vllm:generation_tokens_total{model_name="...driven"}[5m])` | 0 | frozen counter |
| `vllm:num_requests_running{model_name="...driven"}` | > 0, flat | the engine still claims work |

The last two rows together are the signature, and neither alone is one.

**W3.5 The detector, and it must be written over `vllm:*` only.** A rule keyed on
`llmsim_profile_generation` or any other `llmsim_*` series would work here and transfer to
nothing: real vLLM does not emit them. Proposed:

```
- alert: LLMMetricsStale
  expr: vllm:num_requests_running > 0
        and rate(vllm:generation_tokens_total[10m]) == 0
  for: 5m
```

**Both halves are proven in promtool** (`spike/spike_test.yaml`, `spike/stale-rules.yaml`):
the expression fires for the frozen tenant only, and the guard-less variant fires for the
idle one too. Lift those cases into `tests/rules/llm-rules_test.yaml` rather than writing
them again.

⚠️ **The `and` matches on the whole label set apart from `__name__`.** Both sides come from
the same target here, so the metric names differing is fine and everything else lines up —
but `sum by (model_name)` on one side only matches nothing and the alert is green forever.
**Measured in the spike**, by making exactly that edit:

```
FAILED: alertname: LLMMetricsStale, time: 25m
    exp:[ ... LLMMetricsStale{model_name="sim-llama-3-8b-driven", ...} ]
    got:[]
```

`got:[]` is what this failure looks like in production too, except nothing prints it. Same
genre as `llm-prometheusrule.yaml:380-386` for the burn alerts and `:95-104` for the phase
means.

⚠️ **THE WINDOW IS A DETECTION LATENCY, AND IT IS ADDED TO THE `for:`, NOT OVERLAPPED WITH
IT.** `rate(v[10m])` reaches zero only once the **whole** window contains a flat counter, so
after a freeze the window drains first and the `for:` starts counting after that.
**Measured** (`spike/stale_e2e.sh`, a `[1m]`/`for: 30s` twin of the shipped rule, real
Prometheus, real frozen simulator):

```
    rate() reached zero   : 56s after the freeze   (a [1m] window)
    alert reached firing  : 87s after the freeze   (+ a 30s `for:`)
```

56 + 30 = 86, against 87 observed. So at the **shipped `[10m]` and `for: 5m` this alert takes
about fifteen minutes to fire**, not five, and not the "seven to eight minutes" the timings
table budgets for a 5m alert. Budget the drill accordingly, print the residual while it waits
(W1.5), and if fifteen minutes is too long to be useful, that is an argument about the window
— which is the trade-off below, now with a cost attached to one side of it.

⚠️ **The window is a real trade-off, not a default.** 10m over `generation_tokens_total`
says "no request has completed in ten minutes". On this rig the saturated tenant's queue
wait plateaus near 58s (`drive-llm-load.sh:49-53`, 160 / 2.74 by Little's Law) and e2e is
that plus a prefill and a decode, so 10m clears it by an order of magnitude — but a real
deployment generating long outputs at low concurrency can exceed it legitimately. State the assumption in the rule comment and pick the window
deliberately.

**W3.6 The negative case is what makes it worth having.** An **idle** tenant reads
`running == 0` and `rate == 0` and must **not** fire. Assert it in promtool, then break the
rule by dropping the `running > 0` conjunct and watch the idle case go red (rule 18). That
is the experiment that shows the conjunction is doing the work.

⚠️ **Also run it against LIVE data, which promtool cannot do and which is this item's whole
premise.** Done in the spike by standing a fourth, genuinely idle tenant beside the frozen
one and asking both expressions of the same Prometheus:

```
    with the running > 0 guard : 1 series   (the frozen tenant)
    without it                 : 2 series   (the frozen tenant AND the idle one)
```

⚠️ **And the negative case needs something quiet to be a case at all.** The spike's first
attempt ran three busy tenants and one frozen one, where guarded and guardless both return
the same single series and the check passes while proving nothing. An idle tenant is not
scenery here; it is the control.

**W3.7 Pay finding 5's bill**, all seven places: rule file, promtool cases both sides, the
alert table row, `docs/architecture.md:128`, the catalog page's own alert table, a
`CHANGELOG.md` entry, and the derived count at `docs/llm-simulation.md:43`. Run
`task doc-claims` and expect it to go red before you fix the prose; that is the check
earning its keep.

---

## W4 — Partial outage: grading the dashboard, not the alerts

**W4.1** With `llm-driven` scaled to zero from W2 (or frozen from W3), the question is not
what alerts do. It is **whether a human looking at the LLM board would notice**.

**W4.2 Read `manifests/dashboards/llm-sim-overview.json` and list every panel whose
expression aggregates across `model_name` without a breakdown.** The recording rules are
aggregated `by (model_name)` deliberately (`llm-prometheusrule.yaml:20-22`), so a panel that
sums them back up throws away the distinction the rules exist to preserve.
`llm:tokens_per_watt:5m` is cluster-aggregate **on purpose** (`:188-198`) and is not a
finding; anything else that hides a dead tenant is.

**W4.3 The deliverable is a table** — panel, expression, and whether a dead tenant is
visible on it — plus either a repo change or a written decision. This is the same service
the rig already performs for alerts, aimed at the boards.

⚠️ **If it does lead to a panel change, rule 9 applies**: the repo JSON is the source of
truth, the `uid` never changes, and the grafana.com copy is derived by `task dashboards` and
republished as a **revision of id 25620**, never as a new upload. A dashboard edit is
therefore a bigger commit than it looks; prefer landing the table first and the change
second.

---

## W5 — Counter reset

**W5.1 Write the prediction table before you run anything.** This is the mode whose entire
value is the prediction; producing it is `kubectl -n llm-sim rollout restart
deploy/llm-driven`. The claim under test is that `rate()` handles resets and that recording
rules stacked on `rate()` across a restart do too.

Predict, at minimum: `llm:ttft:p95_5m` for the driven tenant across the restart;
`llm:ttft:slo_ratio5m` (numerator and denominator reset together, but the **old and new pods
are different series** with disjoint lifetimes, and `sum by (model_name)` merges them);
whether `LLMTTFTErrorBudgetFastBurn` can misfire during the transient (it needs both the 1h
and the 5m windows over 14.4x, so a 5m-only artefact should not be enough — state that
before checking); and whether the ratio can transiently exceed 1.0.

**W5.2 Then run it and record where the prediction was wrong.** A prediction table that
matched perfectly and a prediction table that was wrong in one row are both good results.
A drill run without one is not a test.

---

## W6 — KV cache exhaustion, isolated from saturation

**W6.1 ⚠️ The isolation window is narrow, and the spike found its edge.** The obvious move
— raise the arrival rate so the request population is large and stable — breaks the drill,
because the queue that fills the KV cache also raises TTFT. Measured across eight seeds
(`spike/kv_profile.py` — the same run as the Background table, one script for both; bucket
p95 computed the way the alert computes it, over `TTFT_BUCKETS`, not from raw samples):

| rps | max queue depth | bucket p95 TTFT | isolated? |
|--|--|--|--|
| 1.2 | 0 | 0.099s | yes, but the population empties (see Background) |
| **1.8** | **5** | **0.696s** | **yes** |
| 2.4 | 21 | **6.453s** | **no — over the 2s `LLMHighTTFT` threshold** |

So the drill runs at **1.8 rps**, and 2.4 is out. Note what the middle column shows: the
queue never approaches `LLMQueueBacklog`'s 50, so **TTFT is the binding constraint on
isolation, not queue depth** — which is the opposite of what "isolate it from saturation"
suggests.

**W6.2 The candidate the spike landed on**, worst case over eight seeds:

```
arrival_rate_rps 1.8 | prompt_tokens.mean 3072 | kv_cache_tokens_capacity 8192
  -> kv usage min 1.000, queue max 5, bucket p95 TTFT 0.696s
```

⚠️ **Re-derive it; do not paste it.** It is a measurement of an unseeded process, the live
tenant draws a fresh seed on every start, and the neighbouring setting shows how thin the
margin gets: `kv_cache_tokens_capacity: 10240` bottoms out at **0.900488** against a `> 0.9`
threshold — 0.05% of headroom, which is another seed set away from not firing at all. 8192
bottoms out at a clamped 1.000. Choose the one that is not interesting.

**W6.3 Two levers, and the second is more faithful**: lower `kv_cache_tokens_capacity`, or
raise `prompt_tokens.mean`. Neither touches latency (verified above). The candidate uses
both — prompt length for the mechanism, capacity for the trim — because prompt length is
how a real deployment fills its KV cache.

**W6.4 The isolation is the assertion.** Expected while it holds:

| Query | Expected |
|--|--|
| `ALERTS{alertname="LLMKVCacheSaturated",...,model_name="...driven"}` firing | 1 |
| `ALERTS{alertname="LLMQueueBacklog",...,model_name="...driven"}` | 0 |
| `ALERTS{alertname="LLMHighTTFT",...,model_name="...driven"}` | 0 |
| `llm:ttft:p95_5m{model_name="...driven"}` | under 2 |

The last three rows are the point: this mode exists to fire **one** alert and demonstrate
that the other two stayed quiet, which is the state ROADMAP.md says no fixture provides.

**W6.5 Say what the drill did and did not do.** It moved a gauge past a threshold and the
alert that watches that gauge fired. The engine's behaviour is unchanged. See the
`num_preemptions_total` note in Background; do not let the write-up imply modelled KV
pressure.

⚠️ **And it falsifies a sentence already in the tree.** `tests/README.md:27` gives "nothing
on the rig reaches 90% KV cache" as the reason `LLMKVCacheSaturated` can only ever be
unit-tested. After this mode lands, something does — deliberately, on the driven tenant, and
only while a drill is running. Correct it in the commit that lands the drill (rule 12). It
is not in finding 5's table because it is a consequence of W6 rather than of a new alert.

**W6.6 Expect a clamped 1.0** at any robust setting (`llm-sim.py:693`), and note the
consequence in the write-up: with a `for: 5m` over a fluctuating population, the settings
that fire reliably are the settings where the crossing is invisible. That is a real property
of gauge-plus-duration alerting, worth one paragraph.

---

## W7 — Request-failure surge

**W7.1 Producible today, and honest only halfway.** `finish_reasons` already carries a 1%
abort (`llm-sim.py:252`); the weights must sum to 1.0 (`:316-321`), and
`vllm:request_success_total` carries a `finish_reason` label (`:456`, `:759`). Raise abort
to a surge level and the series moves.

**W7.2 ⚠️ Write the limitation into the drill's own output, not just the write-up.**
"The request failed" and "the simulator recorded an abort" are the same statement while
nothing outside the process measures a request. Client-side truth is what separates them,
and it arrives with ROADMAP.md item 2. The drill prints that sentence every time it runs.

**W7.3 Nothing watches this.** The decision is the deliverable: either an alert over the
abort ratio, with the same care the SLO ratios take about denominators near zero
(`llm-prometheusrule.yaml:242-244` on clamping at an epsilon rather than at 1, and
`:372-378` on why a traffic guard is not optional — an error-ratio alert on an idle tenant
pins at 100% and fires hardest on a tenant serving nothing), or a written decision not to
have one. If you add it, pay finding 5's bill again.

**W7.4 This drill gets rewritten once item 2 lands**, and the second version is the one that
means anything. Say so in the mode's header so the rewrite is expected rather than discovered.

---

## W8 — Cardinality explosion

**Last, bounded, and never in CI.** This is the one mode that can take the monitoring stack
down, which is precisely why it is worth measuring — and why it goes last, after every other
result is banked.

**W8.1 Decide where the label goes, and write down which and why.** Two options:

- On a `vllm:*` series, so the recording rules see the explosion. Faithful to the operator
  mistake being modelled, and it puts a label on a series that real vLLM does not carry.
- On an `llmsim_*` series. Keeps the vLLM surface honest; the Prometheus-side failure is
  identical, but the rule-side effect is not exercised.

Whichever you choose: the knob is **off by default**, `--selftest` asserts the default
render carries no such label, and the profile key is named so nobody enables it by accident.
`scripts/check-doc-claims.py` derives an "emits N metrics" claim from `--print`, so a bomb
that is on by default breaks `task doc-claims` — another check earning its keep.

**W8.2 Compute the blast radius before you run it, and cap it.** Series added is
`cardinality x (series carrying the label)`. State the ceiling in the script, refuse values
above it, and refuse to run at all without an explicit confirmation flag.

**W8.3 What to measure**: `prometheus_tsdb_head_series`, scrape duration for the `llm-sim`
job, Prometheus RSS, and which one degrades first. Ramp — 1k, 10k, 100k — rather than
jumping to the largest.

**W8.4 The mitigation is the finding.** `sampleLimit`, `labelLimit` and `targetLimit` exist
on the `ServiceMonitor` CRD and this repo sets none of them. Measure what a limit does to
the same run: the target goes down and the alert set sees target loss rather than a
poisoned TSDB, which is the trade the limit buys.

⚠️ **Blast radius warning in the banner, and `LITE=1` explicitly out of scope**: a 4 GiB
runtime has no headroom for this. Say "run this on a cluster you can throw away", and mean it.

---

## W9 — Rules and ServiceMonitors that were never adopted

The repo's **most documented failure**, and nothing injects it. `config.sh:155` describes
`RELEASE_LABEL` as the `release:` selector carried by the four objects `install.sh` applies;
the chart template calls the consequence "THE SILENT ONE"
(`charts/k8s-ai-observability/templates/prometheusrules.yaml:20-27`); the ServiceMonitor
repeats the warning (`llm-sim-servicemonitor.yaml:13`); and `verify.sh:82` exists solely
because the failure is so slow to surface — **measured at over ten minutes against ~90
seconds for a healthy install** (`verify.sh:65`).

**W9.1 ⚠️ It is self-concealing, which is the whole point.** An unadopted `PrometheusRule`
means its rules never evaluate. So an alert *inside that file* watching for its own absence
is unadopted too, and stays silent about being silent. Nothing errors, nothing warns, and
the object reports itself created.

**W9.2 Produce it with a canary object, not by mislabelling a live one.** Apply a new
`PrometheusRule` carrying an unmissable rule — one that fires unconditionally — with a
deliberately wrong `release` label. It never appears. Re-apply the identical object with the
correct label and it appears within a reload. **Blast radius: zero**, nothing shipped is
touched, and the mechanism is demonstrated both ways in one drill.

⚠️ A second, optional step mislabels the real LLM `PrometheusRule` and measures **how long
the failure takes to become visible** — the number `verify.sh:65` already quotes for a
different path. That step breaks every `llm:*` series and every LLM alert while it runs, so
it carries W2.2's rules: banner warning, mandatory restore, never concurrent with
`verify.sh`.

**W9.3 The observable is an alert that stops.** `LLMHighTTFT` fires permanently against
`llm-saturated` (finding 3). Under an unadopted rule file it does not go to `pending` or
`resolved` — it **ceases to exist**, which is a different thing from a resolved alert and is
the signature to record.

**W9.4 The decision, and it may well be "no alert".** A detector cannot live in the object it
watches. The candidates are a second `PrometheusRule` (which has the same failure mode, just
uncorrelated), or Prometheus's own `prometheus_rule_group_rules` / config-reload series,
which come from a different target entirely. **The defensible answer may be that this is an
install-time gate rather than a runtime alert, and that `verify.sh` is already it** — write
that down with the reasoning if that is where you land. Rule 18 cuts both ways: an alert
that cannot survive the failure it watches is not an alert.

---

## W10 — The metric surface moves under the queries

`llm-sim.py` already models the v0-to-v1 rename (`METRIC_SURFACES`, `:148`), and
`config.sh:234` pins the rig to `v1` while `:220` notes `--vllm-surface both` exists for
upgrade testing. The rename is documented at length and **has never been injected**: a
tenant serving v0 names is perfectly healthy, scraped, and invisible to every rule and panel
keyed on `kv_cache_usage_perc` and `inter_token_latency_seconds`.

**W10.1 ⚠️ The flip is unavoidably a restart, so the drill needs a control.** The surface is
an argparse option with an environment fallback — `--vllm-surface`, default
`LLM_SIM_VLLM_SURFACE`, read once at startup (`llm-sim.py:1305-1306`). It is **not** a
profile key, so unlike every other mode here it cannot be hot-reloaded, and a naive run
confounds the rename with W5's counter reset.

Run it as a pair:

| | Restart | Surface | Isolates |
|--|--|--|--|
| control | yes | unchanged (`v1`) | what a restart alone does |
| treatment | yes | `v0` | what the rename adds on top |

**W10.2 The expectations** are the interesting part, so write them first: which panels blank,
which recording rules produce nothing, which alerts cease to exist (as in W9.3), and whether
anything at all reports the tenant as unhealthy. Expect: the target stays `up`, the pod stays
`Ready`, and `absent()` on the v1 names becomes true only if **no** tenant serves v1 — the
same global-operator trap as finding 1, arriving by a different road.

**W10.3 `both` is the third row, and it is the realistic one.** A real engine upgrade emits
both names for a release. Running one tenant on `both` shows which queries silently read the
superseded series — which is precisely the finding ROADMAP.md item 3 records against
`llm-d-inference-sim`, reproduced here against a surface this repo controls.

---

## W11 — Two replicas of one `model_name`

**W11.1 The claim under test is already written down as a caveat.**
`llm-prometheusrule.yaml:230-234` states that the SLO ratio is exact only because numerator
and denominator are the same histogram on the same target at the same scrape timestamps, so
`rate()`'s extrapolation cancels — and that **"across replicas whose scrapes are out of phase
it does not"**. The catalog page repeats it: "One pod per model here; if you run four, know
why it might not hold" (`llm-sim-overview.grafana-com.md:179`). Neither has ever been
measured. `kubectl -n llm-sim scale deploy/llm-driven --replicas=2` is the whole apparatus.

**W11.2 Predict all four effects before scaling** (rule 6):

- **Offered load doubles.** Each replica reads the same profile and synthesises its own full
  arrival rate — the mechanism ROADMAP.md cites for autoscaling having the wrong sign here.
  Observed directly, this is a result rather than an assumption.
- **Per-pod gauges return two series.** `vllm:num_requests_running{model_name="...driven"}`
  is no longer one line. Anything not aggregating sees both.
- **`llm:ttft:p95_5m` should be roughly unchanged** — two histograms drawn from the same
  distribution merge cleanly.
- **`llm:ttft:slo_ratio5m` is the one under test.** State the expected direction and rough
  magnitude of the deviation before looking.

**W11.3 ⚠️ "Too small to measure here" is a legitimate result, and agree to it in advance.**
Two pods scraped by one Prometheus on one interval may be close enough in phase that the
deviation sits under the noise of an unseeded simulator over a short window. Writing that
down beats manufacturing a number, and it is the honest half of the repo's own motto about
invented figures. If it *is* measurable, the deliverable is a number replacing "might not
hold" on the catalog page.

⚠️ **That page is em-dash-free and enforced** (rule 13, `EM_DASH_FREE` in
`check-doc-claims.py`). Edit it accordingly, or `task preflight` tells you.

---

## Effort

Estimates from reading the code, not from doing the work. **Treat the ordering as firmer
than the numbers, and re-derive the largest line before planning around it.** Instruments
are priced as code plus 25-35% verification, because the selftest is where these overrun.

⚠️ **The table sums to 34 hours, about 4.25 days — and the honest comparison with
ROADMAP.md's 3 to 4 days for the same item is narrower than that looks.** Three of these
lines (W9, W10 and W11, 3 hours together) are work the roadmap's mode table does not contain
at all, so like for like this item is ~31 hours against a 24 to 32 hour estimate: inside the
range rather than over it. What the roadmap genuinely assumed away is still real and named
twice — W3's simulator change (finding 4) and the seven-place cost of every new alert
(finding 5) — it is simply offset by modes that came in cheaper than the page expected,
mostly because the spike did the expensive part first. Correct the roadmap's estimate when
you know the real number, with the actual beside it, as the page already does for item 3's
surface question.

⚠️ **The spike moved three of these lines down**, and the pattern is the one ROADMAP.md
already warns about: the cheap empirical version existed. The promtool cases for W2 and W3
are written and proven, W3's freeze design is settled with numbers, and W6's profile is
derived. What remains in those lines is cluster work and integration.

| | Estimate |
|--|--|
| **W0 config surface: `faults` schema, merge helper, two accessors, compose tenant** | **~2 hours** |
| W1 harness: modes, expectation tables, polling, restore trap, helper extraction | ~5 hours |
| W2 target loss both sides, plus the absence-detector decision and the doc corrections | ~2 hours, promtool cases done |
| **W3 freeze knob, thaw fix, selftest, drill, new alert, promtool both sides, five docs** | **~1 day**, design and cases done |
| W4 dashboard grading and the panel table | ~3 hours |
| W5 counter reset: prediction table, one restart, the write-up | ~2 hours |
| W6 KV exhaustion: offline derivation, then the drill | ~2 hours, derivation done |
| W7 failure surge, plus the alert-or-not decision | ~3 hours |
| W9 adoption canary, the optional destructive step, and the alert-or-not decision | ~1 hour |
| W10 surface flip with a restart control, plus the `both` row | ~1 hour |
| W11 two replicas, prediction table, and the catalog-page number | ~1 hour |
| W8 cardinality: knob, caps, ramp, `sampleLimit` comparison | ~half a day |

⚠️ **W0 buys both platforms for two hours, and that is the point of doing it first.** The
transport is free — the poll, the validator, the "last good profile" behaviour and the
generation counter all already exist — so what is being written is a schema and a merge. Every
fault below still costs what it costs; W0 does not make them cheaper, it makes them portable.

⚠️ ~~**W3 is still the softest number and the largest** ... the clock-offset fix is proven as
a design on a scratch harness, not inside `worker()`.~~ **DONE — built inside `worker()` on
2026-08-07** (`spike/worker_freeze.py`). That was the right call and it did not make the line
smaller. The design held; the wiring around it did not, and the spike found **two further
one-line clock bugs plus a validation gap** that reading could not reach. W3's code is now
designed, measured and driven red — what remains is writing it in its real place with the
selftest, the five-doc bill and a fifteen-minute cluster drill. **Treat ~1 day as firmer
than before rather than smaller**, and note that the drill's wall clock roughly doubled.

⚠️ **Wall-clock is not effort here.** Five of the ten modes wait out a 5m `for:`. The
cluster time is roughly an hour of waiting spread across the drills, and it does not
compress. W9, W10 and W11 are the cheap exception: none of them waits on a `for:`, because
what each observes is a series or an alert **ceasing to exist**, which is visible within an
evaluation interval.

---

## Non-goals

- **Touching the fixtures.** Rule 1. `llm-steady` and `llm-saturated` are never scaled,
  driven or retuned, and the two documented instructions that say otherwise get corrected
  rather than followed.
- **Any `verify.sh` check asserting a fault.** Rule 7: a finding with a shelf life belongs in
  a scenario script. What CI gains is `llm-sim.py --selftest` coverage — the freeze knob and
  its thaw-burst assertion (W3.3), the byte-identical default render (W0.5) and the label
  bomb's off-by-default assertion (W8.1) — plus the promtool cases for any new alert.
  Nothing beyond that, and nothing that waits on a `for:`.
- **Modelling KV pressure, or emitting `num_preemptions_total`.** See W6.5.
- **A configuration language, a CRD, a fault API or a sidecar.** W0 is a JSON block in a file
  the simulator already reads. Anything that needs a controller, a schema compiler or a second
  transport has left the design, and the stdlib-only rule is what it hits first.
- **A client, an ingest path, or streaming.** That is ROADMAP.md item 2, and W7 states the
  limitation rather than working around it.
- **Draining or cordoning a node.** `kind/gpu-sim.yaml` is a single control-plane node;
  `prompt-disruption-drill.md` documents what that costs and evicts pods instead. Nothing
  here needs a node-level action at all.
- **An autoscaler.** Item 4.
- **Making the drills part of `install.sh` or the chart.** The tenant is opt-in and the
  drills are invoked by hand. A new alert does ship in the chart; the drills do not.
- **Prometheus's own health** — rule-evaluation failures, a restart mid-window and WAL
  replay, scrape timeouts, out-of-order samples. Real, and adjacent to W9, but it is a
  different instrument: the subject becomes the monitoring stack rather than the alert set,
  and it wants its own prompt. Say so if W9 tempts you further in.
- **Alert delivery.** Everything here grades whether an alert reaches `firing`, never whether
  it reaches a person. Alertmanager is `enabled: false` under `LITE=1`
  (`helm/kube-prometheus-stack/values-lite.yaml`), so half the CI matrix has no delivery path
  to test against — which is itself a finding worth one line in the write-up, and no more.
- **OOMKill and CPU throttling of a simulator pod.** Same reset as W5 by a different cause.
  Worth having, cheap, and deliberately deferred so W5's prediction table stays about `rate()`
  rather than about `kubectl`.

---

## Acceptance criteria

Written before the work (house convention), and mapped to ROADMAP.md item 1's "Done when".

1. Every in-process fault is configured **only** through the `faults` block of the tenant's
   `profile.json`, written by a **merge** and never a whole-object rewrite, and the identical
   document works on Kubernetes and on compose. No second config mechanism exists.

2. With no `faults` key present, `--selftest` shows the render is **byte-identical** to
   today's, and the compose stack runs `llm-driven` from a profile extracted by
   `extract.sh` rather than a second copy.

3. Every mode is invoked deliberately from `scripts/fault-drill.sh <target> <mode>`; nothing
   in `verify.sh`, `install.sh` or CI runs a drill, and a mode with no analogue on a target
   reports SKIP with its reason.

4. Every mode prints its expectation table **before** acting, and `--dry-run` prints it
   without touching the cluster.

5. Every expectation that can be scoped by `model_name` is, and any that is not carries a
   comment saying why.

6. Every wait is polled with a budget in seconds, and prints its residual while waiting.

7. Restore runs from a trap covering EXIT, INT and TERM, is idempotent, and leaves
   `verify.sh` passing. Demonstrated by running `verify.sh` after a Ctrl-C'd drill.

8. `LLMMetricsAbsent` is shown **not** to fire on single-tenant loss, `GPUMetricsAbsent` is
   shown to fire, and the two wrong "to test it" instructions are corrected.

9. Stale-but-up is produced, shown to defeat the absence alerts, and detected by a rule
   written over `vllm:*` series only — with its idle-tenant negative case asserted in
   promtool and driven red once.

10. The freeze knob is off by default, covered by `--selftest` including the thaw-burst
    assertion, and each new selftest assertion has been driven red deliberately (rule 18).
    The freeze assertions run the **real `worker()`**, cover **both** clock comparisons
    (W3.2), and each carries a control arm so none can pass for the wrong reason.

11. **An unknown or mistyped key in the `faults` block is refused by name**, with a message
    naming what is known. Asserted in `--selftest`, and driven red by a profile that a
    permissive validator would have accepted silently.

12. **The stale drill raises the arrival rate and confirms a non-zero request population
    before it injects**, and says in its output that it did so. Freezing an idle tenant
    produces "no alert fired" about a state that never existed, which is the one output this
    rig must never emit (W3.4).

13. W4 produces the panel table — panel, expression, whether a dead tenant is visible — for
    every panel on `llm-sim-overview.json` that aggregates across `model_name`, and each
    finding gets either a repo change or a written decision. `llm:tokens_per_watt:5m` is
    excluded with its reason, not silently.

14. W5's prediction table is written and committed **before** the restart is run, and the
    write-up records every row the prediction got wrong. A drill run without one does not
    count as run.

15. KV exhaustion fires `LLMKVCacheSaturated` for the driven tenant while `LLMQueueBacklog`
    and `LLMHighTTFT` stay silent **for that tenant**, with the capacity derived offline from
    a measured minimum rather than a mean.

16. The modes that trip nothing produce either a new alert or a written decision not to
    have one. Both are answers; recording neither is not. **W9's decision may legitimately be
    "no runtime alert"**, provided the reason — a detector cannot survive inside the object it
    watches — is written down.

17. Any new alert is complete across all seven places in finding 5, and `task preflight` is
    green.

18. W9 demonstrates non-adoption **both ways** with a canary object and zero blast radius, and
    records that an unadopted alert *ceases to exist* rather than resolving.

19. W10 runs restart-with-`v1` as a control beside restart-with-`v0`, so the rename is
    separated from the counter reset it arrives with, and the `both` surface is exercised.

20. W11 states its four predictions before scaling, and either produces a number for the
    catalog page's "might not hold" or records that the deviation is unmeasurable at this
    scale. Manufacturing a number is the failure mode here.

21. **W8 cannot run by accident.** The label bomb is off by default and `--selftest` asserts
    the default render carries no such label; the script states its series ceiling, refuses a
    cardinality above it, and refuses to run at all without an explicit confirmation flag;
    the banner carries the blast-radius warning; and `LITE=1` is rejected rather than
    documented as unwise. This is the one mode that can take the monitoring stack down, so
    its guards are acceptance criteria and not prose.

22. `task preflight` is green. Anything left open carries a `⚠️` **and a phrase `task
    outstanding` actually matches** — the curated list is in `Taskfile.yml` and is not
    reproduced here; match an existing phrasing or extend the list (rule 16). An item
    phrased in new words is silently missed, which is how rule 17's own gap sat unseen.

23. ROADMAP.md item 1 is corrected where this work proved it wrong — **including its mode
    table, which gains W9, W10 and W11** (finding 6) — and its effort table carries the
    actuals, with no em dashes introduced (rule 13).

---

## Process

**W0 → W1 → W2 → W3 → W4 → W5 → W6 → W7 → W9 → W10 → W11 → W8.**

The numbering is by subject, the order above is by cost and risk, and they differ in one
place: **W8 runs last** regardless, alone, on a cluster you are willing to lose.

**W0 first, and not only because everything writes through it.** It is what lets the drills be
developed on compose — seconds to start, no cluster, instant file propagation against the
cluster's ~60s ConfigMap delay, and the same rules, simulator and dashboards. Build on
compose, confirm on kind, and the cloud targets then need a cost banner rather than new code.

⚠️ **Two environment facts the spike hit before it got that far, both cheap to design around
and expensive to debug** (2026-08-07):

- **`docker compose` can be absent while `docker` works.** The plugin is separate, and the
  `compose` target's own precondition already tests for it (`Taskfile.yml:465`). A drill that
  assumes compose is reachable because Docker is will fail in a way that reads like a stack
  problem. The whole stack is reproducible with plain `docker run` on one user-defined
  network if it comes to it, which is what `spike/stale_e2e.sh` does.
- **`colima` does not mount macOS's `/var/folders`**, so a bind mount from `mktemp -d` fails
  at container start with `not a directory` — a message that reads as a file-versus-directory
  bug and is really a mount-namespace one. Stage working files **under the repo**, which is
  inside the VM's view on both colima and Docker Desktop.

W2 before W3 because the absence result is what makes staleness interesting: you have to
show the alert cannot see it before building the thing that can. W4 reuses W2's state, so run
them in one cluster session. **W5 before W10**, because W10's control arm is a restart and
W5 is what tells you what a restart alone does. W9 and W11 depend on nothing and wait on no
`for:`; they are the two you can pick up in a gap while something else polls.

**Land it as several commits, one logical change each** (rule 11), and prefer a PR
(`CLAUDE.md`, review discipline). The harness, each drill, the simulator change and each doc
correction are separate changes with separate reasoning.

⚠️ `CLAUDE.md`'s mottoes both apply here. **Suspect the instrument before the world**: the
first time a drill reports a surprising alert state, the harness is hours old and the alert
set is months old. And **an invented number presented as a modelled one is the exact failure
this repo exists to prevent** — which is why W6 derives its capacity offline and states what
it did not model.
