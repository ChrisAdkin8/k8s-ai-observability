#!/usr/bin/env python3
"""
llm-sim.py — a simulated vLLM inference server.

Emits the metric *shape* of a real vLLM deployment — real metric names, real
metric types, real histogram bucket boundaries — without running a model, so
dashboards, recording rules and alert expressions built against it transfer
unchanged to a real vLLM deployment.

Standard library only: no `pip install`, no image build, no registry. That is a
hard constraint of this repo, and the reason this file is mounted from a
ConfigMap rather than baked into an image.

Run it locally, no cluster needed:

    python3 scripts/llm-sim.py --selftest          # validate the exposition output
    python3 scripts/llm-sim.py --print             # print one scrape and exit
    python3 scripts/llm-sim.py                     # serve on :9401
                                                   # (--port, or LLM_SIM_LISTEN_PORT)

    python3 scripts/llm-sim.py --vllm-surface both # v1 + the superseded v0 names,
                                                   # to see which panels an engine
                                                   # upgrade would break

In the cluster it is mounted from a ConfigMap; see manifests/llm/.

HOW IT WORKS
------------
A background worker advances simulated requests through
arrival -> queue -> prefill -> decode -> completion in *wall-clock time*, and
observes into the metrics exactly as an instrumented server would. Serving
/metrics is a pure read: it never advances a counter or observes a sample.
That matters because `curl`-ing the endpoint during development, and kubelet's
readiness probe, must not perturb what Prometheus sees.

PREFIX CACHING HAS NO LATENCY EFFECT HERE, BY CONSTRUCTION
----------------------------------------------------------
`prefix_cache_hit_rate` moves the two prefix-cache counters and NOTHING else.
On real hardware a cache hit skips recomputation and shortens TTFT. It cannot
here, because prefill is FLAT rather than token-proportional:

    prefill = p["base_ttft_seconds"] * self._jitter()          # _admit()

There is no per-token work for a cached block to remove, so any speedup would
have to be invented. Making prefill token-proportional is a genuine modelling
change rather than a tweak, and it re-derives, in this order:
`service = base_ttft + gen_mean * itl` in capacity_rps(), the 2.74 rps capacity
figure, both shipped profiles in manifests/llm/10-profiles.yaml, the
LLMHighTTFT 2s threshold, verify.sh's L3b bound, and every expected value in
tests/rules/llm-rules_test.yaml.

An honest zero beats a fabricated speedup, and a panel built against these
counters still transfers: what a real deployment plots is the RATIO, and the
ratio is right whether or not the latency here responds to it. --selftest
asserts TTFT is byte-identical across hit rates, so that nobody "fixes" the
flat-prefill model without re-deriving the arithmetic above.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# vLLM histogram bucket boundaries.
#
# Transcribed from vLLM's own metrics definitions. DO NOT invent these:
# histogram_quantile() accuracy is entirely determined by bucket placement, so a
# dashboard or SLO tuned against made-up buckets silently fails to transfer to a
# real deployment — which is the whole point of this simulator.
#
# SOURCE OF TRUTH: vllm/v1/metrics/loggers.py on vllm-project/vllm. These are
# transcribed from it verbatim, in order. `scripts/check-vllm-buckets.py` diffs
# them against that file and runs weekly in CI, because these MOVE — an upstream
# PR to add finer-grained low-end latency buckets was open when they were last
# transcribed. Do not hand-edit one without re-running that check.
#
# ⚠️ THESE WERE WRONG UNTIL THE V1 SYNC. Releases 0.1.0 and 0.2.0 carried the
# v0.6.x layout, whose TTFT tail (15/20/30/45/60/90/120) V1 replaced entirely
# with 20/40/80/160/640/2560. The first sixteen boundaries were identical, so
# nothing looked wrong — and the saturated tenant sits at ~58s, squarely in the
# tail that had diverged, which is precisely where this rig teaches you to read a
# p95. A wrong boundary does not fail: it returns a confident, plausible, wrong
# number. That is why the automated check exists rather than a note to re-check.
# --------------------------------------------------------------------------
# V1's tail runs to 2560s, so the "must cover a fully-queued tenant" concern that
# shaped the old custom tail is now upstream's problem and not ours: a request
# arriving behind a full queue waits
#   (max_in_flight - max_concurrency) / capacity = 160 / 2.74 ~= 58s,
# which lands mid-range rather than anywhere near the top bucket.
TTFT_BUCKETS = [0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1,
                0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0,
                20.0, 40.0, 80.0, 160.0, 640.0, 2560.0]
# ⚠️ p95 TPOT reads HIGH at this rig's operating point, and that is the buckets,
# not the simulator. A full batch models ITL at base_itl x 1.5 = 0.0225s, which
# lands inside the wide (0.01, 0.025] bucket, so histogram_quantile interpolates
# across a 15ms gap and reports ~24ms. Real vLLM has this same layout, so a real
# deployment reads high in the same way and the boundaries must stay exactly as
# transcribed. Do not derive an ITL SLO from the p95 panel at these latencies.
#
# The v0.6.x list was a strict PREFIX of this one — V1 only extended the tail
# (5.0 … 80.0) — so unlike TTFT this one was never wrong at the operating point.
TPOT_BUCKETS = [0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3,
                0.4, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0,
                20.0, 40.0, 80.0]
# Upstream calls this `request_latency_buckets` and shares it across several
# request-scoped histograms. Note the resolution BELOW 1s (0.3/0.5/0.8), which
# the v0.6.x list this repo shipped did not have at all — it started at 1.0, so
# every healthy request fell in one bucket and no sub-second e2e percentile was
# recoverable.
E2E_BUCKETS = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0, 15.0,
               20.0, 30.0, 40.0, 50.0, 60.0, 120.0, 240.0, 480.0,
               960.0, 1920.0, 7680.0]

# --------------------------------------------------------------------------
# vLLM metric SURFACE.
#
# The V1 engine renamed two of the series this file emits. Everything else in
# the surface below — TTFT, e2e, the token counters, request_success, the
# running/waiting gauges — kept its v0 name and needs no mapping.
#
#   vllm:gpu_cache_usage_perc      -> vllm:kv_cache_usage_perc
#       V1 dropped CPU KV-cache offload, so the "gpu_" prefix no longer
#       distinguished anything. Confirmed against the current metrics docs and
#       the V1 metrics design page.
#
#   vllm:time_per_output_token_seconds -> vllm:inter_token_latency_seconds
#       Same measurement, and what this simulator models: seconds between
#       successive output tokens, observed once per request weighted by token
#       count. ⚠️ Current vLLM ALSO exposes a separate, differently-shaped
#       vllm:request_time_per_output_token_seconds (per-request mean rather than
#       per-token). This maps to inter_token_latency_seconds because that is the
#       series with the same meaning; if you need the other one, it is not the
#       same number and should not be aliased onto this histogram.
#
# `both` exists for the case this repo is actually good at: pointing a dashboard
# at one endpoint and seeing which panels survive an engine upgrade. Real vLLM
# emits ONE surface — `both` is a rig affordance, not a fidelity claim.
V0, V1, BOTH = "v0", "v1", "both"
METRIC_SURFACES = {
    #  logical name       v0                                   v1
    "kv_cache_usage": ("vllm:gpu_cache_usage_perc", "vllm:kv_cache_usage_perc"),
    "inter_token":    ("vllm:time_per_output_token_seconds", "vllm:inter_token_latency_seconds"),
}

# ⚠️ A RESHAPE, not a rename — and it is the sharpest upgrade-rehearsal case
# this repo has. VERIFIED against vllm/engine/metrics.py at tag v0.6.6: v0
# exposed prefix caching as a GAUGE OF A RATIO, vllm:gpu_prefix_cache_hit_rate.
# V1 replaced it with TWO COUNTERS, vllm:prefix_cache_queries and
# vllm:prefix_cache_hits.
#
# A panel bound to the v0 gauge cannot be repaired by substituting a name: the
# replacement has to be rate(hits)/rate(queries). Neither of the two renames
# above makes that point, which is why this one is worth emitting under `both`.
#
# Same positional (v0, v1) convention as METRIC_SURFACES, but each side is a
# TUPLE of names — a 1:1 map cannot say "one gauge becomes two counters", and
# special-casing it in render() would put branching in the one path where the
# no-observation-on-scrape rule is easiest to break. scripts/check-vllm-buckets.py
# reads the v0 side of BOTH tables to keep deliberate aliases out of its drift
# report.
#
# The cpu_ variant is deliberately skipped. Nothing here models CPU KV offload
# and V1 dropped it entirely — which is precisely why gpu_ stopped
# distinguishing anything, the same reasoning already recorded for
# gpu_cache_usage_perc in manifests/alerts/llm-prometheusrule.yaml.
METRIC_RESHAPES = {
    #  logical name     v0                                    v1
    "prefix_cache": (("vllm:gpu_prefix_cache_hit_rate",),
                     ("vllm:prefix_cache_queries_total",
                      "vllm:prefix_cache_hits_total")),
}

# What each prefix-cache name carries: (renderer, reading, HELP). A table rather
# than three branches in render(), so that path stays a loop over names.
PREFIX_CACHE_SERIES = {
    "vllm:prefix_cache_queries_total": (
        "counter", "queries", "Prefix cache queries, in terms of number of "
                              "queried tokens."),
    "vllm:prefix_cache_hits_total": (
        "counter", "hits", "Prefix cache hits, in terms of number of cached "
                           "tokens."),
    "vllm:gpu_prefix_cache_hit_rate": (
        "gauge", "ratio", "GPU prefix cache hit rate, 0 to 1. Superseded: the "
                          "V1 engine replaced this gauge with two counters."),
}


def surface_names(key, surface):
    """Every name `key` is emitted under, for the chosen surface.

    Reads whichever table holds `key`. METRIC_SURFACES entries carry one name
    per side and METRIC_RESHAPES entries carry several; normalising here is what
    lets render() and --selftest stay indifferent to which kind a metric is.
    """
    if key in METRIC_SURFACES:
        v0, v1 = [(name,) for name in METRIC_SURFACES[key]]
    else:
        v0, v1 = METRIC_RESHAPES[key]
    if surface == V0:
        return list(v0)
    if surface == V1:
        return list(v1)
    return list(v1) + list(v0)   # v1 first: it is what a current deployment emits


# Memory safety. The profile is operator-editable, so the ceiling cannot depend
# on it alone: a hand-edited max_concurrency must not be able to exhaust memory.
HARD_MAX_IN_FLIGHT = 10_000

# Decode slows as the running batch fills:
#   itl = base_itl * (1 + CONGESTION_AT_FULL_LOAD * load),  load = running / max_concurrency
# Declared once because _admit() and capacity_rps() must agree on it. They did not:
# capacity_rps() used the uncongested base_itl and so overstated sustainable
# throughput by this factor, which is how the shipped "steady" profile came to be
# tuned above its own capacity.
CONGESTION_AT_FULL_LOAD = 0.5

DEFAULT_PROFILE = {
    "model_name": "sim-llama-3-8b-steady",
    "arrival_rate_rps": 1.8,
    "max_concurrency": 16,
    "max_in_flight": 176,
    "prompt_tokens": {"mean": 512, "stddev": 128},
    "generation_tokens": {"mean": 256, "stddev": 64},
    "base_ttft_seconds": 0.08,
    "base_itl_seconds": 0.015,
    "kv_cache_tokens_capacity": 32768,
    # Prefix caching. 0.0 by default because a hit rate is a property of the
    # WORKLOAD — how much prompt prefix successive requests share — not of the
    # server, so there is no defensible number to default to. The shipped
    # profiles set it; manifests/llm/10-profiles.yaml says where those two
    # numbers come from, which is not the same place capacity_rps comes from.
    #
    # 0.0 means a cache that is consulted and always misses, NOT one that is
    # switched off: queries still advance, hits stay at zero, and both series
    # are still emitted. An absent series and a zero one are different things to
    # a panel.
    "prefix_cache_hit_rate": 0.0,
    # vLLM's KV block size. Hits are quantised to whole blocks upstream — a
    # partial trailing block is not cacheable — so they are here too. 16 is
    # vLLM's default.
    "kv_block_tokens": 16,
    "finish_reasons": {"stop": 0.90, "length": 0.09, "abort": 0.01},
    "seed": None,
}


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------
class ProfileError(ValueError):
    """Raised for a malformed profile. Never fatal — the last good one is kept."""


def validate_profile(raw):
    """Return a validated profile dict, or raise ProfileError.

    A malformed profile must never crash the process: restarting resets every
    counter, which is precisely what polling a ConfigMap exists to avoid.
    """
    if not isinstance(raw, dict):
        raise ProfileError("profile must be a JSON object")

    p = dict(DEFAULT_PROFILE)
    p.update(raw)

    def positive(key):
        v = p.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise ProfileError(f"{key!r} must be a positive number, got {v!r}")
        return float(v)

    for key in ("arrival_rate_rps", "base_ttft_seconds", "base_itl_seconds",
                "kv_cache_tokens_capacity"):
        p[key] = positive(key)
    for key in ("max_concurrency", "max_in_flight"):
        v = p.get(key)
        if not isinstance(v, int) or v <= 0:
            raise ProfileError(f"{key!r} must be a positive integer, got {v!r}")
    if p["max_in_flight"] < p["max_concurrency"]:
        raise ProfileError("max_in_flight must be >= max_concurrency")

    # Clamp rather than reject: memory safety must not depend on the operator.
    p["max_in_flight"] = min(p["max_in_flight"], HARD_MAX_IN_FLIGHT)

    if not isinstance(p.get("model_name"), str) or not p["model_name"]:
        raise ProfileError("model_name must be a non-empty string")

    rate = p.get("prefix_cache_hit_rate")
    if not isinstance(rate, (int, float)) or not 0.0 <= rate <= 1.0:
        raise ProfileError(f"'prefix_cache_hit_rate' must be a number in 0.0-1.0, "
                           f"got {rate!r}")
    p["prefix_cache_hit_rate"] = float(rate)

    block = p.get("kv_block_tokens")
    if not isinstance(block, int) or block <= 0:
        raise ProfileError(f"'kv_block_tokens' must be a positive integer, got {block!r}")

    for key in ("prompt_tokens", "generation_tokens"):
        d = p.get(key)
        if not isinstance(d, dict) or "mean" not in d:
            raise ProfileError(f"{key!r} must be an object with a 'mean'")
        if d["mean"] <= 0:
            raise ProfileError(f"{key}.mean must be > 0")
        d.setdefault("stddev", 0)

    fr = p.get("finish_reasons")
    if not isinstance(fr, dict) or not fr:
        raise ProfileError("finish_reasons must be a non-empty object")
    total = sum(fr.values())
    if abs(total - 1.0) > 1e-6:
        raise ProfileError(f"finish_reasons weights must sum to 1.0, got {total}")

    return p


def capacity_rps(p):
    """Sustainable throughput. This is what separates 'steady' from 'saturated'.

    An arrival rate below this keeps the queue near zero; above it, the queue
    grows until max_in_flight and latency plateaus at a computable value.

    Uses the *congested* inter-token latency, not the base one. A server at
    capacity is by definition running a full batch, so base_itl_seconds alone
    overstates throughput by (1 + CONGESTION_AT_FULL_LOAD) — 1.5x with the
    shipped model. Tune profiles against this number, not the base figure.
    """
    itl = p["base_itl_seconds"] * (1.0 + CONGESTION_AT_FULL_LOAD)
    service = p["base_ttft_seconds"] + p["generation_tokens"]["mean"] * itl
    return p["max_concurrency"] / service


# --------------------------------------------------------------------------
# Metric primitives
# --------------------------------------------------------------------------
def fmt(value):
    """Prometheus-friendly number formatting."""
    if isinstance(value, int):
        return str(value)
    if value != value:                       # NaN
        return "NaN"
    if value == math.inf:
        return "+Inf"
    if value == -math.inf:
        return "-Inf"
    return repr(round(float(value), 6))


def esc(value):
    """Escape a label value per the exposition format."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def labels(pairs):
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in pairs) + "}"


class Histogram:
    """A cumulative Prometheus histogram.

    Supports weighted observations so a request's per-output-token latency can be
    recorded once with weight = token count, rather than looping per token — at
    saturation the aggregate token rate is in the thousands per second, and a
    loop would dominate a 2 vCPU node.
    """

    __slots__ = ("bounds", "buckets", "total", "count")

    def __init__(self, bounds):
        self.bounds = list(bounds)
        self.buckets = [0] * (len(bounds) + 1)   # final entry is +Inf
        self.total = 0.0
        self.count = 0

    def observe(self, value, weight=1):
        i = 0
        while i < len(self.bounds) and value > self.bounds[i]:
            i += 1
        self.buckets[i] += weight
        self.total += value * weight
        self.count += weight

    def render(self, name, base_labels, help_text):
        out = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        running = 0
        for i, bound in enumerate(self.bounds):
            running += self.buckets[i]
            out.append(f"{name}_bucket{labels(base_labels + [('le', fmt(bound))])} {running}")
        running += self.buckets[-1]
        out.append(f"{name}_bucket{labels(base_labels + [('le', '+Inf')])} {running}")
        out.append(f"{name}_sum{labels(base_labels)} {fmt(self.total)}")
        out.append(f"{name}_count{labels(base_labels)} {self.count}")
        return out


# --------------------------------------------------------------------------
# The simulation
# --------------------------------------------------------------------------
class Request:
    # queue_time and prefill are the two terms of ttft, kept separately rather
    # than recomputed: ttft is BUILT from them in _admit(), so
    # `ttft == queue_time + prefill` is an identity by construction, and
    # --selftest asserts it as one.
    #
    # decode and inference are the same move one level out. Upstream's phase
    # vocabulary is queue -> prefill -> decode, with inference = prefill + decode
    # ("time spent in RUNNING phase"), and every one of those terms was already
    # computed here — decode as `gen_tokens * itl` inside the finish_at
    # expression. Storing them makes `inference == prefill + decode` exact by
    # ASSIGNMENT rather than merely true in algebra, which is what lets
    # --selftest assert it with == instead of a tolerance.
    #
    # ⚠️ __slots__, so a new field MUST be declared here before it is assigned.
    # Without it the assignment in _admit() raises
    #   AttributeError: 'Request' object has no attribute 'decode' and no
    #   __dict__ for setting new attributes
    # which reads like a typo rather than a missing declaration.
    __slots__ = ("arrived", "prompt_tokens", "gen_tokens",
                 "ttft", "queue_time", "prefill", "decode", "inference",
                 "itl", "finish_at", "reason")


class Simulator:
    """Advances simulated requests in simulated time.

    All state changes happen in `advance_to()`. `render()` only reads — see the
    module docstring and `--selftest`.
    """

    def __init__(self, profile, start_time, binding_device_id=None, surface=V1):
        self.profile = profile
        self.now = start_time
        self.binding_device_id = binding_device_id
        self.surface = surface
        seed = profile.get("seed")
        self.rng = random.Random(seed) if seed is not None else random.Random()

        self.queue = deque()
        self.running = []

        self.next_arrival = start_time + self._interarrival()

        # Cumulative metrics. These reset on restart, which is correct — rate()
        # is reset-aware and persisting them would be worse than useless.
        self.prompt_tokens_total = 0
        self.generation_tokens_total = 0
        self.success_total = {r: 0 for r in profile["finish_reasons"]}
        self.rejected_total = 0
        self.prefix_cache_queries_total = 0
        self.prefix_cache_hits_total = 0
        # Fractional hits carried between requests, so the emitted ratio is the
        # number the profile asked for. See _admit().
        self._prefix_cache_carry = 0.0

        self.h_ttft = Histogram(TTFT_BUCKETS)
        self.h_tpot = Histogram(TPOT_BUCKETS)
        self.h_e2e = Histogram(E2E_BUCKETS)
        # ⚠️ E2E_BUCKETS, not a fourth constant. VERIFIED against
        # vllm/v1/metrics/loggers.py on 2026-07-31: upstream declares ONE
        # `request_latency_buckets` list and passes it to BOTH
        # vllm:e2e_request_latency_seconds and vllm:request_queue_time_seconds,
        # and E2E_BUCKETS is that list, all 21 values. So the correct boundaries
        # are already in this file, and check-vllm-buckets.py's existing
        # E2E_BUCKETS entry already watches them on behalf of both histograms.
        # Transcribing a second copy would add a list to drift, in the one repo
        # that refuses second copies.
        self.h_queue = Histogram(E2E_BUCKETS)
        # The three phase histograms, on the SAME list, for the same reason.
        # VERIFIED against vllm/v1/metrics/loggers.py on 2026-07-31: upstream
        # declares `request_latency_buckets` once at :889 and passes it to ALL
        # FIVE request-scoped histograms — e2e, queue, inference, prefill and
        # decode. E2E_BUCKETS is that list, so the comment above is now
        # load-bearing for five metrics rather than two, and check-vllm-buckets.py
        # still watches these boundaries on behalf of all of them.
        #
        # ⚠️ Prefill is UNRESOLVED at this rig's operating point and that is
        # upstream's layout, not a modelling fault: base_ttft_seconds is 0.08s and
        # the first boundary here is 0.3, so every prefill observation lands in
        # the first bucket and histogram_quantile interpolates from zero across
        # it — a measured ~3x overstatement on both shipped tenants. The
        # BREAKDOWN PANEL IS BUILT FROM MEANS, which carry no bucket dependence
        # (_sum and _count are exact) and are additive where quantiles are not.
        # Do not "fix" this with a finer low-end bucket list: the boundaries are
        # what make a query built here transfer unchanged to real vLLM, which has
        # exactly the same blind spot.
        self.h_prefill = Histogram(E2E_BUCKETS)
        self.h_decode = Histogram(E2E_BUCKETS)
        self.h_inference = Histogram(E2E_BUCKETS)

        self.profile_generation = 1
        self.profile_reload_errors = 0

        # Incremented on every histogram observation. --selftest asserts this is
        # unchanged across a render, which is how "scrapes are pure reads" is
        # verified without comparing two renders (which cannot distinguish
        # "render did nothing" from "nothing happened to be scheduled").
        self.observations = 0

    # -- profile ----------------------------------------------------------
    def apply_profile(self, profile):
        """Swap the profile without disturbing in-flight requests or counters."""
        for reason in profile["finish_reasons"]:
            self.success_total.setdefault(reason, 0)
        self.profile = profile
        self.profile_generation += 1

    # -- sampling ---------------------------------------------------------
    def _interarrival(self):
        return self.rng.expovariate(self.profile["arrival_rate_rps"])

    def _tokens(self, key):
        spec = self.profile[key]
        n = self.rng.gauss(spec["mean"], spec.get("stddev", 0))
        return max(1, int(n))

    def _jitter(self):
        return max(0.35, self.rng.gauss(1.0, 0.12))

    def _pick_reason(self):
        reasons = self.profile["finish_reasons"]
        r = self.rng.random()
        acc = 0.0
        for name, weight in reasons.items():
            acc += weight
            if r <= acc:
                return name
        return next(iter(reasons))

    # -- event loop -------------------------------------------------------
    def next_event_time(self):
        times = [self.next_arrival]
        if self.running:
            times.append(min(r.finish_at for r in self.running))
        return min(times)

    def advance_to(self, target):
        """Process every event scheduled at or before `target`."""
        guard = 0
        while True:
            nxt = self.next_event_time()
            if nxt > target:
                break
            self.now = nxt
            if self.running and min(r.finish_at for r in self.running) <= self.next_arrival:
                self._complete_one()
            else:
                self._arrive()
            self._admit()
            guard += 1
            if guard > 200_000:              # pathological profile; don't wedge
                break
        self.now = target

    def _arrive(self):
        self.next_arrival = self.now + self._interarrival()
        in_flight = len(self.queue) + len(self.running)
        if in_flight >= self.profile["max_in_flight"]:
            # Rejected, not silently dropped. On a saturated deployment the cap
            # is the normal operating state, so this counter is the only thing
            # explaining why the queue metric has stopped growing.
            self.rejected_total += 1
            return
        req = Request()
        req.arrived = self.now
        req.prompt_tokens = self._tokens("prompt_tokens")
        req.gen_tokens = self._tokens("generation_tokens")
        self.queue.append(req)

    def _admit(self):
        p = self.profile
        while self.queue and len(self.running) < p["max_concurrency"]:
            req = self.queue.popleft()
            load = (len(self.running) + 1) / p["max_concurrency"]

            # Prefill is the only work that happens before the first token, so it
            # is the only thing that may extend finish_at. Queue wait must NOT be
            # added here: the request has already spent that time sitting in
            # self.queue, and charging it a second time against a concurrency slot
            # makes slot occupancy grow with queue depth. That is positive feedback
            # with no restoring force — past a queue of roughly
            #   (max_concurrency / arrival_rate - service) / penalty
            # throughput drops below arrival and the queue can never drain, which
            # pinned every profile at max_in_flight regardless of its arrival rate.
            # ⚠️ FLAT, not token-proportional. This is the reason a prefix-cache
            # hit changes no latency in this model — see the file header before
            # changing it.
            prefill = p["base_ttft_seconds"] * self._jitter()

            # Reported TTFT still includes the wait — that is what vLLM measures —
            # but taken from the clock rather than modelled from queue depth. The
            # wait is kept as its own term rather than being recovered by
            # subtraction later: vllm:request_queue_time_seconds observes exactly
            # this number, so TTFT = queue_time + prefill is an identity here and
            # not an approximation of one.
            req.queue_time = self.now - req.arrived
            req.prefill = prefill
            req.ttft = req.queue_time + prefill
            req.itl = (p["base_itl_seconds"]
                       * (1.0 + CONGESTION_AT_FULL_LOAD * load) * self._jitter())

            # Prefix cache, counted in TOKENS and quantised to whole KV blocks.
            #
            # ⚠️ TOKENS, not requests, and that distinction is the whole point of
            # modelling it at all. A per-request counter gives a ratio that does
            # not respond to prompt length, so a panel built here would behave
            # differently against a real deployment — which defeats the purpose.
            #
            # Deterministic rather than per-block Bernoulli, so --selftest is
            # reproducible without depending on the profile's `seed`. A plain
            # floor() would bias the ratio DOWN by half a block on every request
            # — at 512 tokens that is ~1.5 percentage points, enough for a
            # profile asking for 0.15 to plot at 0.134 and read as a bug — so the
            # fraction is carried to the next request instead.
            block = p["kv_block_tokens"]
            self.prefix_cache_queries_total += req.prompt_tokens
            want = self._prefix_cache_carry + (req.prompt_tokens // block) \
                * p["prefix_cache_hit_rate"]
            whole = int(want)
            self._prefix_cache_carry = want - whole
            self.prefix_cache_hits_total += whole * block
            req.reason = self._pick_reason()

            # The phase decomposition, ASSIGNED rather than re-derived at
            # observation time. Decode is the product finish_at was already
            # formed from; naming it here and building finish_at out of the
            # named terms keeps ONE expression for the quantity.
            #
            # ⚠️ Assigned HERE and not beside req.prefill above, because req.itl
            # is not set until a few lines further down — `req.gen_tokens *
            # req.itl` is unavailable at the point prefill is stored.
            #
            # ⚠️ And finish_at is rewritten in terms of them deliberately.
            # Leaving it as `prefill + req.gen_tokens * req.itl` beside a
            # separate req.decode would be two spellings of one quantity, which
            # is how they drift apart the next time itl changes — invisibly,
            # since both evaluate identically until one is edited. It is also
            # what makes `inference == prefill + decode` hold BIT-EXACTLY: the
            # sum is evaluated once and stored, exactly as ttft is.
            #
            # ⚠️ `self.now + req.prefill + req.decode` and NOT
            # `self.now + req.inference`. They are algebraically the same and
            # associate differently — (now+prefill)+decode against
            # now+(prefill+decode) — which is not bit-identical in IEEE 754.
            # This spelling is the one the original expression used, so the
            # simulated clock is unchanged to the last bit, and it is the
            # association the e2e tolerance in --selftest was measured against.
            req.decode = req.gen_tokens * req.itl
            req.inference = req.prefill + req.decode
            req.finish_at = self.now + req.prefill + req.decode
            self.running.append(req)

    def _complete_one(self):
        req = min(self.running, key=lambda r: r.finish_at)
        self.running.remove(req)

        self.prompt_tokens_total += req.prompt_tokens
        self.generation_tokens_total += req.gen_tokens
        self.success_total[req.reason] = self.success_total.get(req.reason, 0) + 1

        self.h_ttft.observe(req.ttft)
        # Weighted: one observation carrying the token count, per the note on
        # Histogram.observe.
        self.h_tpot.observe(req.itl, weight=req.gen_tokens)
        self.h_e2e.observe(self.now - req.arrived)
        # Observed HERE and nowhere else, at the same point as TTFT, from the
        # term TTFT was built out of. Anything that re-derives it — from queue
        # depth, from a second clock reading — breaks the identity the selftest
        # asserts, and does so without failing anything else.
        self.h_queue.observe(req.queue_time)
        # The three phases, observed at the same point and from the same stored
        # terms — never re-derived here. queue + inference is e2e and
        # prefill + decode is inference, so a value computed at observation time
        # rather than read off the request is a second expression for a quantity
        # that already has one, and --selftest's identity block would stop
        # meaning anything.
        self.h_prefill.observe(req.prefill)
        self.h_decode.observe(req.decode)
        self.h_inference.observe(req.inference)
        self.observations += 7

    # -- derived gauges ---------------------------------------------------
    def kv_cache_usage(self):
        active = sum(r.prompt_tokens + r.gen_tokens for r in self.running)
        return min(1.0, active / self.profile["kv_cache_tokens_capacity"])

    # -- rendering (PURE READ) --------------------------------------------
    def render(self):
        p = self.profile
        model = [("model_name", p["model_name"])]
        sim = [("model_name", p["model_name"]), ("source", "simulated")]
        out = []

        def gauge(name, value, help_text, lbls=model):
            out.append(f"# HELP {name} {help_text}")
            out.append(f"# TYPE {name} gauge")
            out.append(f"{name}{labels(lbls)} {fmt(value)}")

        def counter(name, value, help_text, lbls=model):
            out.append(f"# HELP {name} {help_text}")
            out.append(f"# TYPE {name} counter")
            out.append(f"{name}{labels(lbls)} {fmt(value)}")

        emit = {"gauge": gauge, "counter": counter}

        # --- vLLM surface -------------------------------------------------
        # NOTE: no `source` label on any vllm:* series. Real vLLM does not emit
        # one, and an extra label breaks exact-match joins against a real
        # deployment — which would defeat the point of mirroring the names.
        gauge("vllm:num_requests_running", len(self.running),
              "Number of requests currently running on GPU.")
        gauge("vllm:num_requests_waiting", len(self.queue),
              "Number of requests waiting to be processed.")
        for name in surface_names("kv_cache_usage", self.surface):
            gauge(name, self.kv_cache_usage(),
                  "GPU KV-cache usage. 1 means 100 percent usage.")

        counter("vllm:prompt_tokens_total", self.prompt_tokens_total,
                "Number of prefill tokens processed.")
        counter("vllm:generation_tokens_total", self.generation_tokens_total,
                "Number of generation tokens processed.")

        # ⚠️ vllm:num_preemptions_total is deliberately NOT emitted, and putting
        # it back means modelling KV pressure first. Preemption is what a real
        # engine does when the KV cache runs out; nothing here creates that
        # condition, because _admit() gates on max_concurrency alone and
        # kv_cache_usage() therefore peaks near 0.43 even on the saturated
        # profile. It was previously exported as a hardcoded zero, which is not
        # an absence but a claim — "no preemptions are happening" — on a board
        # someone may be reading through the published image. It is listed as a
        # gap by scripts/check-vllm-buckets.py, which is where an unmodelled
        # upstream metric belongs.

        # Prefix cache. Which names appear depends on the surface: two counters
        # on V1, one gauge of the ratio on v0. Every reading below is computed
        # from state the worker already advanced — nothing here observes.
        prefix_cache = {
            "queries": self.prefix_cache_queries_total,
            "hits": self.prefix_cache_hits_total,
            # v0's gauge is the ratio the two V1 counters express. Cumulative
            # rather than windowed: nothing here models eviction, so the lifetime
            # ratio and a windowed one converge, and a windowed gauge would need
            # state only a scrape could advance.
            "ratio": (self.prefix_cache_hits_total / self.prefix_cache_queries_total
                      if self.prefix_cache_queries_total else 0.0),
        }
        for name in surface_names("prefix_cache", self.surface):
            kind, reading, help_text = PREFIX_CACHE_SERIES[name]
            emit[kind](name, prefix_cache[reading], help_text)

        out.append("# HELP vllm:request_success_total Count of successfully processed requests.")
        out.append("# TYPE vllm:request_success_total counter")
        for reason in sorted(self.success_total):
            lbls = model + [("finished_reason", reason)]
            out.append(f"vllm:request_success_total{labels(lbls)} {self.success_total[reason]}")

        out += self.h_ttft.render("vllm:time_to_first_token_seconds", model,
                                  "Histogram of time to first token in seconds.")
        for name in surface_names("inter_token", self.surface):
            out += self.h_tpot.render(name, model,
                                      "Histogram of inter-token latency in seconds.")
        out += self.h_e2e.render("vllm:e2e_request_latency_seconds", model,
                                 "Histogram of end to end request latency in seconds.")
        # No surface entry: VERIFIED at tag v0.6.6 that v0 exposed this under the
        # same name, so there is nothing to alias.
        out += self.h_queue.render("vllm:request_queue_time_seconds", model,
                                   "Histogram of time spent in WAITING phase for request.")

        # The phase breakdown. VERIFIED at tag v0.6.6 that all three exist there
        # under IDENTICAL spellings alongside request_queue_time_seconds, so
        # there is nothing for METRIC_SURFACES to map and nothing for
        # METRIC_RESHAPES to reshape — an unmapped metric renders on every
        # surface, which is the correct behaviour. The question is mandatory for
        # each new metric, not interesting; the answer is recorded so nobody has
        # to guess.
        #
        # ⚠️ THREE STRING LITERALS. DO NOT FACTOR THESE INTO A LOOP over
        # ("prefill", "decode", "inference") with an f-string name. It emits
        # identical metrics and blinds scripts/check-vllm-buckets.py, which
        # discovers what this repo emits by AST-walking for string literals
        # matching `vllm:[A-Za-z0-9_]+`. MEASURED against the real checker: the
        # only literal a loop leaves is the f-string's constant head,
        # `vllm:request_`, so the three names vanish from the matched set AND a
        # name upstream does not declare appears as DRIFT — exit 1, and the
        # weekly job goes red pointing at a checker that is working correctly.
        #
        # This is not the "no second copies" rule in reverse: that rule is about
        # one VALUE living in two places where they can drift. Three distinct
        # names are three distinct values. What repeats is a call shape, and a
        # repeated call shape is not a copy of anything.
        out += self.h_inference.render("vllm:request_inference_time_seconds", model,
                                       "Histogram of time spent in RUNNING phase for request.")
        out += self.h_prefill.render("vllm:request_prefill_time_seconds", model,
                                     "Histogram of time spent in PREFILL phase for request.")
        out += self.h_decode.render("vllm:request_decode_time_seconds", model,
                                    "Histogram of time spent in DECODE phase for request.")

        # --- this rig's own series (safe to label freely) ------------------
        gauge("llmsim_profile_generation", self.profile_generation,
              "Increments each time the load profile is successfully reloaded.", sim)
        counter("llmsim_profile_reload_errors_total", self.profile_reload_errors,
                "Malformed profile reloads rejected; the last good profile is kept.", sim)
        counter("llmsim_requests_rejected_total", self.rejected_total,
                "Arrivals refused because max_in_flight was reached.", sim)
        gauge("llmsim_capacity_rps", capacity_rps(p),
              "Sustainable throughput implied by the current profile.", sim)

        if self.binding_device_id:
            out.append("# HELP llmsim_gpu_binding_info Which simulated GPU this pod holds.")
            out.append("# TYPE llmsim_gpu_binding_info gauge")
            lbls = sim + [("device_id", self.binding_device_id)]
            out.append(f"llmsim_gpu_binding_info{labels(lbls)} 1")

        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
class State:
    """Shared state guarded by one lock."""

    def __init__(self, sim, profile_path):
        self.sim = sim
        self.profile_path = profile_path
        self.lock = threading.Lock()
        self.stop = threading.Event()


def read_profile_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return validate_profile(json.load(fh))


def worker(state, poll_seconds=10.0):
    """Advance the simulation in wall-clock time and poll the profile."""
    last_poll = 0.0
    while not state.stop.is_set():
        now = time.monotonic()
        with state.lock:
            state.sim.advance_to(now)
            due = state.sim.next_event_time()

        if state.profile_path and now - last_poll >= poll_seconds:
            last_poll = now
            try:
                new = read_profile_file(state.profile_path)
            except Exception as exc:                      # noqa: BLE001
                with state.lock:
                    state.sim.profile_reload_errors += 1
                print(f"llm-sim: keeping last good profile; reload failed: {exc}",
                      file=sys.stderr, flush=True)
            else:
                with state.lock:
                    if new != state.sim.profile:
                        state.sim.apply_profile(new)
                        print(f"llm-sim: profile generation "
                              f"{state.sim.profile_generation} applied "
                              f"({new['model_name']}, {new['arrival_rate_rps']} rps)",
                              flush=True)

        # Sleep until the next event, capped so profile polling stays responsive.
        state.stop.wait(max(0.01, min(due - time.monotonic(), 0.5)))


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                 # noqa: N802
            if self.path.split("?")[0] not in ("/metrics", "/"):
                self.send_error(404)
                return
            with state.lock:
                body = state.sim.render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass                                          # one line per scrape is noise

    return Handler


def detect_binding():
    """Return the device-plugin allocation id for this pod's GPU, if any.

    The fake GPU operator injects MOCK_NVIDIA_VISIBLE_DEVICES via the device
    plugin's Allocate() response — note the MOCK_ prefix; it is NOT the usual
    NVIDIA_VISIBLE_DEVICES. Absent means the pod asked for no GPU, or none was
    available; either way the simulator runs normally, just without the binding
    series.

    ⚠️ THIS IS NOT THE GPU'S DCGM UUID, however much it looks like one and
    however reasonable it seems to join on it. Chart 0.0.59 injects the device
    plugin's own per-allocation id — a bare random v4 like
    "cb7f4584-d2db-4fc2-9bc9-4e4f3179fb9a" — while the exporter labels that same
    GPU "GPU-fff9ceb6-313d-537f-9174-a01b04f1a9ff", a deterministic v5 taken from
    the topology ConfigMap. The two are minted by different code paths and never
    match, so `on (UUID)` joins silently return NOTHING. Hence the label here is
    `device_id`, not `UUID`: naming it UUID invited a join that cannot work.

    To attribute a simulator pod to a real DCGM series, join on the POD instead.
    The fake exporter labels each allocated GPU with its consumer, and Prometheus
    renames those to exported_* because target labels win:

        llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu)
          label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""},
            "namespace", "$1", "exported_namespace", "(.*)"),
            "pod", "$1", "exported_pod", "(.*)")

    That is what verify.sh L4b asserts and what the dashboard's attribution table
    queries.
    """
    raw = os.environ.get("MOCK_NVIDIA_VISIBLE_DEVICES", "").strip()
    if not raw or raw in ("void", "none"):
        return None
    return raw.split(",")[0].strip() or None


DEFAULT_PORT = 9401

# Committed inputs for --selftest. See tests/README.md.
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "tests", "fixtures")


def default_port():
    """Listen port from the environment, or DEFAULT_PORT.

    The variable is LLM_SIM_LISTEN_PORT and NOT the more obvious LLM_SIM_PORT,
    because that name is not ours to read inside Kubernetes. kubelet injects a
    Docker-link-compatible `<SVCNAME>_PORT` env var for every Service in the
    pod's namespace, so the Service `llm-sim` in namespace `llm-sim` sets

        LLM_SIM_PORT=tcp://<clusterIP>:9401

    into every simulator pod. Reading that name meant int() got a URL and every
    pod died at startup — a CrashLoopBackOff whose only visible symptom was an
    LLM dashboard with no data. The Deployments now also set
    enableServiceLinks: false; this name is the half that travels with the
    script, for anyone running it in a namespace we don't control.

    A bad value exits with a readable message rather than a bare ValueError
    traceback, because that traceback is what made the collision hard to read.
    """
    raw = os.environ.get("LLM_SIM_LISTEN_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"ERROR: LLM_SIM_LISTEN_PORT must be a port number, got {raw!r}")


# --------------------------------------------------------------------------
# Self-test — no cluster, no Prometheus, no wall-clock waiting
# --------------------------------------------------------------------------
def selftest():
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    print("llm-sim --selftest")

    # ---- fmt(): the three spellings Prometheus parses specially -------------
    # ⚠️ EVERY VALUE IN THE EXPOSITION GOES THROUGH fmt(), AND NOTHING TESTED IT.
    # The structural checks below are thorough about histogram shape — buckets
    # cumulative, +Inf equal to _count — and say nothing about how a number is
    # SPELLED. Prometheus accepts exactly `NaN`, `+Inf` and `-Inf`; get one wrong
    # and it rejects the sample while every structural assertion here stays green.
    # That is the failure class this repo exists to catch, and it was uncovered by
    # a CodeQL alert on the NaN test one line below being a false positive.
    #
    # `value != value` IS the NaN idiom — NaN is the only value not equal to
    # itself — and it stays. This pins the behaviour so it cannot be "tidied" into
    # something that silently formats NaN as the string "nan", which Prometheus
    # does not accept.
    fmt_cases = [
        (float("nan"), "NaN"),
        (math.inf, "+Inf"),
        (-math.inf, "-Inf"),
        (3, "3"),                    # int passes through without a decimal point
        (1.5, "1.5"),
        (0.1 + 0.2, "0.3"),          # rounded to 6dp, not 0.30000000000000004
    ]
    bad = [(v, fmt(v), want) for v, want in fmt_cases if fmt(v) != want]
    check(not bad, f"fmt() renders NaN, +Inf, -Inf, ints and rounded floats"
                   + (f" — got {bad}" if bad else ""))

    profile = validate_profile(dict(DEFAULT_PROFILE, seed=1234))
    sim = Simulator(profile, start_time=0.0, binding_device_id="d3adbeef-0000-4000-8000-000000000000")

    # Drive simulated time in fixed steps. The clock is a parameter, not a call
    # to time.monotonic(), which is what makes this deterministic and instant.
    prev_counts = None
    monotonic_ok = True
    for step in range(1, 61):
        sim.advance_to(step * 5.0)
        cur = (sim.h_ttft.count, sim.h_e2e.count,
               sim.prompt_tokens_total, sim.generation_tokens_total)
        if prev_counts and any(c < p for c, p in zip(cur, prev_counts)):
            monotonic_ok = False
        prev_counts = cur

    text = sim.render()
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]

    check(sim.h_ttft.count > 0, "requests completed and TTFT was observed")
    check(sim.h_queue.count == sim.h_ttft.count,
          "queue time is observed once per completion, alongside TTFT")
    check(sim.h_prefill.count == sim.h_decode.count == sim.h_inference.count
          == sim.h_ttft.count,
          "each phase is observed exactly once per completion, alongside TTFT")
    check(sim.prefix_cache_queries_total > 0,
          "prefix cache queries advance (every prompt token is looked up)")
    check(monotonic_ok, "counters never decrease across steps")

    # Buckets cumulative and non-decreasing, with a +Inf that equals _count.
    hist_ok, inf_ok, sum_ok = True, True, True
    for hist, name in ((sim.h_ttft, "ttft"), (sim.h_tpot, "tpot"),
                       (sim.h_e2e, "e2e"), (sim.h_queue, "queue"),
                       (sim.h_prefill, "prefill"), (sim.h_decode, "decode"),
                       (sim.h_inference, "inference")):
        running = 0
        prev = -1
        for i in range(len(hist.bounds)):
            running += hist.buckets[i]
            if running < prev:
                hist_ok = False
            prev = running
        running += hist.buckets[-1]
        if running != hist.count:
            inf_ok = False
        if hist.total < 0:
            sum_ok = False
    check(hist_ok, "histogram buckets are cumulative and non-decreasing")
    check(inf_ok, "+Inf bucket equals _count for every histogram")
    check(sum_ok, "_sum is consistent")

    # Exactly one TYPE per metric family, and a HELP for each.
    types = [ln.split()[2] for ln in text.splitlines() if ln.startswith("# TYPE ")]
    helps = [ln.split()[2] for ln in text.splitlines() if ln.startswith("# HELP ")]
    check(len(types) == len(set(types)), "one # TYPE per metric family")
    check(set(types) == set(helps), "every family has both # HELP and # TYPE")

    # The E2 requirement: rendering must not observe anything. Extended to the
    # non-histogram counters, and to the rendered text itself — the prefix-cache
    # series are computed at render time, which is exactly where a "let me just
    # advance this here" would be easiest to slip in and hardest to see.
    before = (sim.observations, sim.prefix_cache_queries_total,
              sim.prefix_cache_hits_total)
    first, second = sim.render(), sim.render()
    after = (sim.observations, sim.prefix_cache_queries_total,
             sim.prefix_cache_hits_total)
    check(after == before, "rendering performs no observation (pure read)")
    check(first == second, "two consecutive renders are identical")

    # No vllm:* series may carry a `source` label — it would break exact-match
    # joins against a real deployment.
    leaked = [ln for ln in lines if ln.startswith("vllm:") and 'source="' in ln]
    check(not leaked, "no source label on any vllm:* series")

    # --- metric surface -------------------------------------------------
    # The default must be the CURRENT engine's names. This repo's whole claim is
    # that what you build here transfers, and it stopped being true silently once
    # V1 renamed these two — nothing failed, the names just quietly stopped
    # matching a real deployment. That is exactly the class of regression a
    # selftest has to hold, so assert the emitted names rather than trusting the
    # mapping table to be read.
    v0_kv, v1_kv = METRIC_SURFACES["kv_cache_usage"]
    v0_itl, v1_itl = METRIC_SURFACES["inter_token"]

    def emits(text, name):
        return any(ln.split("{")[0].split(" ")[0] == name or
                   ln.split("{")[0].startswith(name + "_")
                   for ln in text.splitlines() if ln and not ln.startswith("#"))

    check(emits(text, v1_kv) and emits(text, v1_itl),
          "default surface emits the v1 names")
    check(not emits(text, v0_kv) and not emits(text, v0_itl),
          "default surface does NOT emit the superseded v0 names")

    both = Simulator(validate_profile(dict(DEFAULT_PROFILE, seed=1)), 0.0, surface=BOTH)
    both.advance_to(120.0)
    bt = both.render()
    check(all(emits(bt, n) for n in (v0_kv, v1_kv, v0_itl, v1_itl)),
          "surface 'both' emits the v1 and v0 names together")

    old = Simulator(validate_profile(dict(DEFAULT_PROFILE, seed=1)), 0.0, surface=V0)
    old.advance_to(120.0)
    ot = old.render()
    check(emits(ot, v0_kv) and not emits(ot, v1_kv),
          "surface 'v0' emits only the legacy names")

    # ⚠️ Prefix caching is the RESHAPE case, and it is asserted per surface for a
    # different reason than the two renames above. A rename can be repaired by
    # substituting a name; this cannot — v0's single gauge of a ratio became two
    # counters, so a panel bound to the old name needs rate(hits)/rate(queries).
    # The counts differ by surface, which is the thing METRIC_SURFACES could not
    # express, so assert the counts and not just the presence.
    (v0_pc,), v1_pc = METRIC_RESHAPES["prefix_cache"]
    check(all(emits(text, n) for n in v1_pc) and not emits(text, v0_pc),
          "surface 'v1' emits the two prefix-cache counters and no gauge")
    check(emits(ot, v0_pc) and not any(emits(ot, n) for n in v1_pc),
          "surface 'v0' emits the prefix-cache gauge and neither counter")
    check(all(emits(bt, n) for n in (v0_pc,) + v1_pc),
          "surface 'both' emits all three prefix-cache series")

    # ⚠️ The phase histograms must appear on EVERY surface, and no entry may have
    # been added to either surface table for them. VERIFIED against
    # vllm/engine/metrics.py at tag v0.6.6: all three exist there under identical
    # spellings, so there is no rename to map and no reshape to express — an
    # unmapped metric renders unconditionally, which is exactly right.
    #
    # Asserted rather than assumed, because "renders unconditionally" is a
    # property of render() that someone could break while adding a fourth
    # metric, and because a well-meaning surface-table entry would be invisible:
    # it would emit the same names on v1 and silently drop or alias them on v0.
    PHASE_METRICS = ("vllm:request_prefill_time_seconds",
                     "vllm:request_decode_time_seconds",
                     "vllm:request_inference_time_seconds")
    for label, rendered in (("v1", text), ("v0", ot), ("both", bt)):
        check(all(emits(rendered, n) for n in PHASE_METRICS),
              f"surface {label!r} emits all three request phase histograms")
    mapped = set()
    for side in METRIC_SURFACES.values():
        mapped |= set(side)
    for v0_side, v1_side in METRIC_RESHAPES.values():
        mapped |= set(v0_side) | set(v1_side)
    check(not (set(PHASE_METRICS) & mapped),
          "no METRIC_SURFACES or METRIC_RESHAPES entry was added for the phases")

    # Duplicated families under 'both' must still each carry exactly one TYPE,
    # or Prometheus rejects the whole scrape.
    btypes = [ln.split()[2] for ln in bt.splitlines() if ln.startswith("# TYPE ")]
    check(len(btypes) == len(set(btypes)), "surface 'both' keeps one # TYPE per family")

    # Every sample line must parse as `name{labels} value`.
    parse_ok = all(len(ln.rsplit(" ", 1)) == 2 and ln.rsplit(" ", 1)[1] not in ("",)
                   for ln in lines)
    check(parse_ok, "every sample line is well formed")

    cap = capacity_rps(profile)
    check(abs(cap - 2.74) < 0.05, f"capacity model gives {cap:.2f} rps for the shipped profile")
    check(profile["arrival_rate_rps"] < cap,
          f"shipped profile arrival {profile['arrival_rate_rps']}rps is below capacity {cap:.2f}rps")

    # --- queue time, asserted as an IDENTITY rather than a statistic ------
    # ttft is BUILT as queue_time + prefill in _admit(), and the histogram
    # observes that same first term — so this must hold exactly for every
    # request, floating point aside. That is strictly stronger than comparing a
    # p95 against a p95 with a tolerance, which can pass while the wiring is
    # wrong: observe the WHOLE ttft into the queue histogram and a quantile
    # comparison still looks approximately right at this rig's operating point,
    # where prefill is 0.08s against a queue wait of seconds.
    #
    # Subclassed rather than sampled from sim.running, because a request admitted
    # and completed inside one step never appears there.
    seen = []

    class Recording(Simulator):
        def _complete_one(self):
            req = min(self.running, key=lambda r: r.finish_at)
            seen.append((req.ttft, req.queue_time, req.prefill,
                         req.decode, req.inference, self.now - req.arrived))
            super()._complete_one()

    rec = Recording(validate_profile(dict(DEFAULT_PROFILE, seed=99)), 0.0)
    for step in range(1, 61):
        rec.advance_to(step * 5.0)
    check(bool(seen) and all(abs(t - (q + f)) < 1e-9 for t, q, f, _, _, _ in seen),
          f"ttft == queue_time + prefill for all {len(seen)} completed requests")

    # --- the phase breakdown, and the two identities are NOT equally exact ----
    # Upstream's own documentation strings give the decomposition:
    #     queue     = time spent in WAITING phase
    #     inference = time spent in RUNNING phase
    #     prefill   = time spent in PREFILL phase
    #     decode    = time spent in DECODE phase
    # so inference = prefill + decode and e2e = queue + inference. Both hold
    # algebraically here. Only ONE of them survives an ==, and asserting the
    # other with == fails on ~93% of perfectly correct requests.
    #
    # BIT-EXACT, because req.inference IS `req.prefill + req.decode` — one
    # expression, evaluated once in _admit() and stored. Exactness comes from the
    # ASSIGNMENT, not from the algebra, which is the same reason the ttft
    # assertion above holds and the reason W1.3 refuses to re-derive these at
    # observation time. A tolerance here would hide a real re-derivation.
    check(bool(seen) and all(i == f + d for _, _, f, d, i, _ in seen),
          f"inference == prefill + decode EXACTLY for all {len(seen)} requests")

    # NOT bit-exact, and the residual is float REASSOCIATION rather than a wiring
    # fault. e2e is read off the clock as (admit + prefill + decode) - arrived,
    # while this identity computes (admit - arrived) + prefill + decode. Those
    # are not bit-identical in IEEE 754.
    #
    # MEASURED over 1538 completed requests across three seeds: the clock itself
    # is exact (self.now == req.finish_at, 1538/1538), a bit-exact comparison
    # here passes on only 107/1538 (7%), and the worst absolute error is 5.24e-14.
    # abs_tol=1e-9 is five orders of margin over that — and a genuine wiring
    # fault (observing ttft into the queue histogram, re-deriving decode from a
    # second clock reading) moves this by MILLISECONDS, twelve orders clear.
    #
    # ⚠️ This is not the tolerance-on-a-statistic this repo objects to elsewhere.
    # That objection is to comparing a p95 against a p95 with a tolerance, which
    # can pass while the wiring is wrong. This is an identity whose only error
    # term is bounded at ~1e-14. Do not "fix" the simulator to chase the residual.
    check(bool(seen) and all(math.isclose(e, q + i, rel_tol=0, abs_tol=1e-9)
                             for _, q, _, _, i, e in seen),
          f"e2e == queue_time + inference within 1e-9 for all {len(seen)} requests")

    # --- prefix cache: the rate is emitted, and it changes NO latency -----
    def drive(rate, seed=4242):
        s = Simulator(validate_profile(dict(DEFAULT_PROFILE, seed=seed,
                                            prefix_cache_hit_rate=rate)), 0.0)
        for step in range(1, 61):
            s.advance_to(step * 5.0)
        return s

    base = drive(0.0)
    check(base.prefix_cache_hits_total == 0 and base.prefix_cache_queries_total > 0,
          "a 0.0 hit rate misses every lookup rather than skipping it")
    for want in (0.35, 0.15):                     # the two shipped tenant rates
        run = drive(want)
        got = run.prefix_cache_hits_total / run.prefix_cache_queries_total
        # Tolerance, not equality, and the shortfall is real rather than noise:
        # a partial trailing block is not cacheable, so at a 512-token mean about
        # 1.6% of queried tokens can never be hits.
        check(abs(got - want) < 0.02,
              f"hit rate {want} emits a ratio of {got:.3f}")
        # ⚠️ THE POINT OF W1.4. Same seed, same everything — the RNG stream is
        # untouched by the prefix-cache accounting, so the TTFT histogram must be
        # bit-identical. If this ever fails, someone has made prefill
        # token-proportional without re-deriving the capacity arithmetic, the
        # profiles, the 2s threshold and the promtool expectations. Fix the
        # arithmetic, do not relax the check.
        check((run.h_ttft.count, run.h_ttft.total, run.h_ttft.buckets)
              == (base.h_ttft.count, base.h_ttft.total, base.h_ttft.buckets),
              f"TTFT is unchanged at hit rate {want} (no latency effect, by construction)")

    # --- the committed 0.0 fixture ---------------------------------------
    # An absent series and a zero one are different things to a panel, so the
    # case a profile is most likely to be left in is pinned in the repo rather
    # than constructed here.
    fixture = os.path.join(FIXTURES, "profile-no-prefix-cache.json")
    try:
        zero = Simulator(read_profile_file(fixture), 0.0)
    except Exception as exc:                               # noqa: BLE001
        check(False, f"tests/fixtures/profile-no-prefix-cache.json is readable ({exc})")
    else:
        for step in range(1, 61):
            zero.advance_to(step * 5.0)
        zt = {ln.split("{")[0]: ln.rsplit(" ", 1)[1]
              for ln in zero.render().splitlines()
              if ln and not ln.startswith("#")}
        check(zt.get("vllm:prefix_cache_queries_total", "0") != "0"
              and zt.get("vllm:prefix_cache_hits_total") == "0",
              "the 0.0 fixture still EMITS both counters, hits flat at zero")

    # Malformed profiles are rejected, not fatal.
    bad = 0
    for broken in ({"finish_reasons": {"stop": 0.5}}, {"max_concurrency": 0},
                   {"model_name": ""}, [],
                   {"prefix_cache_hit_rate": 1.5},         # a rate, not a percentage
                   {"prefix_cache_hit_rate": "0.35"},      # JSON string, not a number
                   {"kv_block_tokens": 0}):
        try:
            validate_profile(broken)
        except ProfileError:
            bad += 1
        except Exception:                                  # noqa: BLE001
            pass
    check(bad == 7, "malformed profiles raise ProfileError rather than crashing")

    print()
    if failures:
        print(f"SELFTEST FAILED ({len(failures)} check(s))")
        return 1
    print("SELFTEST PASSED")
    return 0


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Simulated vLLM metrics endpoint.")
    ap.add_argument("--profile", default=os.environ.get("LLM_SIM_PROFILE", ""),
                    help="path to profile.json (polled for changes)")
    ap.add_argument("--port", type=int, default=default_port())
    ap.add_argument("--poll-seconds", type=float, default=10.0)
    ap.add_argument("--vllm-surface", choices=(V1, V0, BOTH),
                    default=os.environ.get("LLM_SIM_VLLM_SURFACE", V1),
                    help="which vLLM metric names to emit (default: v1, what a "
                         "current engine exposes). 'both' emits the v0 aliases "
                         "alongside, for testing a dashboard across an upgrade.")
    ap.add_argument("--selftest", action="store_true",
                    help="validate exposition output and exit (no cluster needed)")
    ap.add_argument("--print", dest="print_once", action="store_true",
                    help="warm up briefly, print one scrape, and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.profile:
        try:
            profile = read_profile_file(args.profile)
        except Exception as exc:                           # noqa: BLE001
            print(f"llm-sim: profile unreadable ({exc}); starting on defaults",
                  file=sys.stderr, flush=True)
            profile = validate_profile({})
    else:
        profile = validate_profile({})

    binding = detect_binding()
    sim = Simulator(profile, start_time=time.monotonic(), binding_device_id=binding,
                    surface=args.vllm_surface)

    print(f"llm-sim: model_name={profile['model_name']} "
          f"arrival={profile['arrival_rate_rps']}rps "
          f"capacity={capacity_rps(profile):.2f}rps "
          f"max_in_flight={profile['max_in_flight']}", flush=True)
    print(f"llm-sim: vllm metric surface={args.vllm_surface} "
          f"(kv cache: {', '.join(surface_names('kv_cache_usage', args.vllm_surface))})",
          flush=True)
    if binding:
        print(f"llm-sim: bound to simulated GPU {binding}", flush=True)
    else:
        print("llm-sim: no simulated GPU allocated "
              "(MOCK_NVIDIA_VISIBLE_DEVICES unset) — running unbound, "
              "llmsim_gpu_binding_info will not be emitted", flush=True)

    if args.print_once:
        sim.advance_to(time.monotonic() + 30.0)
        sys.stdout.write(sim.render())
        return 0

    state = State(sim, args.profile)
    threading.Thread(target=worker, args=(state, args.poll_seconds), daemon=True).start()

    server = ThreadingHTTPServer(("", args.port), make_handler(state))
    print(f"llm-sim: serving /metrics on :{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
