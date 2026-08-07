# Spike evidence — scaffolding, tracked on purpose

Written on 2026-08-06 to settle the empirical questions in `prompts/prompt-fault-injection.md`
(ROADMAP.md item 1, fault injection) before any of it was implemented. Every measured
number quoted in that prompt comes from this directory.

**This is scaffolding, not a repo feature.** Nothing here is executed by `task preflight`,
CI or the chart, and nothing here ships.

⚠️ **EXECUTED AND VALIDATED ARE DIFFERENT THINGS, AND ONLY THE FIRST EXCLUDES YOU.**
Nothing here runs, but everything here is still *checked*: the repo's validators walk
`git ls-files`, so a file added to this directory is inside `check-second-copy.py` and,
if it is markdown, inside `check-doc-claims.py` too. Adding something here is not a way
to stay out of CI's way — assuming otherwise is what produced the collision below.

⚠️ **THIS FILE USED TO SAY IT SHOULD NOT BE ON `main`.** The heading read "NOT for main"
and the paragraph above ended "it should not merge to `main` as-is". `0d426e5` contradicted
that the same day it was written, and the contradiction is resolved here in favour of
tracking, on the argument that tracked the briefs one commit earlier: every measured number
in `prompt-fault-injection.md` comes from these scripts, and a reader who cannot rerun them
has to take the numbers on trust. That is the failure this repo exists to prevent. What
survives the spike is still rewritten in its real place — the list below is the outstanding
work, not a description of what already happened.

⚠️ **The collision that caused is worth knowing about.** `stale-rules.yaml` ends in
`-rules.yaml`, which is what the canonical-copy guard matches, repo-wide. It held the chart
job red on `main` for a day with nobody noticing, because that job is not a required check.
`scripts/check-second-copy.py` owns the allowlist and the reasoning for sparing `spike/`;
read it there rather than trusting this paragraph, which is a pointer and not the rule.

What survives the spike is:

- `spike_test.yaml` + `stale-rules.yaml` -> the promtool cases belong in
  `tests/rules/llm-rules_test.yaml` once the `LLMMetricsStale` alert lands (W3).
- `kv_profile.py` -> the derivation W6 must re-run; its output belongs in the drill's
  comment, not in a committed file.
- `thaw_burst.py` -> becomes a `--selftest` assertion in `scripts/llm-sim.py` (W3.3).
- `worker_freeze.py` -> the freeze belongs in `worker()` itself (W3.2). Three fixes have
  to travel with it or it is worse than not shipping: the clock **offset** rather than a
  skipped `advance_to()`, the profile poll on the **wall** clock, and the event sleep
  against the **simulated** one. The second makes a freeze permanent; the third
  busy-spins a core for the life of the pod while serving correct output.
- `stale_e2e.sh` -> ⚠️ **this one has no obvious heir, and that is the decision.** It is
  the only thing here that answers the question the whole item exists to ask — does the
  alert fire against a real frozen simulator scraped by a real Prometheus, rather than
  against a series we hand-wrote to look the way we assumed. promtool cannot answer it.
  Retire it into `verify.sh` only if the drill ends up covering the same ground; until
  then it is the item's acceptance evidence and deleting it loses the finding.

Run everything from the repo root.

| Command | Question it settles |
|---|---|
| `./scripts/extract.sh rules spike/ && (cd spike && promtool test rules spike_test.yaml)` | can single-tenant loss fire `LLMMetricsAbsent`? does the stale detector's `and` match, and does its idle-tenant negative hold? |
| `python3 spike/kv_profile.py` | what KV profile fires `LLMKVCacheSaturated` for 5m while leaving `LLMQueueBacklog` and `LLMHighTTFT` silent? |
| `python3 spike/thaw_burst.py` | is the freeze/thaw replay burst real, and does the clock-offset fix remove it? |
| `python3 spike/worker_freeze.py` ⚠️ | does the clock-offset design survive inside the **real** `worker()`, with the lock held and the profile poll running beside it? |
| `bash spike/stale_e2e.sh` ⚠️ | does `LLMMetricsStale` fire against a **real frozen simulator scraped by a real Prometheus**, rather than a hand-written series? and how long does it take? |

⚠️ **The last two need W0's `faults` block and W3's freeze knob, which do not exist yet.**
They ran green on 2026-08-07 against a spike implementation on `spike/fault-injection`, which
was deleted afterwards as stage 3 says it should be. They are kept because every number the
prompt now quotes for W3 comes from them. Re-point them at the real implementation when W0
and W3 land — `worker_freeze.py` is close to the `--selftest` W3.3 asks for, and
`stale_e2e.sh` is close to the drill W3.4 describes.

`extract.sh` drops `llm-rules.yaml` and `gpu-rules.yaml` here; both are gitignored
build products of `manifests/alerts/`, so they are not committed.

## Results, so a reader does not have to run them

- One tenant of two absent for 15m fires **nothing**: `absent()` is a global operator.
  Both tenants absent fires at 5m. Asserted in the same promtool run, so the negative is
  not passing vacuously.
- `vllm:num_requests_running > 0 and rate(vllm:generation_tokens_total[10m]) == 0` matches
  across the two metric names. Aggregating one side with `sum by (model_name)` yields
  `got:[]` — green forever, which is what that mistake looks like in production.
- Naive freeze replays 135,636 tokens into one scrape gap, **10x** a normal 30s interval.
  The clock-offset fix yields exactly 0, then exactly 1.00x.
- KV exhaustion is isolable at **1.8 rps** (queue max 5, bucket p95 TTFT 0.696s) and **not**
  at 2.4 rps (queue max 21, p95 6.453s, over the 2s `LLMHighTTFT` threshold). TTFT is the
  binding constraint, not queue depth.
- A capacity chosen from the MEAN never fires: 1.8 rps / kv 6144 / prompt 512 has a mean of
  0.939 and a minimum of 0.358 across eight seeds. Both fixtures ship `"seed": null`, so the
  live tenant draws a fresh seed on every start.

### From the second spike, 2026-08-07

- **The clock-offset design is right and the wiring around it is not.** Built inside the real
  `worker()`, it needs two further one-line fixes: the profile poll keys off the SIMULATED
  clock, which makes a freeze permanent because the poll is the only path a thaw arrives by;
  and the event sleep compares a simulated `due` against `time.monotonic()`, which busy-spins
  at **70 passes/s against 2/s** once the clocks diverge. Neither is visible from the design.
- **`validate_profile` accepts unknown keys silently**, so `{"freze": true}` validates,
  applies, bumps the generation counter and injects nothing.
- **Detection latency is the rate() window PLUS the `for:`, in series.** Measured 56s + 30s =
  87s on a `[1m]`/`30s` twin, so the shipped `[10m]`/`5m` is about **fifteen minutes**.
- **`llm-driven` cannot be frozen reliably at its shipped 0.4 rps**: the population hits zero,
  the `running > 0` guard excludes it, and the drill reports a silence it manufactured. Two
  runs minutes apart held 6 requests and 0.
- **`LLMMetricsStale` fires against a real frozen simulator**, driven tenant only, with the
  idle-tenant negative shown against live data (guardless 2 series, guarded 1).
- **A third `zsh` vs `bash` split**: `[ "$(f "q with \"quotes\"")" != "0" ]` works in zsh and
  is `[: too many arguments` in bash. `shellcheck` is silent at every severity and neither
  repo checker looks for it. The repair is `verify.sh`'s assign-then-test habit.
