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
# Re-check them if you change LLM_VLLM_VERSION in scripts/config.sh.
# --------------------------------------------------------------------------
# The upper bounds must cover a fully-queued tenant, or histogram_quantile lands
# in the +Inf bucket and Prometheus reports the highest finite bound instead —
# a p95 panel pinned flat at the top bucket, which reads as a plateau rather than
# as "off the scale". Worst case here is a request arriving behind a full queue:
#   (max_in_flight - max_concurrency) / capacity = 160 / 2.74 ~= 58s of wait,
# plus generation, so TTFT needs ~2x that in headroom and e2e a little more.
TTFT_BUCKETS = [0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1,
                0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0,
                15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0]
# ⚠️ p95 TPOT reads HIGH at this rig's operating point, and that is the buckets,
# not the simulator. A full batch models ITL at base_itl x 1.5 = 0.0225s, which
# lands inside the wide (0.025, 0.05] bucket, so histogram_quantile interpolates
# across a 25ms gap and reports ~43ms: roughly double the modelled value. Real
# vLLM has this same layout, so a real deployment reads high in the same way and
# the boundaries must stay exactly as transcribed. Do not derive an ITL SLO from
# the p95 panel at these latencies; it has no resolution between 25ms and 50ms.
TPOT_BUCKETS = [0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3,
                0.4, 0.5, 0.75, 1.0, 2.5]
E2E_BUCKETS = [1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0,
               90.0, 120.0, 180.0]

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


def surface_names(key, surface):
    """Every name `key` is emitted under, for the chosen surface."""
    v0, v1 = METRIC_SURFACES[key]
    if surface == V0:
        return [v0]
    if surface == V1:
        return [v1]
    return [v1, v0]          # v1 first: it is what a current deployment emits


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
    __slots__ = ("arrived", "prompt_tokens", "gen_tokens",
                 "ttft", "itl", "finish_at", "reason")


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
        self.preemptions_total = 0

        self.h_ttft = Histogram(TTFT_BUCKETS)
        self.h_tpot = Histogram(TPOT_BUCKETS)
        self.h_e2e = Histogram(E2E_BUCKETS)

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
            prefill = p["base_ttft_seconds"] * self._jitter()

            # Reported TTFT still includes the wait — that is what vLLM measures —
            # but taken from the clock rather than modelled from queue depth.
            req.ttft = (self.now - req.arrived) + prefill
            req.itl = (p["base_itl_seconds"]
                       * (1.0 + CONGESTION_AT_FULL_LOAD * load) * self._jitter())
            req.reason = self._pick_reason()
            req.finish_at = self.now + prefill + req.gen_tokens * req.itl
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
        self.observations += 3

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
        counter("vllm:num_preemptions_total", self.preemptions_total,
                "Cumulative number of preemptions from the engine.")

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
    check(monotonic_ok, "counters never decrease across steps")

    # Buckets cumulative and non-decreasing, with a +Inf that equals _count.
    hist_ok, inf_ok, sum_ok = True, True, True
    for hist, name in ((sim.h_ttft, "ttft"), (sim.h_tpot, "tpot"), (sim.h_e2e, "e2e")):
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

    # The E2 requirement: rendering must not observe anything.
    before = sim.observations
    sim.render()
    sim.render()
    check(sim.observations == before, "rendering performs no observation (pure read)")

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

    # Malformed profiles are rejected, not fatal.
    bad = 0
    for broken in ({"finish_reasons": {"stop": 0.5}}, {"max_concurrency": 0},
                   {"model_name": ""}, []):
        try:
            validate_profile(broken)
        except ProfileError:
            bad += 1
        except Exception:                                  # noqa: BLE001
            pass
    check(bad == 4, "malformed profiles raise ProfileError rather than crashing")

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
