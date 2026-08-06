#!/usr/bin/env python3
"""SPIKE — derive the W6 KV-exhaustion profile, and check it stays isolated.

Every number quoted in prompt-fault-injection.md's W6 comes from this file. Run it
from the repo root:  python3 spike/kv_profile.py

It advances the real Simulator from scripts/llm-sim.py with no cluster and no
Prometheus, samples kv_cache_usage() at 30s (the rule evaluation cadence) over ten
simulated minutes after a five minute warmup, and reports the WORST case over eight
seeds — because both fixtures ship "seed": null and the live tenant draws a fresh
seed on every start.

Three quantities, because the drill has to fire ONE alert and leave two silent:
  kv usage min   -> LLMKVCacheSaturated  (> 0.9 held for 5m)
  queue max      -> LLMQueueBacklog      (> 50 held for 5m)
  bucket p95     -> LLMHighTTFT          (> 2s held for 2m)

p95 is computed the way the alert computes it — histogram_quantile over TTFT_BUCKETS
— and NOT from the raw samples, because those are different numbers here (CLAUDE.md
rule 4).
"""
import importlib.util
import statistics

spec = importlib.util.spec_from_file_location("llmsim", "scripts/llm-sim.py")
llmsim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llmsim)

BUCKETS = llmsim.TTFT_BUCKETS
SEEDS = (1, 5, 7, 42, 808, 1234, 31337, 99991)
EVAL_INTERVAL = 30.0
WARMUP = 300.0
WINDOW_MINUTES = 10


def bucket_p95(hist):
    """histogram_quantile(0.95, ...): find the bucket, interpolate across it."""
    if not hist.count:
        return float("nan")
    target = 0.95 * hist.count
    cum, lower = 0, 0.0
    for i, bound in enumerate(hist.bounds):
        prev = cum
        cum += hist.buckets[i]
        if cum >= target:
            width = cum - prev
            return lower + (bound - lower) * ((target - prev) / width if width else 0.0)
        lower = bound
    return lower


def run(rate, kv, prompt_mean, seed):
    profile = llmsim.validate_profile({
        "model_name": "sim-llama-3-8b-driven",
        "arrival_rate_rps": rate, "max_concurrency": 16, "max_in_flight": 176,
        "prompt_tokens": {"mean": prompt_mean, "stddev": prompt_mean // 4},
        "generation_tokens": {"mean": 256, "stddev": 64},
        "base_ttft_seconds": 0.08, "base_itl_seconds": 0.015,
        "kv_cache_tokens_capacity": kv, "prefix_cache_hit_rate": 0.25,
        "finish_reasons": {"stop": 0.90, "length": 0.09, "abort": 0.01},
        "seed": seed,
    })
    sim = llmsim.Simulator(profile, start_time=0.0)
    t = 0.0
    while t < WARMUP:                       # reach steady state before sampling
        t += EVAL_INTERVAL
        sim.advance_to(t)
    sim.h_ttft = llmsim.Histogram(BUCKETS)  # measure the window, not the warmup
    usage, waiting = [], []
    end = WARMUP + WINDOW_MINUTES * 60
    while t < end:
        t += EVAL_INTERVAL
        sim.advance_to(t)
        usage.append(sim.kv_cache_usage())
        waiting.append(len(sim.queue))
    return {"usage_min": min(usage), "usage_mean": statistics.fmean(usage),
            "run_min": min_running(sim), "wait_max": max(waiting),
            "p95": bucket_p95(sim.h_ttft)}


def min_running(sim):
    return len(sim.running)                 # last reading, diagnostic only


def worst(rate, kv, prompt):
    rows = [run(rate, kv, prompt, s) for s in SEEDS]
    return {"usage_min": min(r["usage_min"] for r in rows),
            "usage_mean": statistics.fmean(r["usage_mean"] for r in rows),
            "wait_max": max(r["wait_max"] for r in rows),
            "p95": max(r["p95"] for r in rows)}


CASES = [
    # rate, kv capacity, prompt mean, note
    (0.4, 32768,  512, "the SHIPPED llm-driven profile"),
    (1.2,  4096, 3072, "population empties inside the window"),
    (1.8, 32768,  512, "shipped capacity, steady-fixture rate"),
    (1.8,  6144,  512, "capacity chosen from the MEAN"),
    (1.8,  4096,  512, ""),
    (1.8, 10240, 3072, "sits exactly on the > 0.9 line"),
    (1.8,  8192, 3072, "<- the candidate"),
    (2.4, 12288, 2048, "queue builds, TTFT breaks isolation"),
    (2.4, 16384, 3072, ""),
]

print(f"worst case over {len(SEEDS)} seeds, {WINDOW_MINUTES}m sampled every "
      f"{EVAL_INTERVAL:.0f}s after a {WARMUP:.0f}s warmup\n")
print(f"{'rps':>4} {'kv_cap':>7} {'prompt':>6} | {'usage min':>9} {'usage mean':>10} "
      f"{'queue max':>9} {'p95 TTFT':>8} | {'fires':>5} {'isolated':>8}  note")
print("-" * 104)
for rate, kv, prompt, note in CASES:
    r = worst(rate, kv, prompt)
    fires = r["usage_min"] > 0.9                       # LLMKVCacheSaturated
    isolated = r["wait_max"] <= 50 and r["p95"] <= 2.0  # the two that must stay quiet
    print(f"{rate:>4} {kv:>7} {prompt:>6} | {r['usage_min']:>9.3f} {r['usage_mean']:>10.3f} "
          f"{r['wait_max']:>9} {r['p95']:>8.3f} | {'YES' if fires else 'no':>5} "
          f"{'YES' if isolated else 'NO':>8}  {note}")

print("\nseed sensitivity at 1.8 rps / kv 6144 / prompt 512 — one seed is one sample:")
for seed in SEEDS:
    r = run(1.8, 6144, 512, seed)
    print(f"  seed {seed:>6}: usage min {r['usage_min']:.3f}")
