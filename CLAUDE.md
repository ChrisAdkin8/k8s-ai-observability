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
| `scripts/check-sigpipe.py` | finds pipes whose consumer exits before the producer — see rule 17 |
| `scripts/registry-cache.sh` | opt-in pull-through image caches for `local`; `kind-up.sh` mirrors only the ones actually running, so the default path is unchanged |
| `docs/ci.md` + `docs/releasing.md` | what CI proves; how to cut a release without breaking it |
| `.github/actions/verify-chart/` | the chart's cluster verification, called by CI **and** the publish workflow |
| `scripts/config.sh` | single source for version pins, names, labels; asserts cross-file invariants |
| `charts/` + `scripts/chart-build.py` | Helm chart, assembled into gitignored `dist/` |
| `terraform/{eks,gke}` + `terraform/modules/contract` | clusters; `contract` holds **cross-cloud identity constants only** — sizing stays in the roots |
| `kind/gpu-sim.yaml` | local cluster — **single node** |
| `Taskfile.yml` | **`task preflight`** is the gate before landing; also `selftest`, `compose-selftest`, `doc-claims`, `sigpipe`, `rule-tests`, `drift-test`, `chart`, `dashboards`, `compose`, `cache:*`, `outstanding` |

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
10. **`install.sh` and `teardown.sh` flags are positional, and EVERY unrecognised argument
    is rejected** — a typo'd flag must fail loudly, not silently do the non-flag thing.
    Checking only `$2` is how `--lite` was accepted and ignored.
11. **One logical change per commit** (`CONTRIBUTING.md`). Subjects state the change; bodies
    carry the reasoning — the commit log is part of the documentation.
12. **Docs drift is a known failure class.** Counts ("emits N metrics", "N jobs") and ids
    in prose have been corrected repeatedly. Re-verify every number against the code it
    describes; `doc-claims` mechanises the ones that recur, including this file's.
13. **Prose style:** em dashes stay out of the README and the two `.grafana-com.md`
    catalog pages. Stripped by hand three times; **`doc-claims` now enforces it**, so a
    fourth is a red `preflight` rather than something a reader has to notice.
14. **Containers run `readOnlyRootFilesystem`** — hence `PYTHONDONTWRITEBYTECODE`; scripts
    stay dependency-free.
15. **Terraform:** commit `.terraform.lock.hcl`, never `*.tfvars` (examples only). GKE's
    `node_count` is **per zone**; EKS's is absolute.
16. **Outstanding work is marked where it lives — there is no TODO file, and adding one
    would be a mistake.** Open items carry a `⚠️` in the file that owns them. `task
    outstanding` greps the markers and skips struck ones. When you finish one, **strike it
    and say what happened** — `⚠️ ~~the claim~~ **DONE — <what, when>**` — never delete it,
    because the reasoning outlives the action. The same applies to *evidence*: a long
    justification belongs in the file that owns the work, not in this one, which is loaded
    into every session.
17. **`zsh` is not `bash`, and CI is `bash`.** Two bugs on **2026-08-04** were invisible in
    the local shell. `echo "$x" | grep -q` returned 0 under zsh and **141 under bash** on the same
    input, because a SIGPIPE'd producer only fails the pipeline under `pipefail` — that one
    could have skipped the cluster jobs on a code change. Separately, zsh does not
    word-split unquoted parameters, so a test harness passed `"local --skip-monitoring"` as
    ONE argument and a passing script looked broken. **Test shell with `bash -c`**, never
    interactively. `check-sigpipe.py` covers the first class. ⚠️ **Unverified:** the second
    has no check at all, so a word-splitting bug still reaches CI unaided.

18. **An assertion that only ever passes is not an assertion.** Before trusting a new
    check, break what it watches and confirm it goes red — then fix it. Every `--selftest`
    here pins bugs that were reintroduced deliberately to prove it fails on them, and CI
    drives the chart's render-time assertions and `helm test`'s negative case to failure on
    purpose. A check that has never failed is a guess.

## Working loop

- **Before landing anything: `task preflight`** — selftest, compose-selftest, drift-test,
  doc-claims, rule-tests, chart in one command. These are the gates that would have caught
  most of the repo's correction commits. `doc-claims` (`scripts/check-doc-claims.py`)
  mechanises the recurring prose-drift class: dashboard ids vs the catalog README, "emits
  N" claims vs what `--print` renders.
- **Releases:** `docs/releasing.md`. **Tag locally, run
  `chart-build.py --strict-version`, then push** — the check resolves the tag with `git
  describe`, so it means nothing until the tag exists, and three of four releases on
  2026-08-04 hit a bug this step catches while everything is still private.
- **Cluster loop:** phase 1 `terraform apply` / `kind-up.sh`, phase 2
  `./scripts/install.sh <eks|gke|local> [--skip-monitoring]`, then `./scripts/verify.sh`.
  `LITE=1` fits a 4 GiB runtime. `KPS_RELEASE=<name>` for BYO Prometheus.
- **CI:** four required checks — `fast`, the two kind legs (`full`, `lite`) and
  `chart on kind`. Matrix values are part of the name, so the names live in the ruleset AND
  in `.github/required-checks.txt`; **change the ruleset first, then record it**, or
  `settings-drift` reports the disagreement. Docs-only changes skip the cluster, and `fast`
  gates the five expensive jobs. **Timings live in `docs/ci.md`, not here** — with the run
  id they came from, which you quote with any figure you take from them. They sat in this
  file until 2026-08-05 and drifted: a wrong run id and a total no run produced.
- **Review discipline:** specs and plans get **one adversarial review round, then
  implementation** — after one round the remaining risk is empirical (a timing, a default,
  a command's exact syntax) and a desk can't settle it; the first hours of implementation
  can. Prefer landing via PR: CodeRabbit provides non-author eyes, and same-author
  re-review has demonstrated anchoring. And **reference, don't restate**: a number or fact
  stated in two places is a fork waiting to disagree — state it once in the file that owns
  it and point there (`doc-claims` exists because prose kept forking from code).

## Prompt files

`prompt-*.md` in the root are task specifications. ⚠️ **Unverified:** whether they should
stay **gitignored** has never been decided, only inherited.

House conventions: numbered W-items; a Background of
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
