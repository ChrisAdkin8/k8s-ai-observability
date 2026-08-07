# Roadmap

Where this rig goes next, and why in this order. Five items. None of them exists today.
Each one states what it adds, what it depends on, and how we would know it works.

Defects and gaps in work that already ships are not on this page. Those are marked in the
files that own them and listed by `task outstanding` (CLAUDE.md rule 16). This page is
capability the repo has never had, which is a different kind of entry, so it deliberately
stays out of that tool's phrasing.

Facts cited below were read in the files and at the URLs named, on **2026-08-06**. Where one
turns out to be wrong, correct it in the commit that finds out.

## At a glance

| # | Capability | Depends on | Rough size |
|---|---|---|---|
| **1** | **Fault injection.** Alerts fire against real broken states rather than hand-written series | five of seven modes depend on nothing; one wants item 2 | 3 to 4 days |
| **2** | **Ingest path.** Load arrives over the wire and is served, so a client can measure what the simulator claims | nothing | 3 to 4 days |
| **3** | **llm-d-inference-sim** as an external implementation the existing checks can grade | the tenant wants 2 | 2 to 3 days |
| **4** | **KEDA and Karpenter.** Autoscaling that settles on a replica count the capacity model predicted | KEDA needs 2 | 2 days, plus Karpenter |
| **5** | **A fuller sandbox.** Terraform stands up the AI platform, not just the cluster | 2 | size it after 2 |

Fault injection leads because it is the only item that is largely free of the others, and
because two of its drills are the cheapest work on this page. Item 2 is the one that changes
what the rig is, and four of the five items lean on it somewhere.

## The constraint behind items 2 to 5

The simulator does not serve requests. `scripts/llm-sim.py` synthesises arrivals internally
from `arrival_rate_rps` in the profile ConfigMap (`manifests/llm/10-profiles.yaml:64`), and
its handler implements `do_GET` only (`scripts/llm-sim.py:878`). The listen port serves
`/metrics` and nothing else.

That one fact limits most of this page:

| | Consequence |
|--|--|
| **No client-side truth** | Latency is whatever the simulator reports. Nothing outside the process measures the same request, so the numbers can only be checked against themselves. |
| **Nothing to route** | One replica, one queue, no alternative destination. Routing and scheduling logic has no surface to act on. |
| **Autoscaling has the wrong sign** | A second replica reads the same profile and generates its own full arrival rate, building its own queue. Aggregate queue depth doubles, an autoscaler sees a worse number, and it scales again to `maxReplicas`. Today the rig would demonstrate autoscaling failing. |

Item 1 is the useful exception. Breaking something and watching what the alerts do needs no
client at all, which is why it goes first.

---

## 1. Fault injection

**Depends on: almost nothing.** This is the reason it leads. Five of the seven modes below
can be produced against the tree as it stands today, with `kubectl` and the profile
ConfigMap. The exceptions are narrow and named in the table: KV cache exhaustion needs a
profile state that isolates it from saturation, which is work inside this item rather than a
dependency on another one, and a request-failure surge is the single row that genuinely wants
item 2. Nothing here waits on ingest, on llm-d-inference-sim, on an autoscaler, or on
Terraform. `llm-driven` in `manifests/llm/extras/` already exists as the tenant to target.

**What it adds.** Every alert in both rule files is already asserted in `tests/rules/`, where
promtool feeds a hand-written series and checks the alert fires. That proves the expression,
not the path. Nothing here has established that a real failure produces the series shape
those tests assume, and the two claims are far apart: one is about PromQL, the other is about
whether the platform notices when something breaks.

Saturation is the sole exception, and an instructive one. It has a fixture, because
`llm-saturated` exists precisely so the queue-depth and TTFT alerts have a real state to fire
against. Every other failure mode has a test and no state.

This is rule 18 aimed at the alert set rather than at the checks. An alert that has only ever
fired against a hand-written series is a guess about the world. The pattern to extend already
exists, since CI drives the chart's render-time assertions and `helm test`'s negative case to
failure on purpose.

### The modes, and what each would settle

| Failure mode | Should trip | Producible today? |
|--|--|--|
| Target loss: pod deleted, or the `ServiceMonitor` stops matching | `LLMMetricsAbsent`, `GPUMetricsAbsent` | **Yes, trivially.** `kubectl delete pod`. It has never been done |
| **Stale but up**: scrape succeeds, counters frozen | **nothing** | Yes, and this is the sharp one. `absent()` cannot see it, and `rate()` reads zero, which is indistinguishable from an idle tenant |
| Partial outage: one tenant dies, the other is healthy | should be visible per `model_name` | Yes. Whether an aggregate panel hides it is exactly the kind of thing this rig can grade |
| Counter reset: pod restarts mid-window | nothing should misfire | Yes. `rate()` handles resets; recording rules stacked on `rate()` across a restart are less obvious |
| Cardinality explosion: a label value per request, or per LoRA adapter | nothing | Yes. A genuine AI-platform outage mode, and the one most likely to take Prometheus with it |
| KV cache exhaustion, distinct from queue backlog | `LLMKVCacheSaturated` | Partly. The profile drives cache usage, but no state isolates it from saturation. That state is work inside this item |
| Request-failure surge: `finish_reason` turning to error | **nothing** | Partly, and this is the one row that wants item 2. See below |

Two rows trip nothing at all. That is a result the rig can demonstrate rather than assert,
and "there is no alert for this" is a legitimate grade to hand out. It is the same service
the repo already performs for dashboards.

**Stale but up is the one to build first.** It is cheap to produce, by freezing the
simulator's counters while it keeps serving `/metrics`. It defeats the absence alerts that
exist. And its correct detector, staleness on a counter rather than absence of a target, is
a real piece of observability engineering that transfers straight to production.

### The one dependency, and how to sequence around it

⚠️ **A request-failure surge cannot be fully expressed until item 2 lands.** `finish_reasons`
already models a 1% abort (`llm-sim.py:252`), so the series is producible and no alert
watches it. What is missing is the ability to tell two statements apart: "the request failed"
and "the simulator recorded an abort" are the same statement while there is no client.
Client-side truth is what separates them.

That does not block this item. Do the other six modes now, and treat the failure-surge drill
as the drill that gets written twice: once against the simulator's own account, and again
against a client once item 2 exists, where the second version is the one that means anything.

### Done when

- Each mode is a scenario script you invoke, in the `drive-llm-load.sh` mould, and not in
  `verify.sh`, which takes invariants only (rule 7). A failure drill is a finding with a
  shelf life by definition.
- Each script states the expected alert, and the expected silence, before it runs (rule 6),
  and polls rather than single-shots (rule 5).
- The modes that trip nothing produce either a new alert or a written decision not to have
  one. Both are answers. Discovering it and recording neither is not.
- Fixtures are untouched. Every drill targets `llm-driven` in `extras/` (rule 1).

---

## 2. An ingest path the simulator owns

**Depends on: nothing.** It is what items 4 and 5 wait for, what item 3's tenant needs to be
worth much, and what makes item 1's last drill honest.

**What it adds.** Load arrives over the wire and is served, rather than being conjured inside
the process. Concretely: a request handler beside the existing `do_GET`, with streaming so a
client can time first-token itself, and an open-loop generator that offers a fixed rate
regardless of how fast responses come back.

**Build it in `llm-sim.py` rather than adopt it.** The obvious shortcut is to reach for
[`llm-d-inference-sim`](https://github.com/llm-d/llm-d-inference-sim) (item 3), which already
serves requests, and call the gap closed. That trades away the thing the rig runs on:

| | Ingest in `llm-sim.py` | Adopting a serving binary |
|--|--|--|
| Cost | a handler beside the one that exists | a third-party image, a new pin, a new failure surface |
| House rule | stays stdlib-only, a standing rule in `CONTRIBUTING.md` | a compiled dependency |
| **Ground truth** | **closed form: the capacity arithmetic predicts latency, queue depth and the saturation point** | **its internals are not modelled here** |

The last row decides it. The rig can grade its own simulator because
`manifests/llm/10-profiles.yaml` derives what those numbers must be. Against a binary whose
behaviour is not derived here, a disagreement tells you that two things differ, not which one
is wrong.

### What this buys beyond autoscaling

It gives us the first check of what the simulator reports against what actually happened on
the wire. Everything the rig asserts about latency today is the simulator's own account of
itself, checked against arithmetic the same process implements. A client that times its own
request introduces a measurement the simulator did not produce.

Be precise about the scope of that. It does not newly reveal the quantile-estimation error.
`histogram_quantile` interpolates inside a bucket, and the size of that error is already
known here analytically, without any client: `manifests/alerts/llm-prometheusrule.yaml:119`
records prefill reading 3.03x high (0.095s reported as 0.285s), and `:209` adds e2e at 1.71x
under saturation and ITL at 1.08x. What a client catches is the layer beneath that, namely
whether the observations going into the histogram match the request the client actually made.
Nothing in the rig can see that today.

### Two constraints on the design

- **The fixtures do not change.** Rule 1: `llm-steady` and `llm-saturated` hold the states
  `verify.sh` asserts. Internal synthesis stays the default and stays exactly as it is.
  Ingest is opt-in and belongs to `llm-driven` in `manifests/llm/extras/`.
- **Do not divide `arrival_rate_rps` by the replica count.** It makes queue depth respond to
  replica count by construction, so an autoscaler appears to work while the answer has been
  hard-coded into the fixture. That is undetectable from the dashboards and is precisely the
  confidently-wrong pattern the rest of the repo exists to catch. Real ingest removes the
  temptation, because a Service distributes offered load without being asked to.

### Done when

- A client can drive `llm-driven` at a chosen rate and time first-token itself.
- Client-measured TTFT and the simulator's own histogram are compared, with the expected
  divergence written down before the comparison runs (rule 6).
- `llm-steady` and `llm-saturated` still pass `verify.sh`'s LLM checks and `--selftest`.
  Not "identical output": both fixtures set `"seed": null` (`10-profiles.yaml:74`, `:102`)
  and `llm-sim.py:445` leaves the RNG unseeded, so they are non-deterministic on purpose and
  only their asserted invariants can be held fixed.

---

## 3. llm-d-inference-sim as a graded second opinion

**Depends on: item 2 for the tenant, nothing for the checks.** The bucket comparison and the
upstream report can be done today. Running it as a tenant worth pointing load at wants item
2's load generator.

[`llm-d-inference-sim`](https://github.com/llm-d/llm-d-inference-sim) is the llm-d project's
GPU-free vLLM stand-in: Go, published at `ghcr.io/llm-d/llm-d-inference-sim`, serving
OpenAI-compatible HTTP and vLLM-compatible gRPC on one port, and exposing vLLM-named
Prometheus metrics on `/metrics`, including `vllm:num_requests_running`,
`vllm:num_requests_waiting`, `vllm:time_to_first_token_seconds` and
`vllm:e2e_request_latency_seconds`, which this repo's rules key on.

It is not the ingest path and it is not a replacement. It is the first external
implementation the existing checks can be pointed at, which is the only kind of question this
repo has ever existed to answer.

### What we already know

A container probe on 2026-08-06 (`ghcr.io/llm-d/llm-d-inference-sim@sha256:7f3a1f72`,
`--mode random`, default config, two chat completions, no cluster) settled the compatibility
question their docs are silent on. The answer is yes, and it reshapes the work below:

| Finding | What it does to the work |
|--|--|
| Every shared histogram's bucket boundaries match this repo's transcription: TTFT 22 boundaries, the per-token pair 19, and e2e, prefill, decode and inference 21 each | The recording rules can point at it unmodified. What remains is a check that catches the day that stops being true |
| There is no `2.0` in their TTFT list either | Rule 4's SLO trap is a shared property rather than a local one. An `le="2"` threshold reads green forever against their binary too, which is a portable finding about a tool other people build on |
| `vllm:time_per_output_token_seconds` and `vllm:inter_token_latency_seconds` are emitted together, with identical `_sum`, `_count` and buckets | A superseded name beside its own replacement. This is the finding worth reporting upstream |
| Their docs list `prefix_cache_hits`, `prefix_cache_queries` and `request_queue_time_seconds`; the binary emitted none of them | Possibly config-gated rather than absent. Re-run with those features exercised before telling anyone |
| Default config produces zero latency: 28 per-token observations summing to 0 | Point a dashboard at it and every latency panel is empty until the latency knobs are set. A trap for exactly the person the Grafana catalog sends there |

### The duplication is worth an upstream issue

The repo treats the v0 to v1 transition as an explicit, pinned choice: `config.sh:234` sets
`LLM_VLLM_VERSION="v1"`, `llm-sim.py:148` tabulates the renames, and `llm-sim.py:145` gives
the reason. That archaeology is already done here with citations, which is why a short probe
could read the result rather than guess at it.

V1 changed three things, and the running binary answers two of them as v1 and the third as
both generations at once:

| V0 to V1 change | `llm-sim.py` | Emitted by the binary |
|--|--|--|
| `gpu_cache_usage_perc` to `kv_cache_usage_perc` (`:130`) | v1 | `kv_cache_usage_perc` only, so v1 |
| `gpu_prefix_cache_hit_rate` gauge to two counters (`:154`, a reshape rather than a rename) | v1 | neither appeared at default config |
| `time_per_output_token_seconds` to `inter_token_latency_seconds` (`:135`) | v1 | **both, with identical `_sum`, `_count` and buckets** |

The discriminator is sharp. `llm-sim.py:138` records that current vLLM does carry a second
per-token series, but it is `request_time_per_output_token_seconds`, a per-request mean,
"not the same number and should not be aliased onto this histogram". Neither project emits
that name. So the pair on the wire is not the legitimate current-vLLM pair; it is a
superseded name beside its own replacement, carrying the same numbers.

The consequence lands on their users, not ours: a dashboard built against llm-d-inference-sim
using `time_per_output_token_seconds` will find that series missing on real vLLM v1. The
simulator is faithful on every bucket boundary and unfaithful on this one name, which is the
hardest kind of divergence to notice, because everything renders.

### The work

A one-off probe is not a check (rule 18). The bucket comparison becomes a third leg in
`check-vllm-buckets.py`, beside upstream source and this repo's transcription, so the day
their boundaries drift is a red CI run rather than a discovery. The comparison logic is
written and proven against a live binary. What remains is running the container in CI and
deciding whether that belongs in the weekly drift job or the per-PR one.

Then a tenant. Their config keys and this repo's profile keys line up, and the row that does
not is the point, and is also why the tenant waits for item 2:

| `manifests/llm/10-profiles.yaml` | `llm-d-inference-sim` config |
|--|--|
| `base_ttft_seconds` | `time-to-first-token` |
| `base_itl_seconds` | `inter-token-latency` |
| `max_concurrency` | `max-num-seqs` |
| `arrival_rate_rps` | **no equivalent: load is offered, not declared** |

### Done when

- The three-way bucket comparison runs in CI beside the existing drift canary, and its
  expected output was written down first.
- That comparison is a check that can go red, not a probe someone ran once. Drive it to
  failure against a deliberately altered boundary before trusting it (rule 18).
- The three doc-versus-binary gaps are re-run with prefix caching and queueing actually
  exercised, so "absent" is distinguished from "config-gated" before anyone is told.
- The per-token duplication is raised upstream, with the version archaeology attached.
- It runs as a tenant in `manifests/llm/extras/`, scraped by the existing `ServiceMonitor`,
  with the `llm:*` recording rules unmodified. The rules are pinned to v1 (`config.sh:234`)
  and the binary emits the v1 names, so this is a fair test: boards lighting up is the
  compatibility claim proven, boards staying dark is the finding.

---

## 4. KEDA and Karpenter: autoscaling with a fixed point

**Depends on: item 2 for KEDA, nothing for Karpenter's first question.** Neither appears
anywhere in the tracked tree today. Without external ingest, a ScaledObject here demonstrates
runaway feedback, for the reason set out above.

### The proof is convergence, not motion

Replicas rising is not evidence that a metric is a control signal, because a runaway loop
also makes replicas rise. Replicas holding still at the value the arithmetic predicted is the
proof. This rig can assert that in a way most autoscaling demos cannot, because the capacity
model already says how many replicas a given arrival rate needs. Write the expected replica
count into the check before running it (rule 6), and poll rather than single-shot it
(rule 5).

### Karpenter is a different problem, and its first question is cheap

`kind/gpu-sim.yaml` declares a single control-plane node, and Karpenter has no cloud provider
to call there. The only local path is the
[kwok provider](https://github.com/kubernetes-sigs/karpenter/tree/main/kwok), which needs an
image built and pushed and no existing Karpenter install. That is a real cost line, not a
`helm install`.

On EKS the risk is money. The obvious sandbox design is a NodePool restricted to cheap CPU
instance families, whose node template carries `run.ai/simulated-gpu-node-pool`, so the fake
GPU operator dresses each new node and `nvidia.com/gpu` appears without a GPU bill. The open
question is whether Karpenter will provision at all under that design, because it schedules
against its own model of what an instance type offers, and the extended resource only exists
after a third-party operator has run on a node that does not exist yet. It resolves two ways,
and they are very different bills:

- it provisions nothing, and the pod stays `Pending`; or
- it shops for an instance type that really does advertise `nvidia.com/gpu`, and bills for
  it.

One Pending pod answers that, with no cluster spend, and it should happen before any
Terraform is written. The kwok provider may turn out to be the only honest local path, since
it lets fake instance types be declared directly.

The GKE counterpart is node auto-provisioning. `terraform/gke/main.tf:104` deliberately runs
Standard rather than Autopilot, so the analogue exists and cross-cloud parity stays
achievable.

### Done when

- Karpenter's first question has an answer written down, with the cost of each branch.
- A ScaledObject drives `llm-driven` to a replica count the capacity arithmetic predicted in
  advance, and holds there across a polled window.
- `verify.sh` gains the convergence check only if it is an invariant. A result that holds
  only at these settings goes in a scenario script (rule 7).

---

## 5. A fuller provisioned sandbox

**Depends on: item 2, and on item 4's first question for the largest candidate.** Today
Terraform stands up a bare cluster and `install.sh` deploys everything else into it. "More
fully featured" means Terraform grows to provision the AI platform surface itself. This is
the item most at risk of turning into a wish list, so it gets an admission rule:

> Nothing is added that cannot be graded. The simulator is the instrument, verification is
> the product. A component with no ground truth is a component this repo has no reason to
> host.

Candidates scored on that test alone, including the ones that fail it, because a rule with no
rejections has no teeth:

| Candidate | Ground truth | Gradeable? |
|--|--|--|
| Karpenter IAM and interruption queue (EKS), node auto-provisioning (GKE) | a Pending pod becomes Running, a node appears, and no GPU instance was billed | yes, after item 4's first question |
| GPU node pools with the taints and labels the fake operator expects | the pool name must match on three sides, and the chart already asserts two of them at render time | **yes** |
| Inference gateway and request routing | the rig knows every replica's queue depth, so "did it pick the shorter queue" is decidable | yes, after item 2 |
| Remote-write to a managed metrics backend | the same rules must evaluate identically off-cluster | **yes** |
| KV-cache-aware routing | a cache-aware router must beat round-robin on prefix-cache hit ratio, which is a known-direction A/B | yes, after item 3 |
| Quota and preemption | PriorityClass ordering is deterministic, so which pod was evicted is decidable | **yes** |
| Cost modelling from price lists | published prices | **no.** It imports a number that drifts under someone else's control, with nothing here to check it. That is rule 12's failure class, bought deliberately |
| A real tiny model on real hardware | real | **no.** It ends the GPU-free premise, which is the reason any of this runs in CI |

### The line that decides the design

`terraform/modules/contract` holds cross-cloud identity constants only, and sizing stays in
the roots (`CLAUDE.md`). Every newly provisioned component has to pick a side of that line.
Karpenter is the first one where the answer is not obvious: its IAM roles and interruption
queue are identity-shaped, its instance families are sizing-shaped, and they arrive together.

### Done when

Each accepted candidate lands with its grading check written before its Terraform, and the
EKS and GKE roots stay honest about where they differ (`docs/architecture.md` owns that
list).

---

## Sequencing

```
item 1, target-loss drill ──── independent, `kubectl delete pod`, proves the absence alerts
item 1, stale-but-up drill ─── independent, and it defeats the alerts that exist
item 3, surface question ───── DONE 2026-08-06, one container, ~20 min
item 3, bucket comparison ──── logic proven against a live binary. Remains: make it a
                               check that can go red, and choose which CI job owns it
item 4, Karpenter question ─── independent, one Pending pod, do it whenever

item 2 (ingest path in llm-sim.py)
   ├── unblocks ── the request-failure drill, told apart from an abort (item 1)
   ├── unblocks ── KEDA with negative feedback                          (item 4)
   ├── unblocks ── anything that routes                                 (item 5)
   └── unblocks ── disruption drills with real clients                  (item 5)

item 3, the tenant ── wants item 2's load generator to be worth much
```

The numbering now tracks the running order more closely than it used to, but it is still not
a schedule. Item 1 leads because it depends on least and pays out fastest. Item 2 is numbered
second and is the largest single change to what the rig is. The cheap independent questions
in items 3 and 4 are what you would pick up on a Monday while something else is running.

## Three costs every item has to price

These are invisible in a feature list and have historically been the difference between a
two-day estimate and a five-day one:

| | The question |
|--|--|
| **CI budget** | Four required checks, two kind legs, one control-plane node, and `LITE=1` fitting a 4 GiB runtime. A third tenant plus an autoscaler plus a load generator is a resource question before it is a feature. Timings live in `docs/ci.md`. |
| **The chart** | Does it ship in the published chart, and does it work on a BYO cluster? Registry versions are immutable, so a mistake stays published rather than being replaced. |
| **The pins** | Here the machinery is weaker than it looks. Rule 8's twice-stated, cross-checked pin covers chart dependencies: `KPS_CHART_VERSION` and `FAKE_GPU_CHART_VERSION` in `scripts/config.sh`, against `Chart.yaml`. A third-party image tag has no such cross-check, so it needs a row in `docs/versions.md` and a deliberate decision about what notices when it goes stale. |

## Effort

Estimates from reading the code, not from doing the work. Treat the ordering as firmer than
the numbers, and re-derive the largest line before planning around it. Instruments are priced
as code plus 25 to 35% verification, because the selftest is where these overrun.

⚠️ **The first line is a warning about the rest of them.** The surface question was priced at
a day and took twenty minutes, because the estimate assumed desk research where a container
run was available. Before planning around any line here, ask whether the cheap empirical
version exists. This page has been wrong about that once already.

| | Estimate |
|--|--|
| 3 settle the surface question | **~20 min, actual.** Priced at a day |
| 1 target-loss drill, the absence alerts end to end | ~2 hours |
| 4 Karpenter's first question (one Pending pod) | ~1 hour |
| 3 fold the bucket comparison into `check-vllm-buckets.py` as a third leg, and drive it red | ~half a day |
| 1 stale but up: produce it, show it defeats the absence alerts, then detect it | ~1 day |
| 1 the remaining modes, one scenario script each | ~half a day each |
| 2 ingest handler with streaming, plus selftest | ~2 to 3 days |
| 2 open-loop load generator | ~half a day |
| 2 client-versus-instrument TTFT comparison | ~1 day |
| 1 the request-failure drill, rewritten against a client once item 2 lands | ~half a day |
| 3 llm-d tenant in `extras/`, rules pointed at it unchanged | ~1 day |
| 3 re-probe the three doc-versus-binary gaps with the features enabled, then report upstream | ~half a day |
| 4 KEDA ScaledObject, equilibrium tuning, convergence checks | ~2 days |
| 4 Karpenter beyond the first question | depends entirely on that answer |
| 5 | size it once item 2 lands |

## Non-goals

- **A benchmark suite.** Names, types and bucket boundaries transfer to real vLLM. Absolute
  values do not, and publishing them as though they did is the exact failure this repo exists
  to prevent.
- **Real GPUs, real weights, or real spend in CI.**
- **Retuning the fixtures to suit new work.** Rule 1. New behaviour gets a new tenant.
- **A TODO file.** Rule 16, and the second paragraph of this page.

Work specifications live in `prompts/`, tracked since 2026-08-06, written one item ahead.
Implementing an item is what makes the next one's spec honest.
