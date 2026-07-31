# Contributing

Issues and pull requests are welcome. This file exists because the repo has a handful of
invariants that are **not** guessable from the code, and every one of them, if broken,
produces a *green install with something silently wrong* rather than an error. They are
documented where they apply; this collects them so you do not have to find them first.

## Before you push

```sh
task selftest          # simulator exposition — no cluster, ~1s
task compose-selftest  # the compose GPU producer vs the DCGM surface contract — no docker, ~1s
task rule-tests        # promtool over every alert and recording rule — needs promtool, ~2s
task drift-test        # the drift check's matching rules, against a fixture — no network, ~1s
```

All four run in CI as the `fast` job, so a failure here is a failure there. The full stack
job takes ~6 minutes and stands up a real kind cluster; you do not need to run it locally
to open a PR, but `task local:up` does exactly what CI does if you want to.

If you changed shell, `bash -n scripts/*.sh` is the next thing CI checks; every `*.py` in
the repo goes through `python3 -m py_compile` beside it, so a syntax error in the release
tooling no longer waits until someone tries to cut a release.

There is also a `compose` job that stands the no-Kubernetes stack up and asserts it
through Prometheus and Grafana. It needs docker, not kind, and takes about a minute.

## The invariants

**Break any of these and the install still goes green.** That is why they have assertions
rather than comments, and why the assertions run before anything is created.

| If you change… | You must also… | Enforced by |
|--|--|--|
| A dashboard **filename** in `manifests/dashboards/` | change the `uid` inside it to match, or neither | `assert_dashboard_contract` fails the install |
| The node-pool label or name | keep `terraform/modules/contract`, `scripts/config.sh` and `helm/fake-gpu-operator/values.yaml` in step | `assert_gpu_contract`, `assert_terraform_contract`, `assert_kind_contract` |
| A namespace in `scripts/config.sh` | change the static manifests to match | `assert_manifest_namespaces` |
| `LLM_STEADY_MODEL` / `LLM_SATURATED_MODEL` | change the profile ConfigMaps, and keep them distinct | `assert_llm_contract` |
| `FAKE_GPU_CHART_VERSION` | re-verify the three bullets above it in `config.sh` — the exporter's three series, the ServiceMonitor's selector, and the labels the dashboards and rules join on | **nothing.** A bad bump is green with blank panels |
| `LLM_VLLM_VERSION`, any bucket list, or any `vllm:` name emitted | run `python3 scripts/check-vllm-buckets.py`, then re-derive the expected values in `tests/rules/llm-rules_test.yaml` | the drift check, weekly in CI |
| `K8S_VERSION` | move `kind/gpu-sim.yaml`'s node image onto the same minor | `assert_kind_contract` |
| The DCGM series or labels either producer emits | update `tests/contracts/dcgm-surface.json` and **both** producers — `compose/gpu-metrics-sim.py` and whatever the chart pin gives you | `gpu-metrics-sim.py --selftest` (exact match, in `fast`) and `verify.sh` check 3b (subset, on a cluster) |
| The `release:` label or the sidecar label on anything in `manifests/` | leave them alone — `install.sh` rewrites both at apply time from `RELEASE_LABEL` and `GRAFANA_DASHBOARD_LABEL` | **nothing.** A wrong selector is silent: no scrape, no rules, empty boards |

The three-way naming invariant is the one to read first if you are touching Terraform or
the fake operator: [docs/architecture.md](docs/architecture.md#the-naming-invariant-read-before-editing).

## Things that have bitten us

Each of these cost real debugging time and is documented inline where it applies. They
are listed here because none of them is discoverable until it happens to you.

- **A renamed upstream metric does not fail — it stops matching.** Two vLLM series were
  renamed by the V1 engine and this repo shipped the old spellings for two releases with
  every test green, because every test reads the simulator and the simulator agreed with
  itself. If you touch anything under `vllm:`, the question is not "do the tests pass" but
  "does this match a real deployment". `scripts/check-vllm-buckets.py` is the only thing
  here that points upstream — it watches the metric *set* as well as the bucket
  boundaries, and prints the ~28 upstream metrics this simulator does not emit so that
  distance stays visible rather than silent.
- **A promtool expected percentile can be architecture-dependent.** `histogram_quantile`
  returned `2.4250000000000003` on arm64 and `2.425` on amd64 for the same input; promtool
  compares exactly, so the test passed locally and went red in CI. If you add or change an
  expected value, verify it on amd64 too —
  [tests/](tests/#-check-a-new-expected-value-on-amd64-before-committing-it) has a
  one-command recipe and the list of values known green on both.
- **The fake `nvidia-smi` is injected by the device plugin's `Allocate()` response**, not
  a mutating webhook. The injected env and mounts are invisible in the pod spec, and
  running `nvidia-smi` in the container panics — it is not a fidelity check.
- **`kubectl port-forward` does not survive a long idle.** Anything that polls for minutes
  and then talks to Grafana must re-establish it; `verify.sh` does, and the comment there
  explains what the failure looks like if you forget.
- **A check on a series that appears asynchronously must poll — this has bitten us four
  times.** Nothing here is ready when the object that produces it reports itself created.
  A recording rule does not exist until its next evaluation (30s); a scrape target is
  registered before it is first scraped; the exporter's `exported_pod` label only appears
  after it re-reads topology *and* Prometheus scrapes it again. Assert any of those once
  and you get a confident, specific, wrong failure — `verify.sh` check 3 reported the DCGM
  target missing while 4c found a series *derived from it*, and the compose leg reported
  the rules unloaded while their input sat at 8 series.

  The counterintuitive part, and the reason these read as real faults rather than flakes:
  **they get worse as the machine gets faster.** A quicker start means the assertion runs
  sooner, with less of the producer's warm-up already elapsed. So a change that only
  speeds something up can turn a passing check red, and a green local run proves nothing
  about a fast runner.

  Anything waiting on the DCGM producer polls on `DCGM_POLL_ATTEMPTS` rather than its own
  literal — checks 3, 4c and L4b share it because they wait on the same thing, and two
  matching constants with a comment asking future editors to keep them aligned is not a
  coupling, it is a wish. Keep it **bounded**: a rule that was never applied never appears,
  and that has to fail rather than hang. Check 4d deliberately keeps its own shorter window
  and says why — by the time it runs the metric is known to exist, so it is budgeting
  annotation propagation, not a first scrape.

## Editing dashboards

Edit in Grafana, then **Dashboard settings → JSON Model**, and copy it back over the file
in `manifests/dashboards/`. Keep the `uid` unchanged. A change made only in the Grafana UI
is reverted on the next install, because the ConfigMap is rebuilt from the file every
time — see [manifests/dashboards/](manifests/dashboards/).

## Docs

Prose lives close to what it describes, and there is a lot of it. If you change behaviour,
the doc most likely to go stale is the one in the same directory — plus
[docs/versions.md](docs/versions.md) if you moved a pinned version, and
[CHANGELOG.md](CHANGELOG.md) always.

The changelog is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format, and its
header explains what MAJOR/MINOR/PATCH mean *for a rig rather than a library* — "breaking"
is about a cluster you already have. New entries go under `[Unreleased]`.

Entries here explain **why**, not just what. That is the house style throughout the repo,
and it is the reason the comments are worth reading: a comment that says what the line
does is noise, one that says why the obvious alternative was wrong is not.

## Commits and PRs

- One logical change per commit. If two things are genuinely independent, they are two
  commits, even when they arrive together.
- Say why in the body, not just what. The diff already says what.
- CI must be green. The `fast` job is seconds; the two full-stack legs are ~6 minutes each
  and run in parallel.

## What is deliberately out of scope

Worth knowing before you propose one of these — each is a considered omission, with the
reasoning in [docs/architecture.md](docs/architecture.md):

- `pip install` / any Python dependency. `python3 scripts/llm-sim.py --selftest` must run
  anywhere, with no venv.
- Real GPU hardware, drivers, quota or model weights.
- Dashboards clicked into Grafana rather than shipped as files.

**A published container image for the simulator used to be on this list, and is not any
more.** The rule it rested on — "stdlib-only Python mounted into a stock image, so there
is nothing to build, push or patch" — was reasoning about how *this rig* runs the
simulator, and it still holds there: `install.sh` builds the ConfigMap from
`scripts/llm-sim.py`, the compose path mounts the same file, and `--selftest` runs it
directly with no build step. None of that changes.

What the rule did not cover is how *anyone else* consumes it. As a file inside this repo,
`llm-sim.py` cannot be pointed at someone's own vLLM dashboards without cloning; as a
published image it can. "Nothing to build, push or patch" stops being free at that point
— the cost moves to them.

⚠️ **The `pip install` bullet above is a separate rule and is unaffected.** The image
would ship the same stdlib-only file. The two are usually stated in one breath; only one
of them was reversed.

Scope decision only — **no image is published yet**, and the build must derive from
`scripts/llm-sim.py` rather than committing a second copy of it.
