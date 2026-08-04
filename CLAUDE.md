# CLAUDE.md

This repo is a **GPU-free test bench for AI observability**: it simulates the GPU and vLLM
metric surfaces with *known ground truth*, then verifies everything downstream of the scrape
— recording rules, dashboards, alerts, SLOs — against that truth. The simulator is the
instrument; **verification is the product**. A dashboard here can be *graded*, because the
rig knows the right answer.

## Map

| Path | What it is |
|---|---|
| `scripts/llm-sim.py` | stdlib-only vLLM metric simulator; `--selftest`; polls its profile every 10s and applies it **without restarting** |
| `manifests/llm/10-profiles.yaml` | load profiles (**JSON, not YAML**) + the capacity arithmetic that derives every number |
| `manifests/llm/20-simulators.yaml` | the two fixture tenants `llm-steady` / `llm-saturated` |
| `manifests/llm/extras/` | opt-in `llm-driven` — the only tenant scripts may drive |
| `manifests/alerts/llm-prometheusrule.yaml` | recording rules, alerts, and the TTFT SLO (a ratio **at** a bucket boundary) |
| `scripts/verify.sh` | numbered acceptance checks (L1…) — invariants only |
| `scripts/check-vllm-buckets.py` | CI canary for upstream vLLM bucket/metric-set drift |
| `scripts/config.sh` | single source for version pins, names, labels; asserts cross-file invariants |
| `charts/` + `scripts/chart-build.py` | Helm chart, assembled into gitignored `dist/` |
| `terraform/{eks,gke}` + `terraform/modules/contract` | clusters; `contract` holds **cross-cloud identity constants only** — sizing stays in the roots |
| `kind/gpu-sim.yaml` | local cluster — **single node** |
| `Taskfile.yml` | `task selftest` / `rule-tests` / `drift-test` / `chart` / `dashboards` / `compose` |

## Iron rules

1. **Fixtures are fixtures.** `llm-steady` and `llm-saturated` hold the states `verify.sh`
   asserts. Never scale, drive, or retune them — `drive-llm-load.sh` refuses to, on purpose.
   Anything dynamic targets `llm-driven` in `extras/`.
2. **Profiles are JSON** (the simulator is stdlib-only; Python has no stdlib YAML).
   `model_name` is an **identity**, unique per Deployment — recording rules aggregate by it
   and `config.sh` asserts it. Two Deployments sharing one merge into a meaningless series.
3. **Tune against `llmsim_capacity_rps`, never the base-latency arithmetic.** Capacity uses
   the *congested* ITL. The incident that taught this is recorded in `10-profiles.yaml`.
4. **Bucket lists are upstream vLLM's, transcribed verbatim.** Never "fix" a boundary;
   `check-vllm-buckets.py` watches drift. `histogram_quantile` interpolates *inside* a
   bucket — prefill p95 reads **3.03× high** here and on real vLLM. Means (`_sum/_count`)
   are exact and additive; quantiles are neither. An SLO threshold must **be** a boundary:
   there is no 2.0 in `TTFT_BUCKETS`, so `le="2"` matches nothing and reads green forever.
5. **Poll any check that races its producer** — never single-shot. Written down at
   `7ef0aa9` "after the fourth time it bit".
6. **Write expected values into a check's comment before running it.** A threshold chosen
   after seeing the data is not a test.
7. **`verify.sh` asserts invariants only.** A finding with a shelf life ("the platform has
   this weakness at these settings") belongs in a scenario script you invoke —
   `drive-llm-load.sh` is the pattern — never in CI.
8. **Version pins live twice** — `Chart.yaml` and `config.sh` — and `chart-build.py`
   cross-checks them **by dependency name**. Move both together. A conditional dependency
   is still fetched when its condition is false.
9. **Dashboards:** the repo JSON is the source of truth and the `uid` never changes (the
   ConfigMap is rebuilt from the file on every install). grafana.com uploads are *derived*
   (`task dashboards`); published ids are GPU **25618**, LLM **25620** — republish as a
   **revision** of the same id, never a new upload.
10. **`install.sh` flags are positional with strict unknown-argument rejection** — a typo'd
    flag must fail loudly, not silently do the non-flag thing.
11. **One logical change per commit** (`CONTRIBUTING.md`). Subjects state the change; bodies
    carry the reasoning — the commit log is part of the documentation.
12. **Docs drift is a known failure class.** Counts ("emits N metrics") and ids in prose
    have been corrected repeatedly — when touching docs, re-verify every number against the
    code it describes, and run the cheap gates before landing, not after.
13. **Prose style:** em dashes have been deliberately stripped from the README and catalog
    pages twice. Don't reintroduce them there.
14. **Containers run `readOnlyRootFilesystem`** — hence `PYTHONDONTWRITEBYTECODE`; scripts
    stay dependency-free.
15. **Terraform:** commit `.terraform.lock.hcl`, never `*.tfvars` (examples only). GKE's
    `node_count` is **per zone**; EKS's is absolute.
16. **Outstanding work is marked where it lives — there is no TODO file, and adding one
    would be a mistake.** Open items carry a `⚠️` in the file that owns them: a CHANGELOG
    entry, `manifests/dashboards/README.md`, a prompt's acceptance criteria. To find what
    is open, grep the markers and skip anything struck through. When you finish one,
    **strike it and say what happened** — `⚠️ ~~the claim~~ **DONE — <what, when>**` — never
    delete it, "because the reasoning outlives the action". A backlog file would be a
    second copy of every one of these: unverifiable (`doc-claims` compares prose to code,
    not intentions to reality) and the first thing here to rot.

## Working loop

- **Before landing anything: `task preflight`** — selftest, compose-selftest, drift-test,
  doc-claims, rule-tests, chart in one command. These are the gates that would have caught
  most of the repo's correction commits. `doc-claims` (`scripts/check-doc-claims.py`)
  mechanises the recurring prose-drift class: dashboard ids vs the catalog README, "emits
  N" claims vs what `--print` renders.
- **Cluster loop:** phase 1 `terraform apply` / `kind-up.sh`, phase 2
  `./scripts/install.sh <eks|gke|local> [--skip-monitoring]`, then `./scripts/verify.sh`.
  `LITE=1` fits a 4 GiB runtime. `KPS_RELEASE=<name>` for BYO Prometheus.
- **CI:** the two kind legs (`full`, `lite`) are **not** equal and `lite` is the critical
  path — measured across three runs on 2026-08-04, `full` took 5m19s-6m25s and `lite`
  6m39s-8m32s, all of the difference being inside `verify.sh`. Their names are required
  checks — matrix values are part of the name. Docs-only changes skip the cluster.
  ⚠️ That measurement predates the poll-budget and port-forward fixes and **still needs**
  re-deriving from a run after those land.
- **Review discipline:** specs and plans get **one adversarial review round, then
  implementation** — after one round the remaining risk is empirical (a timing, a default,
  a command's exact syntax) and a desk can't settle it; the first hours of implementation
  can. Prefer landing via PR: CodeRabbit provides non-author eyes, and same-author
  re-review has demonstrated anchoring. And **reference, don't restate**: a number or fact
  stated in two places is a fork waiting to disagree — state it once in the file that owns
  it and point there (`doc-claims` exists because prose kept forking from code).

## Prompt files

`prompt-*.md` in the root are task specifications (currently **gitignored** — decide
deliberately if that should change). House conventions: numbered W-items; a Background of
**verified facts** with `file:line` citations and the date they were read; an effort table
("estimates from reading the code — **treat the ordering as firmer than the numbers**, and
re-derive the largest line"); Non-goals; acceptance criteria written before the work.
Price instruments as code **plus 25–35% verification** — the selftest is where estimates
overrun. Write the prompt for the *next* work item only; implementing Wn is what makes
Wn+1's prompt honest.

## Mottoes

- When a reading is surprising, **suspect the instrument before the world**.
- An invented number presented as a modelled one is the exact failure this repo exists to
  prevent.
