#!/usr/bin/env bash
# drive-llm-load.sh <idle|steady|burst|saturation|ramp> [--with-gpu]
#
# Walks the OPT-IN `llm-driven` simulator through a load curve, so you can watch
# the queue build, TTFT climb and an alert trip on the LLM dashboard.
#
#   kubectl apply -f manifests/llm/extras/     # once
#   ./scripts/drive-llm-load.sh ramp           # 0.4 -> 6.0 -> 0.4 rps staircase
#   ./scripts/drive-llm-load.sh saturation     # hold above capacity
#   ./scripts/drive-llm-load.sh ramp --with-gpu   # move the GPU metrics too
#
# HOW IT WORKS
#   It rewrites the `llm-profile-driven` ConfigMap. The simulator polls that file
#   every 10s and applies it WITHOUT restarting, so counters and histograms stay
#   continuous — no artificial rate() discontinuity every time you change load.
#   Kubernetes takes up to ~60s to propagate a ConfigMap into a running pod, so
#   allow a minute before a change shows on the dashboard. Watch
#   `llmsim_profile_generation` (top-right panel) tick up to confirm it landed.
#
# WHY NOT llm-steady / llm-saturated
#   Those two hold the fixed states verify.sh asserts against. Driving them would
#   make the acceptance checks flap. This script refuses to touch them.
#
# --with-gpu ALSO drives scripts/drive-load.sh against the gpu-driven workload.
#   GPU and LLM load are independent in this rig (nothing couples them), so this
#   just moves both at once for a more convincing demo. It does not make one
#   cause the other — see docs/llm-simulation.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

MODE="${1:-ramp}"
WITH_GPU=0
[[ "${2:-}" == "--with-gpu" ]] && WITH_GPU=1

STEP_SECONDS="${STEP_SECONDS:-90}"
DEPLOY="llm-driven"
CM="llm-profile-driven"

ctx="$(kubectl config current-context 2>/dev/null || echo '<none>')"
echo "drive-llm-load: mode=$MODE target=$DEPLOY ns=$LLM_NS context=$ctx step=${STEP_SECONDS}s"

kubectl -n "$LLM_NS" get deploy "$DEPLOY" >/dev/null 2>&1 || {
  echo "ERROR: deployment '$DEPLOY' not found in ns '$LLM_NS'." >&2
  echo "       Apply the opt-in extras first:  kubectl apply -f manifests/llm/extras/" >&2
  exit 1
}

# Capacity for the shipped profile shape:
#   16 concurrency / (0.08 + 256 x 0.015 x 1.5) = 2.74 rps  (congested itl)
# Anything below that keeps the queue near zero; above it the queue fills to
# max_in_flight - max_concurrency = 160 and TTFT plateaus around 160/2.74 ~= 58s
# (reported TTFT is the real measured queue wait, so Little's Law sets it).
#
# prefix_cache_hit_rate is REWRITTEN UNCHANGED at every step, and must match
# manifests/llm/extras/llm-driven.yaml: this function replaces the whole
# ConfigMap, so a field omitted here silently reverts to the simulator's 0.0
# default the first time load is driven, and the panel flatlines mid-demo. It
# changes no latency by construction, so it is not what any mode is varying.
set_rate() {
  local rps="$1" note="$2"
  echo "  -> ${rps} rps  (${note}; holding ${STEP_SECONDS}s)"
  kubectl -n "$LLM_NS" create configmap "$CM" --dry-run=client -o yaml \
    --from-literal=profile.json="$(cat <<JSON
{
  "model_name": "${LLM_DRIVEN_MODEL}",
  "arrival_rate_rps": ${rps},
  "max_concurrency": 16,
  "max_in_flight": 176,
  "prompt_tokens":     {"mean": 512, "stddev": 128},
  "generation_tokens": {"mean": 256, "stddev": 64},
  "base_ttft_seconds": 0.08,
  "base_itl_seconds": 0.015,
  "kv_cache_tokens_capacity": 32768,
  "prefix_cache_hit_rate": 0.25,
  "finish_reasons": {"stop": 0.90, "length": 0.09, "abort": 0.01},
  "seed": null
}
JSON
)" | kubectl apply -f - >/dev/null
  sleep "$STEP_SECONDS"
}

# Optionally move the GPU side on a matching curve. Backgrounded so the two
# domains advance together rather than one after the other.
gpu_pid=""
if [[ "$WITH_GPU" -eq 1 ]]; then
  if kubectl -n default get deploy gpu-driven >/dev/null 2>&1; then
    echo "  (also driving GPU utilisation via scripts/drive-load.sh ramp)"
    ./scripts/drive-load.sh ramp >/dev/null 2>&1 &
    gpu_pid=$!
    trap '[[ -n "$gpu_pid" ]] && kill "$gpu_pid" 2>/dev/null || true' EXIT
  else
    echo "  NOTE: --with-gpu asked for, but deployment 'gpu-driven' is not present." >&2
    echo "        Apply it first:  kubectl apply -f manifests/workloads/extras/" >&2
  fi
fi

case "$MODE" in
  idle)       set_rate 0.4 "0.15x capacity" ;;
  steady)     set_rate 1.8 "0.66x capacity — healthy" ;;
  burst)      for _ in 1 2 3; do
                set_rate 1.2 "quiet"
                set_rate 6.0 "2.19x capacity — queue builds"
              done ;;
  saturation) set_rate 6.0 "2.19x capacity — hold until LLMHighTTFT fires" ;;
  ramp)       for r in 0.4 1.2 2.4 3.6 5.0 6.0 5.0 3.6 2.4 1.2 0.4; do
                set_rate "$r" "$(awk -v r="$r" 'BEGIN{printf "%.2fx capacity", r/2.74}')"
              done ;;
  *)
    echo "usage: drive-llm-load.sh <idle|steady|burst|saturation|ramp> [--with-gpu]" >&2
    exit 1 ;;
esac

echo "drive-llm-load: done — profile left at its last value."
echo "  Reset with:  kubectl apply -f manifests/llm/extras/"
