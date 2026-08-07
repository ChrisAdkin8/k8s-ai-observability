# Analysis: feature gaps and developer-community adoption

> Written 2026-07-31, against `main` at `1bf0b58` (post-0.4.0). Untracked by the
> `prompt-*.md` glob in `.gitignore`, on the same terms as the authoring briefs.
>
> This is **analysis and opinion**, not a decided plan. Where it disagrees with a
> decision already recorded in a brief, the brief wins until you change it — several
> recommendations below explicitly propose reversing a stated non-goal, and say so.

## The headline: this is not a feature problem

| | |
|--|--|
| First commit | **2026-07-30** — the repo is two days old |
| Commits / releases | 45 / 4 (`v0.1.0` … `v0.4.0`) |
| Tracked LOC | ~15,300 (excluding `.terraform`) |
| Stars / forks / watchers | **0 / 0 / 0** |
| Open issues | 0 · Discussions **off** · homepage URL **empty** |
| Topics | 12, well chosen |
| Published dashboards | 25618 (GPU), 25620 (vLLM) |

The engineering quality is well ahead of what a two-day-old repo has any right to be:
assertions that fail before anything is created, promtool tests on both sides of every
threshold, a weekly upstream drift check, a parity contract asserted from two independent
producers, and CI that stands the whole stack up on a free runner. The documentation is
unusually honest — the "what transfers and what doesn't" table is the kind of thing most
projects will not write about themselves.

**So the binding constraint is that nobody knows it exists, not that it lacks features.**
Feature gaps are catalogued below because they are real, but leading with them would
misrepresent where the leverage is.

---

## Priority 1 — do these first

### 1. A public, live, read-only Grafana

Highest leverage by a distance. The product *is* dashboards, and the current fastest proof
still asks a stranger to `git clone` and run Docker. A clickable board collapses
time-to-understanding to zero.

Crucially, **a traffic source already points at you**: the grafana.com catalog pages for
25618 and 25620. Put a demo link on those pages and in the README and the funnel becomes
catalog → live board → repo. Grafana Cloud's free tier or a small VM running the compose
stack both work.

### 2. Publish the simulator as a container image

Today `llm-sim.py` is a file *inside this repo*. As `ghcr.io/<org>/vllm-metrics-sim:0.4.0`
it becomes a tool anyone can drop into *their* stack to test *their* vLLM dashboards —
which is how infrastructure tools actually spread. It turns the repo from a destination
into a dependency.

⚠️ **This reverses an explicit non-goal** stated in `prompt-chart.md`,
`prompt-packaging.md` and `prompt-fidelity.md`: *"A container image for the simulator, or
any `pip install`. It stays stdlib-only Python mounted into a stock image."*

The argument for revisiting: that constraint conflates two separate things — how *this rig*
runs the simulator, and how *other people* consume it. You can publish an image built from
the same file without changing `install.sh` at all. The stdlib-only property is precisely
what makes the image trivial to build (a `FROM python:3.12-slim` and a `COPY`).

### 3. Ship the Helm chart

Already fully specified in `prompt-chart.md` and still unimplemented — there is no
`charts/` directory. Its own prerequisites (W4.1–W4.3) landed in 0.4.0, so it is unblocked.

`--skip-monitoring` solved the *hard* half of the BYO story; the chart is the `helm install`
one-liner people expect before they will try anything. Note that W-C2 is the real work:
`.Files.Get` cannot read outside the chart directory, and this repo refuses second copies
of anything.

### 4. SLO burn-rate rules

The best *content* gap, and squarely on-brand.

The README promises that "SLO definitions" transfer, and `docs/` mentions SLOs — but there
are only 7 alerts and no multi-window, multi-burn-rate examples anywhere. Getting SLO
wiring right is genuinely hard, people search for tested examples, and this rig is the
ideal place to demonstrate one: you can drive a tenant through a burn window on demand and
assert the result with promtool.

⚠️ Listed as a non-goal in `prompt-packaging.md` and `prompt-chart.md`. Worth promoting.

### 5. Launch it properly

The pitch — *test your GPU and LLM observability without a GPU* — is genuinely novel and
easy to explain in one sentence.

- Channels: r/kubernetes, CNCF Slack (`#prometheus`, `#grafana`), the vLLM community,
  Grafana Community forums, Hacker News.
- Turn on **Discussions**; set the **homepage** to the live demo.
- Missing community health files: `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue templates,
  PR template. `dependabot.yml` and `CONTRIBUTING.md` are already in place and strong.
- Seed a handful of `good first issue`s.

---

## Feature gaps, ranked

### Metric coverage: 13 of 38

The drift check reports the gap honestly and prints it in full, which is the right design —
visible distance beats silent distance. But some absences limit real dashboards more than
others:

| Missing | Why it matters |
|--|--|
| `vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds` | The prefill/decode split is *the* thing people building disaggregated serving want to see. **Biggest single win.** |
| `vllm:iteration_tokens_total` | Batch-efficiency panels |
| `kv_block_lifetime_seconds`, `kv_block_reuse_gap_seconds`, `kv_block_idle_before_evict_seconds` | Cache-behaviour work; pairs with the prefix-cache panel already shipped |
| LoRA (`vllm:lora_requests_info`), multimodal (`vllm:mm_cache_*`) | Whole workload classes with no representation |

### No simulated inference gateway or router

The interesting layer is now *above* a single vLLM: llm-d, the vLLM production stack,
KV-cache-aware routing, the Gateway API Inference Extension. A simulated router doing
queue-depth-aware balancing across the two existing tenants would be a genuinely
differentiated third domain — and it is the same trick already pulled twice.

### Metrics only, no traces

Observability is not just Prometheus. A simulated OTLP trace stream for the request path
would broaden the audience considerably.

### No Alertmanager routing

Seven alerts fire into nothing. A worked routing / grouping / inhibition example is small
and immediately useful.

### MIG / GPU sharing unmodelled

`mig-faker` runs with 0 desired replicas. MIG partitioning is a live operational concern
for anyone running shared GPUs.

---

## What I would *not* do

- **Chase metric-count parity.** 13/38 with a visible, checked gap beats 38/38 unverified.
  Add metrics that unlock *a panel someone wants*, not to move a number.
- **k3d / minikube support.** Correctly a non-goal. kind works, and each extra target
  multiplies the test matrix.
- **A documentation site.** Eight files in `docs/` are well-organised and navigable.
  Premature.
- **Terraform templating in the chart.** Correctly excluded.

---

## One presentation change

The README opens with what the project *is*. The strongest thing about it is a **claim
about the reader's problem**: you cannot test GPU or LLM alerting without a GPU, which is
why your dashboards and alert thresholds are untested.

Lead with the problem, then the 60-second `docker compose` command. The reframing costs
nothing and is what earns the second paragraph.

---

## Open items carried from earlier in the session

- **The compose CI job** has now run: failed once (readiness ceiling tuned to the fast
  path — Grafana was a full minute behind Prometheus on first-run SQLite migrations),
  fixed in `6d304fd` (60s → 180s, and it now names which service lagged), and green since
  on both a branch and `main`.
- **The full BYO demonstration** (`prompt-packaging.md` acceptance criterion 5) is still
  unproven end-to-end. It needs a clean cluster with a *foreign* Helm release name, which
  means tearing down the local kind cluster. Every other part of that path is tested.
