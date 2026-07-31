#!/usr/bin/env python3
"""
gpu-metrics-sim.py — the fake DCGM exporter's metric surface, without Kubernetes.

⚠️ COMPOSE ONLY. On Kubernetes this job is done by run-ai/fake-gpu-operator, whose
   device plugin advertises nvidia.com/gpu and whose status-exporter emits these
   series. That is a device plugin and a DaemonSet; neither has any meaning outside a
   cluster, so the compose stack needs a stand-in to render the GPU board.

   This file is NOT part of the Kubernetes path and is never installed by
   scripts/install.sh. It exists so the GPU dashboard and the GPU recording rules can
   be developed and demonstrated on a laptop in seconds.

WHAT IT COPIES, AND WHY THAT IS ENOUGH
--------------------------------------
The fake exporter on chart 0.0.59 emits exactly THREE series:

    DCGM_FI_DEV_GPU_UTIL      percent, 0-100
    DCGM_FI_DEV_FB_USED       MiB
    DCGM_FI_DEV_FB_FREE       MiB

and nothing else. Temperature and power are not among them — they are synthesised by
the recording rules in manifests/alerts/gpu-prometheusrule.yaml, which the compose
Prometheus loads unchanged. So emitting these three is sufficient for all four panels
of the GPU board plus every GPU alert: everything else is derived by the same PromQL
that runs on the cluster.

Label set matches the exporter, because the dashboard legends and the recording rules
both join on it: Hostname, gpu, UUID, modelName, device.

⚠️ THAT PARITY IS NOW A CONTRACT, NOT A CLAIM IN THIS HEADER.
   tests/contracts/dcgm-surface.json lists the series and the label keys, and both
   producers are asserted against it: `--selftest` here demands EXACT equality, and
   verify.sh check 3b demands the cluster's exporter satisfy it as a SUBSET. The
   asymmetry is deliberate and the contract file's own header explains it — series
   arriving through Prometheus carry target labels the exporter never emitted.

   Before that file existed this was the only statement of the parity anywhere, and
   nothing compared the two. A chart bump that renamed a series failed the kind path
   loudly in CI and let this path drift silently, which is backwards: `docker compose
   up -d` is the first command in the README.

MEMORY IS ALL-OR-NOTHING, deliberately. The real fake exporter reports an allocated GPU
as FB_USED=<all> / FB_FREE=0 and an unallocated one as the reverse, with nothing in
between, because it tracks ALLOCATION rather than load. Reproducing that here keeps the
memory panel and GPUHighMemoryUsage behaving exactly as they do on the cluster — see
docs/observability.md#reading-the-gpu-board.

Standard library only, same constraint as scripts/llm-sim.py: no pip, no image build.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The parity contract both producers are asserted against. See its own header for
# why the compose side is checked for exact equality and the cluster side for a
# subset; tests/fixtures/dcgm-surface-wrong.json is the negative case.
HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, os.pardir, "tests", "contracts", "dcgm-surface.json")
WRONG_CONTRACT = os.path.join(HERE, os.pardir, "tests", "fixtures",
                              "dcgm-surface-wrong.json")

# Mirrors helm/fake-gpu-operator/values.yaml: 8 GPUs per node, Tesla-T4, 15360 MiB.
GPU_COUNT = int(os.environ.get("GPU_SIM_COUNT", "8"))
GPU_PRODUCT = os.environ.get("GPU_SIM_PRODUCT", "Tesla-T4")
GPU_MEMORY_MIB = int(os.environ.get("GPU_SIM_MEMORY_MIB", "15360"))
HOSTNAME = os.environ.get("GPU_SIM_HOSTNAME", "gpu-sim-compose-0")

# The four allocated GPUs and their utilisation bands, matching the annotations on the
# sample workloads in manifests/workloads/gpu-workloads.yaml. GPUs beyond this list are
# unallocated and sit flat at zero — which is what produces "four moving traces and a
# crowd of flat ones" on the board, exactly as on the cluster.
#
# gpu-busy's 85-99 band straddles the GPUHighUtilization threshold of 80, so the alert
# fires here for the same reason it fires there.
BANDS = [
    ("gpu-busy", 85, 99),
    ("gpu-steady", 40, 60),
    ("llm-steady", 25, 40),
    ("gpu-idle", 0, 5),
]

# Deterministic UUIDs, so a restart does not orphan every series in Prometheus and
# leave the panels with a step discontinuity nothing explains. v5 from a fixed
# namespace mirrors how the real exporter derives them from its topology ConfigMap.
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def gpus():
    out = []
    for i in range(GPU_COUNT):
        band = BANDS[i] if i < len(BANDS) else None
        out.append(
            {
                "index": i,
                "uuid": "GPU-" + str(uuid.uuid5(_NS, f"{HOSTNAME}-{i}")),
                "workload": band[0] if band else "",
                "lo": band[1] if band else 0,
                "hi": band[2] if band else 0,
                "allocated": band is not None,
            }
        )
    return out


GPUS = gpus()


def utilisation(g, t):
    """A slow sine inside the GPU's band, so panels move rather than sit flat.

    The real exporter picks a fresh uniform sample in the band on each scrape. A sine
    is used instead because a demo is watched, not scraped once: uncorrelated samples
    look like noise on a 15m window, where a drifting curve reads as a workload.
    Both stay inside the band, so every threshold behaves identically.
    """
    if not g["allocated"]:
        return 0.0
    mid = (g["lo"] + g["hi"]) / 2.0
    amp = (g["hi"] - g["lo"]) / 2.0
    phase = g["index"] * 1.7  # de-phase the GPUs so they don't move in lockstep
    return max(0.0, min(100.0, mid + amp * math.sin(t / 45.0 + phase) * 0.9))


def render(t=None):
    """One scrape's worth of exposition.

    The clock is a PARAMETER defaulting to wall time, for the same reason
    scripts/llm-sim.py's is: it makes --selftest deterministic and instant, so the
    surface assertions below can render twice and demand byte-identical output
    rather than comparing two sine samples with a tolerance.

    Nothing here accumulates. Every series is a gauge computed from `t`, so a
    scrape cannot move a number — the compose analogue of llm-sim's rule that
    advance_to() mutates and render() only reads. --selftest asserts it.
    """
    t = time.time() if t is None else t
    lines = [
        "# HELP DCGM_FI_DEV_GPU_UTIL GPU utilization (in %).",
        "# TYPE DCGM_FI_DEV_GPU_UTIL gauge",
    ]
    used, free = [], []
    for g in GPUS:
        labels = (
            f'gpu="{g["index"]}",UUID="{g["uuid"]}",device="nvidia{g["index"]}",'
            f'modelName="{GPU_PRODUCT}",Hostname="{HOSTNAME}"'
        )
        lines.append(f"DCGM_FI_DEV_GPU_UTIL{{{labels}}} {utilisation(g, t):.0f}")
        # All-or-nothing, exactly as the fake exporter reports it.
        u = GPU_MEMORY_MIB if g["allocated"] else 0
        used.append(f"DCGM_FI_DEV_FB_USED{{{labels}}} {u}")
        free.append(f"DCGM_FI_DEV_FB_FREE{{{labels}}} {GPU_MEMORY_MIB - u}")

    lines += ["# HELP DCGM_FI_DEV_FB_USED Framebuffer memory used (in MiB).",
              "# TYPE DCGM_FI_DEV_FB_USED gauge", *used,
              "# HELP DCGM_FI_DEV_FB_FREE Framebuffer memory free (in MiB).",
              "# TYPE DCGM_FI_DEV_FB_FREE gauge", *free]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# The parity contract — parse what we emit, compare it to the committed file
# --------------------------------------------------------------------------
# Deliberately parses the RENDERED TEXT rather than reading the constants above.
# Asserting GPU_PRODUCT against a contract entry would only prove this file is
# self-consistent; the thing that has to be right is the exposition a Prometheus
# actually scrapes, including the label spelling inside the braces.
_SAMPLE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?[ \t]+"
                     r"(-?[0-9.eE+-]+|NaN|[+-]Inf)$")
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')
_TYPES = {"counter", "gauge", "histogram", "summary", "untyped"}


def parse_exposition(text):
    """(help_for, type_for, labelsets_for) keyed by metric name.

    labelsets_for maps a name to the SET of label-key sets seen on it, so a
    series whose samples disagree about their labels is visible rather than
    averaged away — that inconsistency is legal exposition and a real bug.
    """
    help_for, type_for, labelsets_for = {}, {}, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# HELP "):
            name, _, doc = line[len("# HELP "):].partition(" ")
            help_for[name] = doc
            continue
        if line.startswith("# TYPE "):
            name, _, kind = line[len("# TYPE "):].partition(" ")
            type_for[name] = kind.strip()
            continue
        if line.startswith("#"):
            continue
        m = _SAMPLE.match(line)
        if not m:
            raise ValueError(f"unparseable exposition line: {raw!r}")
        name, labelstr, _value = m.groups()
        keys = frozenset(k for k, _ in _LABEL.findall(labelstr or ""))
        labelsets_for.setdefault(name, set()).add(keys)
    return help_for, type_for, labelsets_for


def read_contract(path):
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return set(doc["series"]), set(doc["labels"])


def check_surface(text, want_series, want_labels):
    """Problems with `text` as an EXACT match for the contract. Empty list = clean.

    Returns strings rather than raising, so the selftest can report every
    divergence in one run instead of one per invocation.
    """
    problems = []
    try:
        help_for, type_for, labelsets_for = parse_exposition(text)
    except ValueError as exc:
        return [str(exc)]

    got_series = set(labelsets_for)
    for extra in sorted(got_series - want_series):
        problems.append(f"emits {extra}, which the contract does not list")
    for missing in sorted(want_series - got_series):
        problems.append(f"contract lists {missing}, which is not emitted")

    for name in sorted(got_series & want_series):
        for keys in sorted(labelsets_for[name], key=sorted):
            if set(keys) != want_labels:
                problems.append(
                    f"{name} label keys {sorted(keys)} != contract {sorted(want_labels)}")
        if name not in help_for:
            problems.append(f"{name} has no # HELP line")
        kind = type_for.get(name)
        if kind is None:
            problems.append(f"{name} has no # TYPE line")
        elif kind not in _TYPES:
            problems.append(f"{name} declares an invalid # TYPE: {kind!r}")
    return problems


def selftest():
    """Assert this producer against tests/contracts/dcgm-surface.json.

    NO NETWORK, NO DOCKER, NO CLUSTER — it runs in the `fast` CI job in
    milliseconds, so the parity claim is checked on every push rather than only
    when someone happens to bring the compose stack up.
    """
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    print("gpu-metrics-sim --selftest")

    try:
        want_series, want_labels = read_contract(CONTRACT)
    except (OSError, ValueError, KeyError) as exc:
        print(f"  FAIL  contract unreadable: {exc}")
        return 1
    print(f"  ({len(want_series)} series, {len(want_labels)} label keys from "
          f"{os.path.relpath(CONTRACT, os.path.join(HERE, os.pardir))})")

    text = render(0.0)

    problems = check_surface(text, want_series, want_labels)
    for p in problems:
        print(f"        {p}")
    check(not problems, "exposition matches the DCGM surface contract exactly")

    # Every series is a gauge. Not contract-driven — the contract lists names and
    # label keys — but FB_USED arriving as a counter would make rate() meaningful
    # on a quantity that goes down, so it is worth pinning here.
    _, type_for, _ = parse_exposition(text)
    check(all(type_for.get(s) == "gauge" for s in want_series),
          "every contract series is declared a gauge")

    # A scrape must not move a number. Same clock in, same bytes out — the
    # compose analogue of llm-sim's render()-only-reads rule.
    check(render(0.0) == text, "rendering twice at the same clock is identical")
    check(render(1234.0) != text, "rendering at a different clock does move (not frozen)")

    # THE NEGATIVE CASE. Without this, a checker that returns [] unconditionally
    # passes everything above and nothing ever notices. The fixture is wrong in
    # three independent ways; see its header.
    try:
        bad_series, bad_labels = read_contract(WRONG_CONTRACT)
    except (OSError, ValueError, KeyError) as exc:
        print(f"  FAIL  wrong-contract fixture unreadable: {exc}")
        return 1
    bad = check_surface(text, bad_series, bad_labels)
    check(bool(bad), "a deliberately wrong contract is REJECTED")
    check(any("DCGM_FI_DEV_FB_FREE" in p for p in bad),
          "  ...the unlisted series we emit is named")
    check(any("DCGM_FI_DEV_SM_CLOCK" in p for p in bad),
          "  ...the listed series we do not emit is named")
    check(any("model_name" in p for p in bad),
          "  ...the renamed label key is named")

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'OK'}")
    return 1 if failures else 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_error(404)
            return
        body = render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # scrape logs at 15s would drown anything useful
        pass


def main():
    ap = argparse.ArgumentParser(description="Simulated DCGM exporter (compose only).")
    ap.add_argument("--port", type=int, default=int(os.environ.get("GPU_SIM_PORT", "9400")))
    ap.add_argument("--print", dest="once", action="store_true",
                    help="print one scrape and exit")
    ap.add_argument("--selftest", action="store_true",
                    help="assert the exposition against tests/contracts/dcgm-surface.json "
                         "(no network, no docker)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.once:
        print(render(), end="")
        return

    random.seed(0)
    print(f"gpu-metrics-sim: {GPU_COUNT}x {GPU_PRODUCT} on :{args.port}/metrics "
          f"({sum(1 for g in GPUS if g['allocated'])} allocated)", flush=True)
    ThreadingHTTPServer(("", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
