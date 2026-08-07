# Prompt: LLM Serving Simulation & Observability — extends `k8s-ai-observability`

> ## ⚠️ SHIPPED — this is a RECORD, not a specification
>
> The work below landed in **0.1.0 / 0.2.0**: the LLM simulator, its profiles and the vLLM board. Nothing here is outstanding, and nothing
> here should be acted on.
>
> ⚠️ **Its `Background / Facts` section describes the code BEFORE this work landed, and is
> therefore stale by construction.** Those `file:line` citations were true on the date
> stated and are not now — this prompt is what changed them. Read `CLAUDE.md` for the
> current standing law, and the files themselves for current facts. Kept unedited because
> a record's value is being what was actually written.

> **Relationship to `prompt.md`.** That document is the original brief for the GPU
> simulation rig, which is **already built and running** — treat it as historical context,
> not as work to do. Where the two disagree about how the rig behaves, **this document
> wins**: it was written against the live cluster, and `prompt.md` contains at least one
> confidently-stated falsehood (see its struck-through mutating-webhook bullet).
> `verify.sh` currently asserts `prompt.md`'s acceptance criteria; you are **adding** to
> that script, not replacing what it checks.

## Role & Objective

You are a Kubernetes platform engineer extending an existing, working repo. Add a
**simulated LLM inference serving stack** whose Prometheus metrics are indistinguishable
in *shape* from a real vLLM deployment, and surface them in Grafana alongside the existing
simulated-GPU dashboard.

End state: `./scripts/install.sh <eks|gke>` brings up the LLM simulator with the rest of
the stack; a second Grafana dashboard shows TTFT/inter-token-latency percentiles, token
throughput, queue depth and KV-cache utilisation; at least one LLM alert can be driven to
`firing`; and `./scripts/verify.sh` asserts all of it. No GPU hardware, no model weights,
no node resize.

**The point is metric SHAPE fidelity, not value fidelity.** Correct names, metric types,
label sets and bucket boundaries are what dashboards, recording rules, alert expressions
and SLO wiring bind to. Getting those exactly right is what makes the work transfer to a
real vLLM deployment unchanged. Plausible-looking numbers are secondary.

---

## Decisions at a glance

Every row is **already decided** and binding. They interlock: most of the defects found
while writing this document were a decision landing in one section and not the others, so
**read this table before editing any single section**, and when you change a row, check
every place it appears.

| # | Decision | Where |
|---|---|---|
| D1 | Mirror **real vLLM metric names and types**, pinned to a recorded version | Req 1 |
| D2 | **Copy real histogram bucket boundaries** — never invent them | Req 2 |
| D3 | Real cumulative Prometheus **histograms**, not recording rules | Req 3 |
| D4 | `source="simulated"` on `llmsim_*` / `llm:*` **only — never on `vllm:*`** | Req 4 |
| D5 | **No image build**: stock `python:3.x-slim`, stdlib only, script from a ConfigMap | Req 5 |
| D6 | Metrics emitted **in real time** by a background worker; **scrapes are pure reads** | Req 6, E1–E10 |
| D7 | The simulation clock is **injectable**, so `--selftest` can drive it | E10 |
| D8 | Load profile is **JSON** (stdlib has no YAML), polled from a ConfigMap | Req 7, [load profile](#the-load-profile-decided) |
| D9 | Profile mounted as a **directory volume, never `subPath`** | [Mounting](#mounting--directory-volume-never-subpath) |
| D10 | GPU and LLM load are **decoupled**; cross-domain panels are **cluster-aggregate** | [GPU/LLM](#the-gpullm-relationship-decided--read-before-designing-anything) |
| D11 | The GPU binding is **retained but not load-bearing**, and joins **on `(namespace, pod)`** — *not* on `UUID` | GPU/LLM, Req 13 |
| D12 | **Two Deployments** (`llm-steady`, `llm-saturated`) + opt-in `llm-driven` | Deliverable A |
| D13 | `model_name` is **unique per Deployment** and **asserted** in `config.sh` | Deliverable A, Req 10b |
| D14 | The **capacity model** fixes every number; the thresholds interlock | [capacity model](#capacity-model--the-numbers-that-make-the-two-profiles-differ) |
| D15 | A **second dashboard**; `gpu-sim-dcgm` stays untouched | Req 8 |
| D16 | Panels break out **`by (model_name)`** — never aggregate the tenants away | Deliverable C |
| D17 | ServiceMonitor **`interval: 15s`**, must stay ≤30s | Deliverable A |
| D18 | Acceptance criteria are **`L1`–`L8`, additions** to `verify.sh`'s existing checks | [Acceptance Criteria](#acceptance-criteria-add-these-to-verifysh) |
| D19 | `install.sh` creates the conditions; **`verify.sh` only observes** | Deliverable A, L6 |

---

## Background / Verified Facts (use these; do not re-derive)

> Everything in this section was verified against the live `gpu-sim-eks` cluster on
> **2026-07-28** with fake-gpu-operator chart **0.0.59**. The predecessor document
> (`prompt.md`) contains at least one confidently-stated falsehood that cost a full
> re-investigation — see the mutating-webhook correction in it. If you find a
> contradiction between this section and observed reality, **trust reality and correct
> this file**, citing what you ran.
>
> **Reconciled against the built rig on 2026-07-30.** The work is now shipped, so where
> this document and the code disagree, **the code is authoritative** — the corrections
> below have been folded in, each marked **CORRECTED** with what superseded it. Two of
> them were the same class of error this file was written to avoid: a plausible fact
> stated confidently (the `UUID` join) and arithmetic done against the wrong variable
> (the capacity model). Both cost a re-investigation; both are recorded rather than
> quietly deleted, because the *reasoning* is what stops them recurring.

### The existing rig

- Two-phase: Terraform (cluster + CPU node pool) then `scripts/install.sh` (Helm +
  manifests). You are working **entirely in Phase 2**. Do not touch `terraform/`.
- **CORRECTED — there are THREE targets, not two.** A `local` (kind) target shipped
  alongside `eks`/`gke`: Phase 1 is `scripts/kind-up.sh` instead of `terraform apply`, and
  `kind/gpu-sim.yaml` carries the node label that Terraform applies on the clouds
  (cross-checked by `assert_kind_contract`). Phase 2 is byte-identical on all three — the
  only difference is how the kubecontext is obtained. `task local:up` is the advertised
  one-shot path and needs no cloud account. **Everything you add must work on `local`**,
  and `verify.sh`'s LLM checks run there unchanged.
- Nodes: 2× `t3.large` (2 vCPU / 8 GB), CPU-only, amd64, **no GPU taint**. The monitoring
  stack shares this pool. There is no headroom for a real model server — this is why the
  simulator must be tiny. Node *count* differs per target — 2 on EKS, ~3 on GKE
  (`node_count` is per-zone on a regional cluster), **1 on `local`** — so never hardcode
  it or anything derived from it (see the GPU series count below).
- Namespaces are a guarded contract: `monitoring` (kube-prometheus-stack) and
  `gpu-operator` (fake GPU stack). `scripts/config.sh::assert_manifest_namespaces` fails
  the install loudly if `config.sh` drifts from the namespaces hardcoded in
  `manifests/**`.
- `scripts/config.sh` is the single source of truth for runtime constants and holds the
  drift assertions run by `install.sh`: `assert_manifest_namespaces`, `assert_gpu_contract`,
  `assert_dashboard_contract`, `assert_terraform_contract`, plus `assert_kind_contract`
  (the `local` target's stand-in for the Terraform cross-check) and `assert_llm_contract`
  (added by this work — Req 10b). **Follow this pattern for anything you add.**
- `Taskfile.yml` is the front door: `taskfiles/target.yml` is included three times with
  `CLOUD=eks|gke|local`, so one definition serves all three targets. It wraps `scripts/`
  and must not reimplement any of their logic. `task selftest` is the repo's only
  cluster-free test.
- Prometheus is configured with `serviceMonitorSelectorNilUsesHelmValues: false`,
  `ruleSelectorNilUsesHelmValues: false`, `podMonitorSelectorNilUsesHelmValues: false`
  — so **any** ServiceMonitor / PrometheusRule in **any** namespace is picked up
  regardless of labels. You do not need the `release:` label (the existing manifests carry
  it anyway, harmlessly).
- Grafana: sidecar dashboard discovery on label `grafana_dashboard`, `searchNamespace:
  ALL`. ClusterIP only, anonymous **Viewer** enabled, reached via `scripts/grafana.sh`
  which holds a port-forward. Admin password is chart-generated in secret
  `kube-prometheus-stack-grafana`.
- Storage is `emptyDir` for Prometheus, deliberately (keeps EKS/GKE symmetric, avoids
  EBS-CSI + IRSA). **Metrics do not survive a Prometheus restart.** Default retention.
- No CI, no Dockerfile, no container registry, no build pipeline. Every image used today
  is public and pulled directly. **Do not introduce an image build.**
- **CORRECTED — the repo IS under version control now** (git, `.gitignore` + `LICENSE`
  present; the initial commit had not yet been made at the time of this sweep). The
  "assume nothing can be diffed or reverted, be additive" instruction this bullet
  originally carried no longer applies.

### What the fake GPU stack gives you (and does not)

- The fake `dcgm-exporter` emits **exactly three series**, and the chart has no knob to
  add more: `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE`.
- Two further series, `DCGM_FI_DEV_GPU_TEMP` and `DCGM_FI_DEV_POWER_USAGE`, exist **only**
  as recording rules in `manifests/alerts/gpu-prometheusrule.yaml`, synthesised from
  utilisation and tagged `source="derived"`, with an `unless on (UUID) <metric>{source!="derived"}`
  clause that makes them self-disable per-GPU if the real series ever appears. **Copy this
  idiom.**
- Observed label set on `DCGM_FI_DEV_GPU_UTIL`:
  `Hostname, UUID, gpu, modelName, device, container, endpoint, instance, job, namespace,
  pod, service`. Example: `UUID="GPU-057e6f5d-af4d-51e5-8ed4-e17ef9c47547"`,
  `gpu="1"`, `modelName="Tesla-T4"`, `device="nvidia1"`, `Hostname="nvidia-dcgm-exporter-8a3593"`.
- **⚠️ `pod` and `namespace` on DCGM series identify the EXPORTER pod
  (`nvidia-dcgm-exporter-*` in `gpu-operator`), not the workload consuming the GPU.**
  There is **no per-workload GPU attribution** in this rig. This is the single most
  important constraint on the design — see "The GPU/LLM relationship" below.
- GPU series total = node count × `gpuCount: 8` (`helm/fake-gpu-operator/values.yaml`):
  **16 on EKS, ~24 on GKE, 8 on `local`**. `UUID` is present and **distinct per GPU**, so
  it is a valid join key *between DCGM series* — but **not** to the simulator's binding
  series, which cannot see it (see the binding correction below).
- Simulated utilisation is driven by the **pod-template** annotation
  `run.ai/simulated-gpu-utilization: "85-99"`. It is the only controllable quantity.
  **GPU memory is allocation-driven, not load-driven**: a GPU with any pod on it reports
  `FB_USED=15360, FB_FREE=0`.
- **There is no mutating admission webhook.** The fake `nvidia-smi` and topology are
  injected by the **device plugin's `Allocate()` response**, so they never appear in the
  pod spec. The only environment variable injected into a GPU-requesting container is
  **`MOCK_NVIDIA_VISIBLE_DEVICES`** (note the `MOCK_` prefix — it is *not*
  `NVIDIA_VISIBLE_DEVICES`). Running `nvidia-smi` inside a pod **panics**; it is not a
  usable check.
- **⚠️ CORRECTED — that injected value is NOT the GPU's DCGM `UUID`.** This document
  originally assumed it was, and specified a `UUID` join on the strength of that. Chart
  0.0.59 injects the **device plugin's own per-allocation id** — a bare random v4 like
  `cb7f4584-d2db-4fc2-9bc9-4e4f3179fb9a` — while the exporter labels that same GPU
  `GPU-fff9ceb6-313d-537f-9174-a01b04f1a9ff`, a deterministic **v5** derived from the
  topology ConfigMap. Two code paths, two id schemes; they never match, so `on (UUID)`
  matches **nothing, silently, forever** — a join that returns an empty result rather than
  an error, which is the hardest kind to notice. Hence the binding series labels it
  `device_id`, deliberately *not* `UUID`, so nobody is invited to write that join again.
  See `detect_binding()` in `scripts/llm-sim.py`.
- Expected-and-fine in `gpu-operator`, do not treat as failures: `deployment/gpu-operator`
  and `deployment/nvidia-dcgm-exporter` at **0/0** (placeholder + KWOK template — the real
  exporter is the **DaemonSet**), `daemonset/mig-faker` at 0 desired, and recurring
  `FailedToRetrieveImagePullSecret` warnings for a non-existent `gcr-secret`.
- The fake-gpu-operator chart ships **no ServiceMonitor of its own** (verified). The only
  one is `manifests/servicemonitor/fake-gpu-servicemonitor.yaml` (note: the *file* is
  `fake-gpu-servicemonitor.yaml`; `fake-dcgm-exporter` is the ServiceMonitor's
  `metadata.name` inside it), authored here.

### Repo conventions you must follow

- Scripts: `set -euo pipefail`, `cd` to repo root, `source scripts/config.sh`, explicit
  `--context` on every `kubectl`/`helm` call, wrong-context guard before any mutation.
- Install ordering is load-bearing: kube-prometheus-stack **first** (CRDs + its validating
  webhook must exist before any `PrometheusRule` is applied), then the GPU stack, then
  workloads. `teardown.sh` reverses it.
- Pin every version in one referenced place (`scripts/config.sh` / the README table).
- Assert contracts rather than documenting them. A comment that says "these must match"
  is worth less than a `grep` that fails the install.
- **Assert that values MOVE, not merely that series exist.** `verify.sh` recently carried
  a check that appeared to validate the utilisation path and was in fact satisfied by an
  unrelated always-firing alert. Every check you add must be capable of failing for the
  reason its message claims.

---

## The GPU/LLM relationship (decided — read before designing anything)

You will want cross-domain panels: "tokens/sec vs GPU power", "GPU utilisation vs
throughput". Getting this right requires understanding a constraint that has no clean
workaround in this rig.

**Simulated GPU utilisation and simulated LLM load are driven independently, and cannot be
coupled without a pod restart.** GPU utilisation comes from the pod-template annotation
`run.ai/simulated-gpu-utilization`; changing it rolls the pod, which would reset the LLM
counters and histograms the whole exercise depends on. Attempting to make one pod vary
both is a direct contradiction with the Execution model below.

**The decision: decouple them, and reuse what already exists.**

- The LLM simulator holds its fake GPU at a **static** utilisation. It does not attempt to
  vary GPU util, and never rolls during a load run.
- **GPU-side movement comes from the existing rig** — `manifests/workloads/extras/gpu-driven.yaml`
  plus `scripts/drive-load.sh`, which already do exactly this. `scripts/drive-llm-load.sh`
  coordinates the two so the profiles line up in time.
- **Cross-domain panels are therefore CLUSTER-AGGREGATE**, not per-GPU:

  ```promql
  # tokens per watt, cluster-wide
  sum(rate(vllm:generation_tokens_total[5m])) / sum(DCGM_FI_DEV_POWER_USAGE)
  ```

- **⚠️ Be explicit, in the panel description and in `docs/llm-simulation.md`, that these
  correlate two INDEPENDENTLY DRIVEN signals.** They demonstrate the query pattern, the
  join and the dashboard wiring — they do **not** show a causal relationship, because
  nothing in this rig makes GPU load follow LLM load. Overstating this would be exactly
  the "looks like it validates something, doesn't" failure this repo has already had once.

### The attribution binding (retained, but not load-bearing)

Give a simulator pod a `nvidia.com/gpu: 1` request and the device plugin allocates it a
fake GPU, injecting `MOCK_NVIDIA_VISIBLE_DEVICES` into the container. Read that at startup
and emit an info-style series recording that the pod holds a GPU:

```
llmsim_gpu_binding_info{model_name="...", device_id="cb7f4584-...", source="simulated"} 1
```

**⚠️ CORRECTED — this originally read `UUID="GPU-...", gpu="3"`, and both were wrong.**
The injected value is the device plugin's own allocation id, not the exporter's `UUID`
(see the Background correction), and the plugin does not tell the container which GPU
*index* it got, so `gpu` cannot be populated either. The label is `device_id`.

**The join to the GPU side therefore goes through the POD, not an id.** The fake exporter
does label each allocated GPU with its consumer, but Prometheus renames those to
`exported_namespace`/`exported_pod` because the scrape target's own `namespace`/`pod`
labels win the collision — so they have to be mapped back before the vectors share a key:

```promql
llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu)
  label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""},
    "namespace", "$1", "exported_namespace", "(.*)"),
    "pod", "$1", "exported_pod", "(.*)")
```

That expression is what Criterion L4b asserts and what the dashboard's attribution table
queries. Note it recovers `UUID` and `gpu` via `group_left` — the binding series does not
need to carry them, it only needs to identify its pod.

The binding is kept because it demonstrates the attribution technique that **does** become
load-bearing against a real `dcgm-exporter` (which exposes pod attribution via the same
`exported_pod`/`exported_namespace` mechanism). Under this decision no flagship panel
depends on it, so it is a table/annotation panel, not the basis of the cross-domain maths.

**⚠️ Do NOT put `UUID`, `device_id` or `gpu` on the `vllm:*` series.** Real vLLM emits
none of them, and adding one silently breaks the transfer property this exercise exists to
preserve — a dashboard written against your series would not work against a real
deployment. The binding stays in its own metric.

The same prohibition covers **every** label real vLLM does not emit, including this repo's
own `source="simulated"` provenance label — see Requirement 4, which carves `vllm:*` out
explicitly. `llmsim_*` and `llm:*` series are this rig's inventions and may be labelled
freely; `vllm:*` may not.

Because the binding is not load-bearing, **the GPU request must be optional** — see
Requirement 13. In the default install only `llm-steady` takes it; `llm-saturated` runs
unbound deliberately, so the unbound path is exercised every time.

---

## Execution model — real time, event-driven (decided)

Metrics are produced by **simulating request lifecycles in wall-clock time**, not computed
as a function of elapsed time at the moment of scrape. The simulator behaves like an
instrumented server: a background worker advances simulated requests through
arrival → queue → prefill → decode → completion and **observes** into the same counters
and histograms a real client library would. This is what makes the histograms genuinely
observation-driven rather than back-computed, and it is what allows queue depth and TTFT
to degrade coherently under load.

**E1 — State is advanced by a background worker, never by a scrape.** A dedicated thread
owns the simulation clock and the request lifecycle.

**E2 — `/metrics` is a pure read.** Serving a scrape must not increment a counter, observe
into a histogram, or mutate any simulator state: take the lock, render the exposition
text, release. This is a correctness requirement, not an optimisation — `curl`-ing the
endpoint during development, and the `readinessProbe` on `/metrics`, must not perturb the
data Prometheus sees. Two concurrent scrapes must return consistent snapshots.

**E3 — Event-driven, not a busy tick.** Use a scheduled-event queue with sleeps between
events. The nodes are 2 vCPU and shared with Prometheus, Grafana and Alertmanager; the
simulator must idle at effectively zero CPU under the `idle` profile and stay well inside
its limits at `saturation`.

**E4 — Token streams are scheduled in batch, never stepped token-by-token.** At saturation
the aggregate token rate is in the thousands per second; a timer or loop iteration per
token would dominate the node. Compute a request's token schedule on admission and observe
its latency samples at the appropriate wall-clock moments.

**E5 — Gauges are instantaneous; counters and histograms are cumulative.**
`num_requests_running`, `num_requests_waiting` and `gpu_cache_usage_perc` reflect state at
render time. Counters and `_bucket`/`_sum`/`_count` only ever increase within a process
lifetime.

**E6 — Use a monotonic clock** (`time.monotonic()`) for every duration, so an NTP step
cannot produce a negative latency observation or a non-monotonic counter.

**E7 — Restarts reset counters, and that is correct.** Prometheus `rate()` is reset-aware;
do not attempt to persist counters across restarts. It is also the reason the load profile
is polled from a ConfigMap rather than patched into env: needless restarts fragment the
very series the dashboard reads.

**E8 — Seeded RNG.** The seed is configurable and **fixed by default under `--selftest`**
so self-tests are deterministic; normal runs may seed from entropy.

**E9 — Profile changes apply on the next poll** without disturbing in-flight requests or
resetting counters. The poll is **every 10s** (the worker's `poll_seconds`); the ~60s
kubelet ConfigMap propagation delay dominates it, so an edit lands in roughly a minute.

**E10 — The simulation clock must be injectable.** The worker reads time through a single
seam (a `now()` callable, or equivalent) rather than calling `time.monotonic()` throughout.
In normal operation that seam is the monotonic clock and the background thread drives it;
under `--selftest` the seam is driven **manually**, so tests advance simulated time in
fixed steps with no wall-clock waiting and no background thread running.

This is not optional polish — it is what makes the self-test *possible*. E1 gives the
background worker ownership of the clock, so "render twice with no elapsed simulation" is
not a reachable state in normal operation; without an injectable seam that check cannot be
written. It is also what makes E8's seeding produce genuinely reproducible runs.

Concurrency: stdlib `threading` with `http.server.ThreadingHTTPServer`, all shared state
behind a single lock. No third-party libraries.

---

## The load profile (decided)

The profile is the simulator's entire control surface: it is what `drive-llm-load.sh`
edits, what E9 applies on poll, and what determines every metric value.

**It is JSON, not YAML.** Requirement 5 restricts the simulator to the Python standard
library, and `json` is stdlib while `yaml` is not. Store it as a single `profile.json` key
in the profile ConfigMap. (An implementer who reaches for YAML has silently taken on a
dependency and broken Requirement 5.)

### Schema — the `llm-steady` profile

```json
{
  "model_name": "sim-llama-3-8b-steady",
  "arrival_rate_rps": 1.8,
  "max_concurrency": 16,
  "max_in_flight": 176,
  "prompt_tokens":     {"mean": 512, "stddev": 128},
  "generation_tokens": {"mean": 256, "stddev": 64},
  "base_ttft_seconds": 0.08,
  "base_itl_seconds": 0.015,
  "kv_cache_tokens_capacity": 32768,
  "finish_reasons": {"stop": 0.90, "length": 0.09, "abort": 0.01},
  "seed": null
}
```

`finish_reasons` weights must sum to 1.0; reject the profile if they do not.

**⚠️ CORRECTED — `queue_ttft_penalty_seconds` was removed from this schema.** It existed
to make TTFT a linear function of queue depth, at `0.05s` per queued request. That implied
a 20 rps service rate, which the rest of the model contradicted, and it made a *modelled*
number out of a quantity the simulator can simply **measure**. TTFT is now the real
observed wait — `(now - arrived) + prefill` — so queue latency emerges from the queue
rather than being asserted about it. Any profile still carrying the field is stale.

### Capacity model — the numbers that make the two profiles differ

`llm-steady` and `llm-saturated` are defined by whether `arrival_rate_rps` exceeds
sustainable throughput, so **the capacity formula is normative, not background.**

**⚠️ CORRECTED — capacity must be computed from the CONGESTED inter-token latency.** This
document originally used `base_itl_seconds` directly and got `4.08 rps`. That is the
throughput of a server running *one* request; a server at capacity is by definition running
a full batch, so the figure to divide by is the ITL under congestion:

```
itl_full             = base_itl_seconds × (1 + CONGESTION_AT_FULL_LOAD)   # 0.015 × 1.5 = 0.0225
service_time_seconds ≈ base_ttft_seconds + generation_tokens.mean × itl_full
capacity_rps         ≈ max_concurrency / service_time_seconds
```

For the profile above: `0.08 + 256 × 0.0225 = 5.84s`, so `16 / 5.84 = 2.74 rps`. Its
`arrival_rate_rps` of 1.8 is **≈0.66× capacity** — the queue stays near zero and p95 TTFT
sits well under 1s. That is what "healthy" means here, and it is why the number is 1.8
rather than something chosen by eye.

**This error was not cosmetic, and it is why the formula is normative.** Believing capacity
was 4.08, the steady tenant was set to 2.4 rps as "0.59× capacity". It was really **0.88×**
— close enough to capacity that the queue ran away and pinned at `max_in_flight`, making
the healthy and saturated tenants *indistinguishable on the dashboard* while every
individual number still looked plausible. **Tune against the figure `llmsim_capacity_rps`
reports at runtime**, never against a hand-derived one.

**Saturation is a designed steady state, not an overflow.** When arrivals exceed capacity,
`in_flight` grows until it reaches `max_in_flight`; queue depth then plateaus at
`max_in_flight - max_concurrency`, and because TTFT is the *real* wait, Little's Law fixes
the latency plateau:

```
plateau_queue = max_in_flight - max_concurrency
plateau_ttft  ≈ plateau_queue / capacity_rps
fill_time     ≈ plateau_queue / (arrival_rate_rps - capacity_rps)
```

`llm-saturated` differs from the profile above in three fields — two that matter, one
cosmetic:

| Field | `llm-steady` | `llm-saturated` |
|---|---|---|
| `model_name` | `sim-llama-3-8b-steady` | `sim-llama-3-8b-saturated` |
| `arrival_rate_rps` | `1.8` (≈0.66× capacity) | `6.0` (≈2.19× capacity) |
| `finish_reasons` | `abort: 0.01` | `abort: 0.03` (a degraded tenant sheds more requests) |

With `max_in_flight: 176` that gives `plateau_queue = 160`, so
`plateau_ttft ≈ 160 / 2.74 ≈ 58s`, reached about 49s after start
(`160 ÷ (6.0 − 2.74)`). **Do not treat these as arbitrary** — they are chosen to sit
inside the following interlocking set, and changing one requires re-checking the others:

| Quantity | Value | Why |
|---|---|---|
| `LLMHighTTFT` threshold | `llm:ttft:p95_5m > 2`, `for: 2m` | Well above steady (~0.1s), well below saturated (~58s) |
| `llm-steady` p95 TTFT | ~0.1s | Must stay below the alert threshold, or the "healthy" tenant alerts |
| `llm-saturated` p95 TTFT | ~58–60s (the real queue wait) | Must exceed the threshold promptly, and stay below Criterion L3b's sanity bound |
| Criterion L3b sanity bound | `< 120s` | Catches `+Inf` and runaway queues without tracking the tuning above. **Not 60s** — p95 sits above the ~58s mean, so a 60s bound would fail on a correctly-behaving saturated tenant |

⚠️ **Criterion L3 is scoped to `llm-steady`'s `model_name`.** The recording rules are
`by (model_name)` (see Deliverable B), so an unscoped bound would also be applied to the
deliberately-degraded tenant, and the `max_in_flight` value — chosen for memory safety —
would silently decide whether an acceptance check passes.

### Required derived behaviour

These relationships are what make the simulation coherent, and what the alerts exist to
catch. They are requirements, not suggestions:

| Signal | Derivation |
|---|---|
| `vllm:num_requests_running` | `min(in_flight, max_concurrency)` |
| `vllm:num_requests_waiting` | `max(0, in_flight - max_concurrency)` |
| TTFT observation | **Measured, not modelled**: `(now - arrived) + prefill` — the real time the request spent queued, plus its prefill |
| Inter-token latency | `base_itl_seconds × (1 + CONGESTION_AT_FULL_LOAD × load)`, where `load = running / max_concurrency` |
| `llmsim_capacity_rps` | `max_concurrency / (base_ttft + gen_mean × itl_full)` — exposed as a gauge so load is tuned against the real figure |
| `vllm:num_preemptions_total` | counter, part of the mirrored vLLM surface |
| `vllm:gpu_cache_usage_perc` | active-request tokens ÷ `kv_cache_tokens_capacity`, clamped to `[0,1]` |
| `vllm:request_success_total` | partitioned by `finish_reasons` weights, on **completion** |
| Arrival when `in_flight == max_in_flight` | **rejected** — see below |

The TTFT line is the important one, and it is important precisely because it is *not* an
expression. Saturation degrades latency because requests genuinely wait longer, so
`LLMHighTTFT` catches an emergent property rather than a formula restated as a metric:
`arrival_rate_rps` exceeding `capacity_rps` (defined above) causes `in_flight` to grow,
which grows queue depth, which grows the measured wait.

**Rejected arrivals must be observable, never silently dropped.** On a permanently
saturated Deployment the cap is the *normal* operating state, not an edge case — so
dropping arrivals silently would make every queue and latency metric plateau at an
artificial value with nothing indicating why. Increment a dedicated
`llmsim_requests_rejected_total` counter instead.

Deliberately **do not** attribute rejections to `vllm:request_success_total` — that metric
counts requests that *finished*, and a rejected request never ran. Inventing a semantic for
a real vLLM metric is the same error as adding a label to one (Requirement 4).

### Mounting — directory volume, never `subPath`

**⚠️ Mount the profile ConfigMap as a directory volume. Do NOT use `subPath`.**

`subPath` mounts are resolved once when the container starts and are **never updated by
kubelet**. Mounting `profile.json` via `subPath` — the obvious way to place a single file
at a fixed path — freezes it for the lifetime of the pod, so E9's polling reads the same
bytes forever.

This matters more than it sounds, because the failure is completely silent and points
away from the cause: `drive-llm-load.sh` exits 0, `kubectl get cm` shows the new profile,
the pod is Running and healthy, and nothing changes. It reads as a broken simulator. The
`llmsim_profile_generation` gauge from the next section is the tell — it would sit at its
initial value forever — which is a large part of why that metric is required.

The script ConfigMap is unaffected: it is read once at startup, so a stale mount there is
indistinguishable from the normal "restart to pick up a new script" workflow.

### Validation and observability

- **A malformed profile must never crash the process.** Log loudly, **retain the last good
  profile**, and carry on. A crash restarts the pod and resets every counter — precisely
  what E7's rationale forbids.
- Expose two of the simulator's own metrics so a profile change is *visible* rather than
  mysterious during the propagation delay:
  - `llmsim_profile_generation` (gauge) — increments on each successful reload;
  - `llmsim_profile_reload_errors_total` (counter).

  Both are `llmsim_*` series and therefore carry `source="simulated"` per Requirement 4.
  Put `llmsim_profile_generation` on the dashboard: it is how an operator confirms an edit
  landed.

### Presets live in the script, not the ConfigMap

`drive-llm-load.sh` embeds its named profiles (`idle`, `steady`, `burst`, `saturation`,
`ramp`) and writes the chosen one into `profile.json`, mirroring how `scripts/drive-load.sh`
embeds its `ramp`/`spikes` tables. The ConfigMap holds exactly one live profile — not a
menu. **`ramp` is the default** — a staircase from 0.4 up to 6.0 rps and back — and it is
what `install.sh`'s closing hint and the `llm-load` task invoke, so it is the preset most
people will actually see.

---

## Hard Requirements & Decisions (already made — honour them)

1. **Real vLLM metric names and types, pinned.** Record the chosen version as
   `LLM_VLLM_VERSION` in `scripts/config.sh` and in the README version table, and mirror
   **that version's** metric surface exactly.
   **Default: a `v0.6.x` tag**, because the `vllm:*` names listed in this document and the
   widely-circulated public Grafana boards are from that era. Override it if your
   production target differs — the point is that the choice is explicit and recorded, not
   that it is this particular tag.
   ⚠️ vLLM renamed and deprecated metrics between v0 and v1. **Before writing a single
   metric, diff your intended list against `vllm/engine/metrics.py` at the pinned tag.**
   Do not mix names from different eras, and do not invent any. If the pinned version
   disagrees with the names in this document, the pinned version wins — and correct this
   document.
2. **Copy the real histogram bucket boundaries** from the pinned vLLM version's source.
   Do not invent bucket layouts. `histogram_quantile()` accuracy is entirely determined by
   bucket placement; a dashboard or SLO tuned against invented buckets will not transfer,
   which defeats the exercise.
3. **Histograms must be correct Prometheus histograms**: cumulative, monotonically
   non-decreasing `_bucket` counters with a `+Inf` bucket, plus `_sum` and `_count`. They
   must behave correctly under `rate()` and `histogram_quantile()`. This requirement is
   why a recording-rule-only approach was rejected — do not regress to one.
4. **Provenance labelling, with a deliberate carve-out.** Series this repo *invents* —
   `llmsim_*` and the `llm:*` recording rules — carry `source="simulated"`, mirroring the
   existing `source="derived"` convention.
   **⚠️ `vllm:*` series must NOT carry `source`, nor any other label real vLLM does not
   emit.** This is the same rule as the `UUID` exclusion above, for the same reason: an
   extra label breaks exact-match binary operations against a real deployment, breaks
   `group_left`/`group_right` joins that assume matching label sets, and silently survives
   `sum without(...)` aggregations copied from a real setup. Provenance for `vllm:*` is
   already unambiguous from the `job`/`service`/`namespace` labels Prometheus attaches at
   scrape time — it does not need a label of its own.
   Recording rules must use the `llm:` prefix. **Do not record anything under a `vllm:*`
   name.** (The GPU side deliberately records derived series under *real* DCGM names
   because the fake exporter genuinely omits them. That reasoning does not transfer here:
   the simulator emits the full vLLM surface itself, so there is nothing to backfill.)
5. **No image build.** Stock public `python:3.x-slim` (pin the tag), with the simulator
   script mounted from a ConfigMap. **Python standard library only — no `pip install` at
   startup.** Hand-write the Prometheus text exposition format. This keeps the repo
   buildless, registry-free and air-gap-capable.
6. **Metrics are emitted in real time by a background worker; scrapes are pure reads.**
   See [Execution model](#execution-model--real-time-event-driven-decided) above — E1–E10
   are requirements, not suggestions.
7. **Load profile is delivered by a mounted ConfigMap that the simulator polls**, not by
   patching env or annotations. Patching rolls the pod, which resets counters; for a
   metric set dominated by counters and histograms, continuity matters more than the ~60s
   ConfigMap propagation delay. Document that delay. Schema, derived behaviour, validation
   and the JSON-not-YAML constraint are specified in
   [The load profile](#the-load-profile-decided) — that section is normative.
8. **A second dashboard, not an extension of the existing one.** `gpu-sim-dcgm` must stay
   exactly as it is — it is the repo's proof that a stock DCGM board works unchanged.
9. **New namespace `llm-sim`**, added to `scripts/config.sh` as `LLM_NS` and covered by an
   extended `assert_manifest_namespaces`.
10. **Contract assertions — two of them, both following the repo's existing pattern.**

    a. **`assert_dashboard_contract` must be generalised** to validate a list of
    **(manifest file, ConfigMap name, dashboard uid) triples**. Note it currently hardcodes
    all three — including the *path* `manifests/dashboards/dcgm-configmap.yaml` — so
    generalising only the name and uid would leave the second dashboard's JSON unchecked.
    Both dashboards must be covered.

    b. **A new `assert_llm_contract` must guard `model_name`.** Put the names in
    `scripts/config.sh` (`LLM_STEADY_MODEL`, `LLM_SATURATED_MODEL`, `LLM_DRIVEN_MODEL`) and
    assert, at install time, that each matches the `model_name` in its profile ConfigMap
    **and that all three are distinct**.
    This is not bureaucracy — `model_name` has become a string contract spanning four
    artefacts: the profile ConfigMap, the recording rules' `by (model_name)` output,
    Criterion L3's scoped assertion, and the dashboard's panel queries. A typo in any one
    of them produces **a green install where L3 silently scopes to a tenant that does not
    exist** — the identical failure mode `assert_gpu_contract` exists to prevent ("a green
    install with ZERO GPUs"). Every other cross-file string in this repo is asserted;
    this one must be too.
11. **Alert names must contain `LLM`**, so `verify.sh` can select them precisely. Select
    alerts **by exact name** in checks — never by a wildcard regex.
12. **No node resize, no new cost.** Each simulator pod must fit in ~64Mi and a fraction
    of a core; the default install runs two of them. In-flight requests are bounded by the
    profile's `max_in_flight`, which **must additionally be clamped in code** to a hard
    ceiling — the profile is operator-editable, so memory safety cannot depend on it alone.
    Note `max_in_flight` is doing double duty: memory bound *and* the knob that fixes
    `llm-saturated`'s latency plateau, so changing the clamp changes the capacity model.
13. **The fake-GPU request must be OPTIONAL and degrade gracefully.** It exists only for
    the attribution binding, which is not load-bearing. If the GPU stack is absent, or all
    16 fake GPUs are already allocated, a simulator must still come up and serve its full
    `vllm:*` surface — it must **not** sit `Pending` forever. Make the request toggleable,
    omit `llmsim_gpu_binding_info` when unbound, and log the reason clearly at startup.
    `verify.sh` must **SKIP** the binding check when no binding series exists at all —
    but the cluster-aggregate cross-domain check does not depend on the binding and stays
    unconditional. Note `llm-saturated` runs unbound by design, so this path is exercised
    on every default install.
14. **Series cardinality must stay trivial and be stated.** Expect roughly **143 series**
    for the default two-Deployment install. Per pod: 3 histograms contributing
    `24 + 14 + 14 = 52` bucket series (TTFT's copied vLLM layout is 23 buckets plus `+Inf`;
    TPOT and E2E are 13 plus `+Inf`) plus 6 `_sum`/`_count` = 58, plus 13 gauges/counters
    (including `request_success_total` × 3 finish reasons and the four `llmsim_*` metrics),
    and one extra binding series on the bound pod — so 72 + 71. Prometheus here runs on
    `emptyDir` with default retention, so document the multipliers — additional
    `model_name` values, replica count and extra Deployments each scale it linearly.
15. **Existing behaviour must not regress.** All current `verify.sh` checks must still
    pass, and `teardown.sh` must remove everything you add, in the correct order.

---

## Deliverables

### A. The simulator — `manifests/llm/`

- **The simulator script ConfigMap** (stdlib Python, serves `/metrics` on `:9401`), shared
  by all the Deployments below.
  **CORRECTED — it is not a file in `manifests/llm/`.** The simulator lives at
  `scripts/llm-sim.py` as a normal, readable, locally-runnable Python file, and
  `install.sh` builds the ConfigMap from it:
  `kubectl create configmap llm-sim-script --from-file=llm_sim.py=scripts/llm-sim.py
  --dry-run=client -o yaml | kubectl apply -f -` (idempotent on re-install). Keeping it a
  file rather than a few hundred lines of indented YAML is what makes `--selftest` and
  `task selftest` possible at all.
- **One profile ConfigMap per Deployment**, each holding a `profile.json` per
  [The load profile](#the-load-profile-decided).

**Two Deployments, mirroring the GPU rig's `gpu-idle`/`gpu-steady`/`gpu-busy` pattern.**
The existing design is that **`install.sh` creates the conditions and `verify.sh` only
observes** — `gpu-busy` permanently trips `GPUHighUtilization`, and verify just watches.
Preserve that: do **not** make `verify.sh` mutate cluster state to produce an alert.

| Deployment | Profile | GPU request | Purpose |
|---|---|---|---|
| `llm-steady` | healthy — queue stays near zero | **yes** (holds the attribution binding) | the "good" tenant |
| `llm-saturated` | `arrival_rate_rps` above sustainable throughput | **no** (runs unbound) | permanently trips `LLMHighTTFT` |

This resolves three things at once: Acceptance Criterion L6 becomes meaningful while
`verify.sh` stays read-only; the dashboard shows a healthy and a degraded tenant side by
side, which is a far better demo than one uniformly saturated system; and `llm-saturated`
running unbound **exercises Requirement 13's graceful-degradation path in every default
install** rather than leaving it untested.

⚠️ A permanently-firing alert is only acceptable because Criterion L6 selects by **exact
`alertname`**. Permanently-firing is fine; *wildcard-matched* permanently-firing is the
failure this repo already shipped once.

Each Deployment: 1 replica, script + its own profile mounted, resource requests/limits set,
`readinessProbe` on `/metrics`, and a static `run.ai/simulated-gpu-utilization` annotation
on the **pod template** which is never varied during a run. Give them distinct
`model_name` values so the dashboard can separate them. The `nvidia.com/gpu: "1"` request
goes under `limits:` (Kubernetes mirrors extended resources into requests automatically).

**⚠️ `enableServiceLinks: false` is mandatory, not hygiene.** kubelet injects
Docker-link-compatible env vars for every Service in the pod's namespace, so the `llm-sim`
Service sets **`LLM_SIM_PORT=tcp://<clusterIP>:9401`** in the container. The simulator
originally read `LLM_SIM_PORT` as its listen port, could not parse a URL as an integer, and
died at startup on **every** pod — a `CrashLoopBackOff` whose only visible symptom was an
empty LLM dashboard, which reads as a metrics-plumbing fault rather than a naming
collision. The fix is both halves: the simulator reads `LLM_SIM_LISTEN_PORT`, *and* the pod
spec disables the injected set so the next collision cannot happen by accident. Nothing
here consumes those vars — the simulator dials nothing, and `MOCK_NVIDIA_VISIBLE_DEVICES`
comes from the device plugin's `Allocate()`, which this flag does not affect.

**Pod hardening is part of the deliverable**, and cheap here because the workload is a
single stdlib script: `runAsNonRoot` as uid/gid 65534, `seccompProfile: RuntimeDefault`,
`allowPrivilegeEscalation: false`, `capabilities: drop: [ALL]`, and
`readOnlyRootFilesystem: true`. The last one **requires `PYTHONDONTWRITEBYTECODE=1`** —
without it Python tries to write `__pycache__` beside the mounted script and fails at
import. Set `PYTHONUNBUFFERED=1` too, or the startup log lines explaining an unbound GPU or
a rejected profile sit in a buffer instead of reaching `kubectl logs`.

**⚠️ `model_name` must be UNIQUE PER DEPLOYMENT — including the opt-in `llm-driven`
below.** The recording rules aggregate `by (model_name)`, and Criterion L3 asserts
`llm:ttft:p95_5m{model_name="sim-llama-3-8b-steady"} < 2`. If `llm-driven` reuses the
steady tenant's `model_name` — the obvious result of copying that manifest — the rule
merges the two, and the first `drive-llm-load.sh saturation` run drags the merged quantile
above the bound. **Applying an optional add-on would then break a core acceptance check,
and it would present as a simulator bug.** Treat `model_name` as an identity, not a label.

- **Service(s)** — ClusterIP, named port (e.g. `llm-metrics`) on 9401. One Service
  selecting both pods is sufficient; Prometheus distinguishes them by `pod`.
- **ServiceMonitor** in `monitoring` selecting that Service. **It must carry
  `namespaceSelector.matchNames: [llm-sim]`** — the Service lives in `llm-sim` and the
  ServiceMonitor in `monitoring`, exactly as `fake-gpu-servicemonitor.yaml` does for
  `gpu-operator`. Namespaces are a guarded contract here; wire this to `LLM_NS`.
  **`interval: 15s`**, matching the existing ServiceMonitor. This is load-bearing, not
  cosmetic: Acceptance Criterion L2 uses `rate(...[1m])`, which needs **at least two
  samples inside the window**. At 15s you get four; at 60s you get one and `rate()`
  returns nothing at all, so the check fails with a cause that looks like a dead counter
  rather than a scrape-interval choice. **The interval must stay ≤30s**, and any change to
  it requires re-checking every `rate()`/`histogram_quantile()` window in the rules and
  the acceptance criteria.
- **`llm-driven` (opt-in, `manifests/llm/extras/`)** — a third Deployment, **not** applied
  by `install.sh`, that `drive-llm-load.sh` walks through load curves. This mirrors
  `manifests/workloads/extras/gpu-driven.yaml` exactly, including that the extras
  subdirectory is skipped by a non-recursive `kubectl apply -f`.
  It needs **its own profile ConfigMap in `manifests/llm/extras/`** — one profile ConfigMap
  per Deployment applies here too, and `drive-llm-load.sh` writes into *that* ConfigMap.
  Shipping the Deployment without it yields a pod stuck unable to mount its profile. Its
  starting profile is `idle`, matching how `gpu-driven` starts at `0-5`, and its
  `model_name` must differ from both defaults (see above).

**Security note:** the container executes a script mounted from a ConfigMap, so anyone
with ConfigMap write access in `llm-sim` has code execution in that pod. That is
acceptable for this rig (the alternative is an image build, which Requirement 5 rules
out), but state it in `docs/llm-simulation.md` the way the Grafana anonymous-auth
trade-off is stated in `helm/kube-prometheus-stack/values.yaml`.

Per the [Execution model](#execution-model--real-time-event-driven-decided), a background
worker advances simulated requests in wall-clock time and observes into the metrics; the
HTTP handler only renders.

The simulator must model, at minimum: request arrival and completion; prompt and
generation token counts; time-to-first-token; per-output-token latency; end-to-end
latency; running and waiting request counts; KV-cache utilisation; and success/failure
outcomes by finish reason. Queue depth and latency must respond coherently to offered
load — TTFT should degrade as the queue builds, which is the behaviour the alerts exist
to catch. ("Coherently" means the *relationships* between signals hold; it does not mean
the absolute values resemble any particular real deployment — see Non-Goals.)

A `--selftest` mode must validate its own exposition output with **no cluster, no
Prometheus and no wall-clock waiting**, by driving the injected clock (E10) in fixed steps
with the background worker stopped. It must assert:

- bucket monotonicity, presence of `+Inf`, and `_sum`/`_count` consistency;
- `# HELP`/`# TYPE` correctness and one `TYPE` per metric family;
- counters and `_bucket` values never decrease across steps;
- **that rendering performs no observation** — the E2 requirement. Assert it via an
  internal observation counter that must be *identical* before and after a render, rather
  than by comparing two renders, which cannot distinguish "render did nothing" from
  "nothing happened to be scheduled".

This is the repo's first cluster-free testable artifact — wire it into `task` so it runs
locally with no cloud and no cluster.

### B. Rules — `manifests/alerts/llm-prometheusrule.yaml`

- Recording rules for the expensive/reused expressions — TTFT p50/p95/p99 and token
  throughput. **Name the p95 rule `llm:ttft:p95_5m`**; Acceptance Criterion L3 asserts it
  by that name, so it doubles as the check that the rule is live.
  **Aggregate `by (model_name)`, not globally.** A global quantile merges the healthy and
  saturated tenants into one number that describes neither, and hides the degraded one —
  which is the tenant the alert exists to catch.
- `LLMHighTTFT`: `llm:ttft:p95_5m > 2` for `2m`. These numbers are fixed by the interlocking
  set in the [capacity model](#capacity-model--the-numbers-that-make-the-two-profiles-differ)
  — do not change one without re-checking the others.
- At least one **cluster-aggregate** cross-domain metric, e.g.
  `sum(rate(vllm:generation_tokens_total[5m])) / sum(DCGM_FI_DEV_POWER_USAGE)`. Per the
  GPU/LLM decision above, cross-domain rules do **not** join through the binding series.
- Alerts: `LLMHighTTFT`, `LLMQueueBacklog`, `LLMKVCacheSaturated` and `LLMMetricsAbsent`.
  **State for each whether it is exercised by default** — and note that **two** of them are
  permanently firing, not one: `llm-saturated` trips `LLMHighTTFT` (p95 > 2s) *and*
  `LLMQueueBacklog` (`vllm:num_requests_waiting > 50`, which plateaus at 160). That second
  one is precisely why Criterion L6 must select by exact `alertname`: a wildcard would be
  satisfied by the backlog alert alone and would pass with the latency model broken. The
  other two are not exercised — document how to provoke them (lower
  `kv_cache_tokens_capacity`; scale a Deployment to zero replicas).
- Follow the existing file's conventions: a comment block explaining *why* each rule
  exists, as `gpu-prometheusrule.yaml` does, and `source="simulated"` on the `llm:*`
  series these rules produce. Per Requirement 4, **record under the `llm:` prefix only** —
  never under a `vllm:*` name.

### C. Dashboard — `manifests/dashboards/`

Second sidecar ConfigMap with its own uid. Panels covering latency percentiles, token
throughput split prompt/generation, concurrency and queue depth, KV-cache utilisation,
and outcomes by finish reason. Self-contained JSON, no grafana.com egress, datasource
bound via template variable as the existing board does.

**Every per-tenant panel must break out `by (model_name)`, not aggregate across tenants.**
Showing the healthy and degraded tenants side by side *is* the justification for the
two-Deployment design — a board that sums them away discards the entire point, and would
also disagree with the recording rules, which aggregate `by (model_name)`. The latency
panels in particular should make it obvious at a glance that one tenant sits below the
`LLMHighTTFT` threshold and the other above it. Add a `model_name` template variable so a
reader can also isolate one tenant.

Include `llmsim_profile_generation` somewhere on the board — it is how an operator
confirms a `drive-llm-load.sh` edit actually landed during the ~60s propagation delay, and
the tell for a `subPath` mounting mistake.

Cross-domain panels are cluster-aggregate, and **each must carry a `description` stating
that the two signals are driven independently** (see the GPU/LLM decision). The binding
gets a small table panel — "simulator is on GPU \<UUID\>" — not a maths panel.

### D. Scripts

- `scripts/drive-llm-load.sh` — targets the **opt-in `llm-driven`** Deployment by default,
  exactly as `drive-load.sh` targets `gpu-driven`, and fails with the same actionable
  "apply the extras first" message when it is absent. It must **not** target
  `llm-steady`/`llm-saturated`, whose whole purpose is to hold steady states that
  `verify.sh` depends on. Embedded presets at minimum `steady`, `burst`, `saturation`
  (queue builds, TTFT climbs past the alert threshold) and `idle`; it writes the chosen one
  into that Deployment's `profile.json` ConfigMap and must **not** roll the pod.
  **It also coordinates the GPU side** per the GPU/LLM decision: with `--with-gpu` (shipped
  as positional `$2`, not a parsed flag) it drives `scripts/drive-load.sh` against the
  `gpu-driven` Deployment on a matching curve, so the two domains move together on the
  dashboard. It must apply
  `manifests/workloads/extras/gpu-driven.yaml` first, or fail with the same actionable
  message `drive-load.sh` already emits when that Deployment is absent. Guard the
  kubecontext, or document that it acts on the current one, consistently with
  `drive-load.sh`.
- `scripts/install.sh` / `scripts/teardown.sh` — apply/remove `manifests/llm/` in the
  correct position in the existing ordering (after kube-prometheus-stack for the CRDs and
  the validating webhook; after the GPU stack so the optional GPU request is satisfiable).
- `scripts/verify.sh` — new checks (see Acceptance Criteria).
  **Its port-forwards must be self-healing**, both the Prometheus one and the Grafana one:
  re-check liveness before each use and rebuild if dead. This is not defensive
  boilerplate — the 6-minute `GPUHighUtilization` poll sits between the GPU dashboard fetch
  and the LLM one (L5), and a `kubectl port-forward` does not survive that idle. A forward
  opened once and never re-checked gives you a passing GPU board and an `http 000` LLM
  board, for a Grafana that was healthy the entire time. Reap killed forwards with `wait`,
  or the shell prints its own `Terminated: 15` mid-run and it reads as a check failing.
- `scripts/config.sh` — `LLM_NS`, simulator/Deployment names, `LLM_DASHBOARD_CM` +
  `LLM_DASHBOARD_UID` (matching the existing `DASHBOARD_CM`/`DASHBOARD_UID` naming),
  `LLM_STEADY_MODEL` / `LLM_SATURATED_MODEL` / `LLM_DRIVEN_MODEL`,
  `LLM_VLLM_VERSION`; extend the assertions per Requirements 9 and 10.
- `taskfiles/target.yml` (**CORRECTED** — not `cloud.yml`; it is included three times with
  `CLOUD=eks|gke|local`, so one task definition serves all three targets) — an `llm-load`
  task mirroring the existing `load` task, including its context precondition, plus a
  precondition that the opt-in `llm-driven` Deployment exists. Also wire the cluster-free
  `selftest` task into the **root** `Taskfile.yml`, since it is target-agnostic.

### E. Documentation

- `docs/llm-simulation.md` (**CORRECTED** — the file shipped under this name, not
  `llm-observability.md`; every cross-reference in the repo points here) — the metric
  surface and which vLLM version it mirrors; the join design, why the join goes through
  `(namespace, pod)` rather than an id, and why `UUID` is deliberately absent from
  `vllm:*`; how to drive load; a worked example pushing an alert to `firing`; the
  ConfigMap-code-execution trade-off; and an explicit fidelity caveat. It also carries a
  **`## Troubleshooting`** section for profile and tenant problems, anchored into from
  `docs/troubleshooting.md`.
- Update `docs/architecture.md` (component table, data flow, and note that this adds no
  EKS↔GKE difference), `README.md` (mermaid diagram, version table, fidelity caveat),
  `manifests/dashboards/README.md`, and **`docs/troubleshooting.md`** — the repo-wide
  "empty panel" triage guide, which must link to the LLM section above rather than
  duplicating it.

---

## Acceptance Criteria (add these to `verify.sh`)

> **⚠️ These are ADDITIONS. `verify.sh` already numbers its checks 1, 2, 3, 4, 4b, 4c, 4d
> and 5 — every one of those numbers would collide with a naive 1..8 here.** Use the `L`
> prefix below verbatim in the script's output, and **do not renumber the existing
> checks**: their numbers appear in `manifests/dashboards/README.md` and in inline comments
> across the repo.
>
> `L1`–`L6` are `verify.sh` checks. **`L7` and `L8` are not** — `L7` is a property of the
> run as a whole, and `L8` is exercised by running `teardown.sh`, not by `verify.sh`.
>
> ⚠️ **`verify.sh` HAS SINCE TAKEN BOTH LABELS, and this note exists so the clash is not
> rediscovered a third time.** `L7` there is the queue-time and prefix-cache assertion;
> `L8` is the request phase breakdown (`prompt-phases-and-image.md` W1.8). So `L7` and `L8`
> each denote two different things depending on which document you are reading.
>
> **`verify.sh`'s labels are authoritative for `verify.sh`** and are contiguous — the
> alternative, skipping to `L9` there, would leave a hole in that script's sequence to
> protect a label in a brief which states on this very line that it is not a `verify.sh`
> check. This file's `L7`/`L8` remain acceptance criteria for the run and the teardown.
>
> **Every criterion must pass on all three targets, `local` included.** Nothing in the LLM
> stack is cloud-specific, so a check that only passes on EKS is a check that has picked up
> an accidental dependency on node count or cloud behaviour — `local` is one node with 8
> simulated GPUs, and that is the configuration most people will run.

> **⚠️ Every criterion must be expressed as PromQL that returns ZERO SERIES on failure.**
> `verify.sh`'s helper `promql_count()` returns the **number of result series, not a
> value** — it counts, it does not evaluate. A bare `histogram_quantile(...)` returns a
> series even when its value is `NaN`, and `sum(a)/sum(b)` returns a series when `b` is
> zero (`+Inf`). Phrased naively, those checks count 1 and **pass with no data at all** —
> the exact "looks like it validates something, doesn't" failure this repo has already
> shipped once. Guard with comparison operators: `NaN > 0` is false, so a trailing `> 0`
> filters NaN out. Beware `+Inf > 0`, which is **true** — bound both ends, or filter the
> operands rather than the result.

L1. The LLM scrape target is up: `up{job="<llm-sim job>"} == 1`.
L2. A counter is **advancing**: `rate(vllm:generation_tokens_total[1m]) > 0`.
L3. A histogram is well-formed and usable, asserted in two parts so neither NaN nor `+Inf`
   can pass it:
   - observations are landing —
     `rate(vllm:time_to_first_token_seconds_count[5m]) > 0`
   - the quantile is finite and sane — using the recording rule from Deliverable B, which
     this also exercises. **Scoped to the steady tenant**, because the rules aggregate
     `by (model_name)` and `llm-saturated` is degraded on purpose:
     ```promql
     llm:ttft:p95_5m{model_name="sim-llama-3-8b-steady"} > 0
       and llm:ttft:p95_5m{model_name="sim-llama-3-8b-steady"} < 2
     ```
     The upper bound is the alert threshold: the healthy tenant must sit below the line
     that `llm-saturated` is above, which checks the capacity model end to end rather than
     merely checking that a number exists.
L3b. No tenant is insane — an unscoped sanity bound catching `+Inf` and runaway queues
   without depending on the tuning above. **Derive the expected count; do not hardcode it**,
   because applying the opt-in `llm-driven` takes it from two to three and a hardcoded `2`
   would fail the moment someone uses the extras:
   ```promql
   count(llm:ttft:p95_5m < 120) == count(llmsim_profile_generation)
   ```
   One `llmsim_profile_generation` series exists per running simulator pod, so this reads
   as "every simulator that is up has a sane p95", whatever the deployment count.
   **CORRECTED — the bound is 120s, not 60s.** Under the measured-TTFT model the saturated
   tenant's plateau *is* the real queue wait, `160 / 2.74 ≈ 58s` by Little's Law, and p95
   sits above that mean — so a 60s bound fails on a tenant behaving exactly as designed.
   The old 60s figure belonged to the superseded synthetic-penalty model.
   ⚠️ Do **not** invert this to "assert `llm:ttft:p95_5m >= 60` returns nothing" — that
   passes vacuously when the metric is absent entirely, which is exactly the trap the
   preamble warns about. The `==` form returns no series when either side is missing.
L4. The **cluster-aggregate** cross-domain expression, with both operands filtered so a
   zero denominator yields no series rather than `+Inf`:
   ```promql
   (sum(rate(vllm:generation_tokens_total[5m])) > 0) / (sum(DCGM_FI_DEV_POWER_USAGE) > 0)
   ```
   Unconditional — it does not depend on the GPU binding.
L4b. The attribution binding, **conditionally**: the bound pod must resolve to a GPU series
   actually present on the DCGM side. **CORRECTED — join on the POD, not on `UUID`.** The
   originally-specified `* on (UUID) group_left() DCGM_FI_DEV_GPU_UTIL` matches nothing,
   ever (see the Background correction) — and because an empty join is not an error, that
   check would have reported a clean SKIP-or-FAIL forever without anyone learning why:
   ```promql
   llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu)
     label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""},
       "namespace", "$1", "exported_namespace", "(.*)"),
       "pod", "$1", "exported_pod", "(.*)")
   ```
   must return at least one series. If the simulator came up unbound (Requirement 13),
   `llmsim_gpu_binding_info` is absent; report **SKIP** with the reason — do not FAIL.
   Distinguish "absent" from "present but not matching": the latter is a real failure.
L5. The LLM dashboard ConfigMap is present **by name**, its uid resolves in Grafana over an
   **unauthenticated** request, and its core queries return data.
L6. `LLMHighTTFT` is `firing`, driven by the `llm-saturated` Deployment that `install.sh`
   applies. Select it **by exact `alertname`** — never a wildcard. Poll it out over the
   rule's `for:` duration, since a run started immediately after install will catch it
   still `pending`. `verify.sh` must **not** patch the profile or otherwise mutate cluster
   state to make this true; `install.sh` creates the condition, `verify.sh` observes it.
L7. Every pre-existing check still passes.
L8. `teardown.sh` removes the namespace, workloads, rules and dashboard, and
   `terraform destroy` still completes cleanly.

---

## Explicit Non-Goals

- Real inference, real model weights, or a real vLLM/TGI/Triton process.
- Realistic absolute *values*. Thresholds tuned against this simulator prove that alert
  wiring works, **not** that the thresholds are correct for any real workload. (This does
  not excuse incoherent behaviour: the *relationships* between signals must hold — see
  Deliverable A. Plausible relationships, arbitrary magnitudes.)
- Any causal link between simulated GPU load and simulated LLM load. They are driven
  independently by design — see the GPU/LLM decision.
- Modelling batching, KV-cache eviction, speculative decoding or scheduler internals.
- Any change to `terraform/`, node sizing, or the GPU simulation itself.
- Multi-tenancy *mechanics* — quotas, fairness, admission priority, cross-tenant
  isolation — and multi-node serving topologies (tensor/pipeline parallelism, prefill
  disaggregation).
  **This does not conflict with the two-Deployment design.** `llm-steady` and
  `llm-saturated` are two independent simulator instances that happen to render as two
  tenants on one dashboard; nothing arbitrates between them, and nothing should.

---

## References

- vLLM production metrics: https://docs.vllm.ai/en/latest/serving/metrics.html
- vLLM metrics source (bucket boundaries): `vllm/engine/metrics.py` at the pinned tag
- Prometheus exposition format: https://prometheus.io/docs/instrumenting/exposition_formats/
- Histograms and `histogram_quantile` caveats: https://prometheus.io/docs/practices/histograms/
- Existing repo idioms to copy: `manifests/alerts/gpu-prometheusrule.yaml` (derived series,
  self-disabling clause), `scripts/config.sh` (contract assertions),
  `manifests/dashboards/dcgm-configmap.yaml` (self-contained dashboard ConfigMap)
