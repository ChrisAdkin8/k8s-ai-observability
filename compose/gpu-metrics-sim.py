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

MEMORY IS ALL-OR-NOTHING, deliberately. The real fake exporter reports an allocated GPU
as FB_USED=<all> / FB_FREE=0 and an unallocated one as the reverse, with nothing in
between, because it tracks ALLOCATION rather than load. Reproducing that here keeps the
memory panel and GPUHighMemoryUsage behaving exactly as they do on the cluster — see
docs/observability.md#reading-the-gpu-board.

Standard library only, same constraint as scripts/llm-sim.py: no pip, no image build.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def render():
    t = time.time()
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
    args = ap.parse_args()

    if args.once:
        print(render(), end="")
        return

    random.seed(0)
    print(f"gpu-metrics-sim: {GPU_COUNT}x {GPU_PRODUCT} on :{args.port}/metrics "
          f"({sum(1 for g in GPUS if g['allocated'])} allocated)", flush=True)
    ThreadingHTTPServer(("", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
