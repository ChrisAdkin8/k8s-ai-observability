# Spike evidence — NOT for main

Written on 2026-08-06 to settle the empirical questions in `prompts/prompt-fault-injection.md`
(ROADMAP.md item 1, fault injection) before any of it was implemented. Every measured
number quoted in that prompt comes from this directory.

**This is scaffolding, not a repo feature.** Nothing here is wired into `task preflight`,
CI or the chart, and it should not merge to `main` as-is. What survives the spike is:

- `spike_test.yaml` + `stale-rules.yaml` -> the promtool cases belong in
  `tests/rules/llm-rules_test.yaml` once the `LLMMetricsStale` alert lands (W3).
- `kv_profile.py` -> the derivation W6 must re-run; its output belongs in the drill's
  comment, not in a committed file.
- `thaw_burst.py` -> becomes a `--selftest` assertion in `scripts/llm-sim.py` (W3.3).

Run everything from the repo root.

| Command | Question it settles |
|---|---|
| `./scripts/extract.sh rules spike/ && (cd spike && promtool test rules spike_test.yaml)` | can single-tenant loss fire `LLMMetricsAbsent`? does the stale detector's `and` match, and does its idle-tenant negative hold? |
| `python3 spike/kv_profile.py` | what KV profile fires `LLMKVCacheSaturated` for 5m while leaving `LLMQueueBacklog` and `LLMHighTTFT` silent? |
| `python3 spike/thaw_burst.py` | is the freeze/thaw replay burst real, and does the clock-offset fix remove it? |

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
