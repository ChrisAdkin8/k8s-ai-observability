# Prompt: A correct autoscaling testbed — KEDA with real negative feedback

## Role & Objective

You are a Kubernetes platform engineer working in the `k8s-ai-observability` repo. Four
pieces of work:

1. **W1** — give the simulator an **ingest path**, so load *arrives at* a replica instead
   of being synthesised *inside* it — including **streaming**, which this file owns (see
   W1.3).
2. **W2** — KEDA, opt-in, with a ScaledObject whose metric has a **fixed point**.
3. **W3** — prove convergence in `verify.sh`. Replicas rising is not the proof; **replicas
   holding still is.**
4. **W4** — deployment paths: EKS and GKE must deploy **exactly as they do today** by
   default, and **with KEDA** on request.

**Why.** The simulator does not serve requests. It *synthesises* arrivals internally from
`arrival_rate_rps` in its profile ConfigMap, and its Service exposes exactly one port —
metrics. So a horizontal autoscaler placed on top of it today has **positive feedback**:
KEDA sees a deep queue, adds a replica, the new replica reads the same profile, generates
its *own* full arrival rate, and builds its *own* queue. Aggregate queue depth doubles.
KEDA sees a worse number and scales again, to `maxReplicas`.

That makes the current rig a demonstration of autoscaling failing. This work makes the
loop close: load is offered once, distributed across replicas, and the scaling signal has
an equilibrium — which is the only thing that makes a metric a control signal rather than
a coincidence.

⚠️ **The shortcut that must not be taken: dividing `arrival_rate_rps` by replica count.**
It makes queue depth respond to replica count *by construction*, so KEDA appears to work
while the answer has been hard-coded. That is precisely the confidently-wrong pattern the
rest of this repo exists to catch.

### Effort, and where to stop

Estimates from reading the code, not from doing the work. **Treat the ordering as firmer
than the numbers**, and re-derive the largest line before planning around it.

| | Estimate | Standalone? |
|--|--|--|
| W1 ingest path in `llm-sim.py`, incl. SSE port from the drill | ~2–3 days | yes — the rig is more honest with it even without KEDA |
| W1.5 open-loop load generator | ~half a day | needs W1 |
| W2 KEDA install + ScaledObject + **equilibrium tuning** | ~1 day | needs W1 to be meaningful |
| W3 `verify.sh` convergence checks incl. L14 | ~1 day | needs W1 + W2 + a cluster |
| W4.1 chart + `install.sh` opt-in | ~half a day | needs W2 |
| W4.2 terraform node autoscaling | ~1 day | **optional — see W4.2** |
| Docs + CHANGELOG | ~half a day | continuous |

**W1 + W3 are the deliverable.** W1 alone is worth shipping: an ingest path makes the
simulator honest about where load comes from, and it is the prerequisite for the
disruption drill's second finding. **W2 without W3 is not worth shipping** — a
ScaledObject that scales is not evidence that it scales *correctly*, and this repo does
not ship unverified claims.

⚠️ **The table still needs verification priced as its own line.** House rule is code **plus
25–35% verification**, on the grounds that the selftest is where estimates overrun. W3 is a
verification item and is costed, but the instrument lines above it are not split, so the
allocation cannot be read off the table or checked against it. Re-derive with implementation
and verification separated before planning around the total. Raised by CodeRabbit on PR #39.

## Background / Facts

Every fact below was read directly in the file cited, on 2026-08-03. Where one turns out to
be wrong anyway, correct it in your commit message.

⚠️ **Read `CLAUDE.md` first — it carries the repo's standing law and this file does not
repeat it.** Only facts specific to *this task* are below. Where a rule there governs a
decision here, it is cited by number rather than restated: a fact stated twice is a fork
waiting to disagree, which is the failure `scripts/check-doc-claims.py` exists to catch.

### ⚠️ The metrics already exist — VERIFIED (`scripts/llm-sim.py:718-720`)

```python
gauge("vllm:num_requests_running", len(self.running), ...)
gauge("vllm:num_requests_waiting", len(self.queue), ...)
```

Both gauges are already emitted, per pod, with the correct vLLM names. **No new metric is
required for W2** — only a load path that makes them respond to replica count, and a
choice between them that W2.2 gets right.

### ⚠️ The Service has no ingest port — VERIFIED (`manifests/llm/20-simulators.yaml:183`)

```yaml
- { name: llm-metrics, port: 9401, targetPort: llm-metrics }
```

One port, and it is the scrape endpoint. There is nowhere for a request to arrive. Note
also the Service's selector: `app.kubernetes.io/name: llm-sim` — **every** sim component,
both fixtures included. See W1.2.

### ⚠️ Load is a property of the pod, not of the traffic — VERIFIED (`manifests/llm/10-profiles.yaml`, `scripts/llm-sim.py:229`)

`arrival_rate_rps` defaults to `1.8` and is read from the mounted profile. The simulator
polls that ConfigMap every 10s and applies changes **without restarting**, so counters and
histograms stay continuous. Every replica of a Deployment mounts the same ConfigMap and
therefore generates the same rate independently.

### ⚠️ The fixtures, and the one number about them this task turns on

`CLAUDE.md` rule 1 governs: the two fixtures are never scaled or driven, and `llm-driven`
(`manifests/llm/extras/llm-driven.yaml`) is the only dynamic tenant. **The ScaledObject
targets it and nothing else.**

The task-specific part: `llm-saturated`'s queue is **pinned at 160 by design, forever** —
that is its whole job as a fixture. It is also, therefore, a permanent floor under any
unfiltered query over `vllm:num_requests_waiting`. See W2.2's TRAP 2.

### ⚠️ The capacity arithmetic is load-bearing — VERIFIED (`manifests/llm/10-profiles.yaml`)

```
itl_full  = 0.015 x 1.5 = 0.0225
capacity  = 16 / (0.08 + 256 x 0.0225) = 2.74 rps per replica
per-request service time ≈ 0.08 + 256 x 0.0225 = 5.84 s
steady    1.8 rps = 0.66x capacity -> queue ~0
saturated 6.0 rps = 2.19x capacity -> queue pins at 160, TTFT plateaus ~58s,
                                      total request duration ~64s
```

These are the inputs to W2.2's equilibrium arithmetic, and W3's expected replica count must
be derived from them **before** the first run. Per `CLAUDE.md` rule 3, tune against
`llmsim_capacity_rps` rather than re-deriving from the base-latency figure.

### ⚠️ `install.sh` accepts exactly one flag — VERIFIED (`scripts/install.sh:30-37`)

```bash
case "${2:-}" in
  "")                 ;;
  --skip-monitoring)  SKIP_MONITORING=1 ;;
  *) echo "ERROR: unknown argument '${2}'" >&2 ; exit 1 ;;
esac
```

**One flag only** — W4.1 needs a second, which means a loop over `"$@"`. `CLAUDE.md` rule 10
governs what must survive that change: positional, with unknown arguments still rejected.

### ⚠️ Neither cloud has node autoscaling today — VERIFIED (`terraform/eks/main.tf:103-105`, `terraform/gke/main.tf:174`)

```hcl
# EKS
min_size     = var.node_count
max_size     = var.node_count
desired_size = var.node_count

# GKE
node_count = var.node_count # per zone
```

Fixed size in both — nothing changes `desired_size`, so W4.2's variables have no effect
without a node autoscaler to drive them. `CLAUDE.md` rule 15 covers the per-zone/absolute
asymmetry those variables inherit, and the map entry for `terraform/modules/contract` covers
why sizing does not belong there.

## W1 — The ingest path

**W1.1 Two load sources, selected by profile.** Add a profile key — `"load_source"`, values
`"synthetic"` (default) and `"ingest"`. Default preserves today's behaviour byte-for-byte,
so both fixtures and every existing `verify.sh` assertion are untouched. `llm-driven` opts
into `"ingest"`.

Validate the new key the way `arrival_rate_rps` is validated (`scripts/llm-sim.py:282-302`),
with a `ProfileError` naming the bad value. (Profiles are JSON — `CLAUDE.md` rule 2.)

**W1.2 A second port — and its own Service.** `llm-ingest` on 9400, added to the container
and the `llm-driven` Deployment.

⚠️ **Do not add the ingest port to the shared `llm-sim` Service.** Its selector spans every
sim component, so the load generator would spray requests across the fixtures too —
perturbing the exact states `verify.sh` asserts. Create a dedicated Service selecting only
the driven component. This Service is also the object the disruption drill's
`stream-target` repoints to at retirement (`prompt-disruption-drill.md` W2.1), so the two
files meet here by name.

**W1.3 A minimal, shape-compatible endpoint — including streaming, which is owned HERE.**
`POST /v1/completions` accepting at least `prompt_tokens` and `max_tokens`, so request cost
varies per request. Enqueue through **the existing queueing model** — the same
`capacity_rps` arithmetic, the same histograms, the same phase decomposition.

Two response modes:

- **Non-streaming** (default): respond after the modelled latency has elapsed.
- **`stream: true`**: `text/event-stream`, first event after the modelled TTFT, one event
  per token at the modelled ITL, `data: [DONE]` terminator, **per-event flush**, and a
  `SIGTERM` path that drains open streams. **Port these mechanics from
  `scripts/stream-sim.py`** — the disruption drill lands first and its responder is the
  donor.

⚠️ **This ownership statement is deliberate and load-bearing.** An earlier revision of this
file deferred SSE to the drill, while the drill deferred the queueing model to this file —
so the drill's *finding 2* (load-dependent truncation: safe at 1.8 rps, truncates at 6.0)
was orphaned between the two. It is not orphaned now: **streaming-behind-the-queueing-model
lands in this work.** Without it, repointing `stream-target` at the real simulator collapses
the drill's outcome C into outcome D (a connection cut mid-wait has delivered zero bytes),
and the strongest finding either file promises becomes unmeasurable.

⚠️ **Not a general OpenAI implementation.** Shape-compatible enough that a standard load
generator can drive it; nothing more. See Non-goals.

**W1.4 ⚠️ The accept path, and it is the one that will bite.** The simulator uses
`ThreadingHTTPServer`, which is `ThreadingMixIn` — **there is no thread pool to exhaust; it
spawns an unbounded thread per connection.** The two real limits are elsewhere:

- **`socketserver.request_queue_size` defaults to 5.** That is the kernel accept backlog.
  Connections parked there are invisible to the application, so
  `vllm:num_requests_waiting` under-reports exactly when the queue matters most — and that
  is the metric KEDA reads. Set `request_queue_size` explicitly above `max_in_flight` and
  **assert it in the selftest**.
- **Reject beyond `max_in_flight` at accept**, with a 503 and a counter, so the gauge's cap
  matches the model's cap and nothing queues invisibly.

  ⚠️ **Unverified: "at accept" is ambiguous here, and the two readings differ.** The
  simulator runs `ThreadingHTTPServer` (`scripts/llm-sim.py:70`), which accepts the socket
  before the handler runs — so a 503 returned from `do_POST` is admission control *after*
  TCP accept, not at it. `request_queue_size` is the kernel listen backlog and caps
  something else entirely. If the only rejection is a handler-side 503, connections can sit
  in the backlog while `vllm:num_requests_waiting` under-reports them, and the gauge KEDA
  scales on is exactly the one that goes quiet. Settle it in the spike before W2 tuning: an
  application-level semaphore taken before enqueue, or a stated and asserted guarantee that
  accepted requests cannot exceed `max_in_flight`. Raised by CodeRabbit on PR #39.
- At saturation every queued request holds its connection — and its thread — open for up to
  **~64s** (58s queue wait + 5.8s generation), so the process carries in-flight + queued ≈
  176 blocked threads. Python holds that, but **~200 GIL-sharing threads add wakeup jitter
  to the modelled TTFT/ITL**. Say so in the docs: wire timings from the ingest path carry
  jitter under load, which is one more reason the histograms — not the wire — remain the
  source of truth.

**W1.5 An open-loop load generator.** A Deployment or Job that offers a fixed rps at the
dedicated Service **regardless of response time**.

⚠️ **Closed-loop generators cannot saturate this rig.** A generator holding N concurrent
requests self-throttles: as the server slows, it sends less, so the queue can never exceed N
and `saturated` is unreachable.

⚠️ **HTTP keepalive defeats kube-proxy.** kube-proxy load-balances per *connection*, not per
request. A keepalive client pins to one pod, so one connection sends 100% of load to one
replica no matter how many exist — and the convergence you are trying to demonstrate never
appears. The generator must open many connections or disable keepalive. Say so in the
manifest comment; this bites real deployments with HTTP/2 and gRPC and is worth documenting
where someone will read it.

## W2 — KEDA

**W2.1 Chart dependency.** Third entry in `Chart.yaml` with `condition: keda.enabled`,
`keda.enabled: false` in `values.yaml`, pinned version mirrored into `config.sh` as
`KEDA_CHART` / `KEDA_CHART_VERSION` with the same `# verified <date>` comment convention.
Add it to `values.schema.json` alongside the existing toggles.

**W2.2 The scaling metric, and the equilibrium it must have.** KEDA drives an HPA external
metric with `AverageValue` semantics: `desiredReplicas = ceil(metricValue / threshold)`.
Three traps live here, and the first invalidates the obvious design.

⚠️ **TRAP 1 — `vllm:num_requests_waiting` alone has no fixed point on this rig. It is a
relaxation oscillator, not a control signal.** The simulator has a hard capacity knee:
below capacity the queue is ~0; above it, the queue runs to its cap. Walk the file's own
numbers — 6 rps offered, 2.74 rps per replica, threshold 5:

```
1 replica:  queue pins at 160     -> desired = ceil(160/5) = 32 -> clamp to maxReplicas
8 replicas: capacity 21.9 rps ≫ 6 -> backlog drains in seconds -> queue 0
            desired = ceil(0/5) = 0 -> clamp to minReplicas 1
~5 min later (HPA downscale stabilization expires): 1 replica, queue explodes, repeat.
```

Period ~six minutes, indefinitely. **There is no waiting-only threshold below the cap that
yields a stable count**, because every replica count that can serve the load has an empty
queue. And the original acceptance checks all pass on this oscillator — replicas rose,
per-replica queue fell, goodput rose — which is why L14 exists (W3).

**The signal with a fixed point is `vllm:num_requests_running`.** By Little's Law, in-flight
= arrival × service time = 6 × 5.84 ≈ **35, independent of replica count** (once nothing
queues). With threshold ≈ 11 (~0.7 × `max_concurrency` 16):
`desired = ceil(35/11) = 4` — and at 4 replicas the metric still reads 35, so desired stays
4. A fixed point. Use the composite so overload still adds urgency:

```
sum(vllm:num_requests_running{model_name="<driven>"})
  + sum(vllm:num_requests_waiting{model_name="<driven>"})
```

**Compute the expected converged count from the profile arithmetic before the first run**
— `ceil(λ·W / threshold)` — and write it into L14's comment.

⚠️ **TRAP 2 — no selector means the fixtures drive the loop.** `llm-saturated`'s queue is
pinned at 160 **by design, permanently** — it is a `verify.sh` fixture. An unfiltered
`sum(vllm:num_requests_waiting)` therefore never drops below 160, and KEDA pins
`llm-driven` at `maxReplicas` forever, driven entirely by a deployment it does not scale.
This is the repo's own silent-selector genre — the PromQL `or` trap, the `le="2"`
matches-nothing trap — arrived in a ScaledObject. **Filter every term by the driven
tenant's `model_name`, read from `manifests/llm/extras/llm-driven.yaml` — do not guess
it.** L13 watches fixture *replica counts* and would never catch this.

⚠️ **TRAP 3 — `sum()`, not `avg()`.** `avg()` divides by the replica count the HPA is
about to change: at 1 replica it demands 32, at 32 it demands 1. `sum()` is the correct
aggregation for `AverageValue` semantics. (The earlier revision's table illustrating this
imagined a "stable at 32 replicas, queue 5 each" state — arithmetic that TRAP 1 shows the
physics never visits. The sum-vs-avg point stands; that equilibrium does not.)

**W2.3 Scale-down and in-flight work.** Set `cooldownPeriod` and the HPA downscale
stabilization window deliberately — the stabilization window is also the damper on any
residual oscillation, so record the chosen value and why. Set
`terminationGracePeriodSeconds` on `llm-driven` above the modelled request duration: a
scale-down that kills a pod mid-request is a dropped generation, and at saturation the
modelled duration is **~64s** against a Kubernetes default of 30. This is the seam the
disruption drill measures in detail.

## W3 — Proving convergence

Replica count rising proves KEDA is wired up. It proves nothing about correctness — the
oscillator in W2.2 TRAP 1 passes L10–L12. Add to `verify.sh`, in the numbered-check style
already there:

- **L10 — it scales.** Under sustained offered load above `llmsim_capacity_rps`, replicas
  rise above 1 within a bounded time. Poll — `CLAUDE.md` rule 5.
- **L11 — per-replica queue depth falls.** `avg` of the driven tenant's
  `vllm:num_requests_waiting` after convergence is materially below its single-replica
  value. ⚠️ Passes trivially during an overshoot — necessary, not sufficient. L14 is the
  real test.
- **L12 — goodput actually rose.** Completed requests per second at N replicas exceeds
  that at one. Guards against replicas that absorb queue without serving.
- **L13 — the fixtures are untouched.** `llm-steady` and `llm-saturated` still hold
  `replicas: 1` and their asserted states.
- **L14 — stationarity. The check that catches the oscillator.** At constant offered load,
  the replica count holds within ±1 across **at least two consecutive downscale
  stabilization windows**, and equals the count pre-computed in W2.2. A testbed that
  cannot pass L14 has a scaling *demo*, not a scaling *signal*.

⚠️ **Where these run, and what they cost.** Driving load above capacity and waiting out two
stabilization windows is **minutes** — the existing kind CI legs are ~5.5 minutes total,
so L10–L14 cannot land on `full`/`lite` unconditionally. Gate them on detection (a
ScaledObject targeting `llm-driven` exists — the same pattern as `--byo` detection), and
run them in CI on a **third matrix leg** (`keda`) that installs with the flag and applies
the extras. On a cluster without KEDA the checks must skip loudly, not fail.

⚠️ **Write the expected values into each check's comment before running it** (`CLAUDE.md`
rule 6) — and for L14 that means the converged replica count computed in W2.2, not one
observed afterwards.

## W4 — Deployment paths

**W4.1 Opt-in at two layers, and the default is today's behaviour.**

⚠️ **Every line below except the first describes the contract AFTER W4.1, not one you
can run today.** `install.sh` parses `${2:-}` only and rejects anything it does not
recognise (Background, `scripts/install.sh:30-37`), so today it does not know
`--with-keda` at all, and the third line would exit on the *first* flag without ever
reaching the second. The loop over `"$@"` further down is the change that makes these
valid. Do not lift them into an acceptance check before it lands.

```
./scripts/install.sh eks                  # exactly as it behaves today
./scripts/install.sh gke --with-keda      # + KEDA + ScaledObject
KPS_RELEASE=<name> ./scripts/install.sh local --with-keda --skip-monitoring
```

⚠️ **The third line needs a Prometheus that this command does not install, hence
`KPS_RELEASE`.** KEDA's Prometheus scaler reads a Prometheus API to get
`vllm:num_requests_waiting`, and `--skip-monitoring` is precisely the flag that installs no
monitoring stack. On a fresh cluster the two together produce a ScaledObject with nothing to
scale on — it does not fail loudly, it just never scales, which is the worst of the three
outcomes. The combination is only valid against a stack that is already there, which is what
`KPS_RELEASE=<name>` points at (CLAUDE.md, cluster loop). **W2's acceptance path must not use
this line on a fresh cluster.**

⚠️ ~~The third line still needs its prerequisite stated.~~ **DONE — 2026-08-07, the example
now carries `KPS_RELEASE` and the reason.** Raised by CodeRabbit on PR #39, which read the
flag combination as a supported greenfield path, because as written it was.

Convert the `case "${2:-}"` to a loop over `"$@"` so flags compose, keeping the strict
unknown-argument rejection. `--with-keda` installs the chart and applies the ScaledObject;
without it, nothing about the install changes — no new namespace, no CRDs, no ScaledObject.

⚠️ **KEDA's CRDs are cluster-scoped and outlive `helm uninstall`.** `teardown.sh` must remove
them, or a subsequent `--with-keda` install onto the same cluster inherits a stale CRD
version. Mirror the flag there.

**W4.2 Terraform — and the honest answer is that KEDA needs no terraform change.**

State this plainly in the docs rather than quietly adding variables. KEDA scales *pods*.
`llm-sim` is a `python:3.12-slim` container with no GPU request, so several replicas fit on
the existing fixed-size node groups. **The EKS and GKE roots as they stand today already
support everything W1–W3 needs.**

Node autoscaling is a *separate* demonstration — the cluster-autoscaler interaction, not the
KEDA one — and is worth doing only if that interaction is the thing being shown. If it is:

- **EKS**: introduce `node_count_min` / `node_count_max`, both defaulting to `var.node_count`,
  so `terraform plan` against an existing tfvars is byte-identical to today.
- **GKE**: an `autoscaling { min_node_count, max_node_count }` block gated on a variable
  defaulting to off. ⚠️ **Per zone**, matching the existing `node_count` comment — a
  regional cluster multiplies both bounds by the zone count, and a max that looks modest
  becomes three times the bill.
- Both roots keep these in their own `variables.tf`. Sizing is not a cross-cloud identity
  constant and does not belong in `terraform/modules/contract`.

## Non-goals

- **Full OpenAI API compatibility.** A minimal shape-compatible endpoint — two response
  modes, nothing more.
- **The disruption harness and matrix.** `prompt-disruption-drill.md` owns measuring what
  eviction does to in-flight streams. This file only supplies the streaming ingest that
  drill eventually points at.
- **Real GPU scheduling, MIG, or fractional-GPU allocation.** The `nvidia.com/gpu` integer
  problem is real and is not what this measures.
- **Replacing `arrival_rate_rps`.** The synthesised path stays, permanently. It is what
  makes the fixtures reproducible.
- **Scale-to-zero.** Cold start is a different measurement with a different instrument.

## Acceptance criteria

1. `load_source: "synthetic"` is the default, and every pre-existing `verify.sh` check
   passes unchanged with no KEDA installed.
2. The accept-path decisions — `request_queue_size` above `max_in_flight`, 503 beyond the
   cap — are written down **and asserted in the selftest**, not just chosen.
3. The ingest endpoint supports `stream: true` with per-event flush and `SIGTERM` drain,
   ported from `stream-sim.py` — so repointing `stream-target` at it yields an endpoint on
   which the drill's outcome C is still measurable.
4. The load generator is open-loop and does not reuse a single connection — both stated in
   the manifest and demonstrated by L11 passing.
5. Every ScaledObject query term is filtered by the driven tenant's `model_name`, read from
   its manifest; the unfiltered-sum trap (the saturated fixture's permanent 160) is
   documented alongside the query.
6. The scaling metric has a fixed point: the expected converged replica count is computed
   from the profile arithmetic **before** the first run and recorded in L14's comment. The
   waiting-only oscillator and the `avg()` oscillation are both documented.
7. **L14 passes**: replica count stationary within ±1 across two downscale stabilization
   windows at constant load, at the pre-computed count. L11 and L12 also pass, with L11's
   insufficiency noted in its comment.
8. L10–L14 run only where KEDA is detected, skip loudly elsewhere, and execute in CI on a
   dedicated `keda` matrix leg — the `full` and `lite` legs' runtime is unchanged.
9. `install.sh` with no flags produces a byte-identical install to the current one; a
   `terraform plan` on both clouds is unchanged unless W4.2 is deliberately opted into.
10. `chart-build.py` passes with the KEDA dependency pinned in both `Chart.yaml` and
    `config.sh`.
11. `teardown.sh` removes KEDA's CRDs.
12. The docs say plainly that **the current terraform needs no change for KEDA**, so nobody
    concludes cluster autoscaling is a prerequisite.

## Process

Suggested order: W1.1–W1.3 (ingest path incl. streaming, selftest green) → W1.4
(accept-path bounds + assertions) → W1.5 (generator) → W2 → W3 → W4.

Land W1 before touching KEDA. If W1 is right, W2 is a day; if W1 is wrong, W2 will appear
to work and the error will surface as an autoscaler that oscillates on a six-minute period
while every simple check reads green — which is exactly the failure this prompt exists to
prevent, arrived at the long way round.
