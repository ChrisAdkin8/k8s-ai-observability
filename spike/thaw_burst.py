#!/usr/bin/env python3
"""SPIKE — is the freeze/thaw burst in prompt-fault-injection.md W3.2 real?

Run from the repo root:  python3 spike/thaw_burst.py

A frozen simulator must keep serving /metrics while its counters stop. The obvious
implementation — skip advance_to() while frozen — leaves the simulated clock behind
wall clock, and advance_to() processes EVERY event at or before its target, so the
first call after the thaw replays the whole frozen interval into one scrape gap.
"""
import importlib.util

spec = importlib.util.spec_from_file_location("llmsim", "scripts/llm-sim.py")
llmsim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llmsim)

FREEZE_AT, THAW_AT = 600.0, 900.0          # a 5 minute freeze, the length a `for: 5m` needs


def warmed(seed=7):
    p = llmsim.validate_profile({"model_name": "d", "arrival_rate_rps": 1.8, "seed": seed})
    sim = llmsim.Simulator(p, start_time=0.0)
    t = 0.0
    while t < FREEZE_AT:
        t += 1.0
        sim.advance_to(t)
    return sim


s = warmed()
before = s.generation_tokens_total
s.advance_to(FREEZE_AT + 30.0)
normal = s.generation_tokens_total - before
print(f"baseline : {normal} generation tokens in a normal 30s scrape interval")

s = warmed()
frozen = s.generation_tokens_total
running = len(s.running)
s.advance_to(THAW_AT)                       # naive: nothing advanced while frozen
burst = s.generation_tokens_total - frozen
print(f"naive    : +{burst} tokens in ONE scrape gap = {burst / normal:.0f}x normal")

s = warmed()
frozen = s.generation_tokens_total
skew = THAW_AT - FREEZE_AT
s.advance_to(THAW_AT - skew)                # offset: simulated clock resumes where it stopped
first = s.generation_tokens_total - frozen
s.advance_to(THAW_AT + 30.0 - skew)
second = s.generation_tokens_total - frozen - first
print(f"offset   : +{first} on the first post-thaw call, then +{second} "
      f"({second / normal:.2f}x normal)")
print(f"\nthe frozen state held {running} requests running — the gauge W3.5's detector "
      f"needs on the other side of its conjunction")
