#!/usr/bin/env bash
# drive-load.sh <ramp|spikes> [deployment] [namespace]
# Walks a workload's simulated-gpu-utilization over time to produce a moving curve
# (instead of the static bands the default workloads emit). Targets the opt-in
# `gpu-driven` Deployment by default.
#
#   ./scripts/drive-load.sh ramp                 # 0→95→0 staircase on gpu-driven
#   ./scripts/drive-load.sh spikes gpu-driven    # baseline/spike train
#   STEP_SECONDS=30 ./scripts/drive-load.sh ramp gpu-steady default
#
# Mechanism: each step patches the pod-template annotation, which rolls a new pod that the
# fake operator admits with the new util. (If your fake-operator version re-reads the
# annotation on the *live* pod, you could instead `kubectl annotate pod ... --overwrite`
# for a restart-free curve — but the template patch works regardless of that behaviour.)
#
# NOTE: operates on your CURRENT kubecontext — it prints which one. Point kubectl at the
# right cluster first (install.sh sets contexts gpu-sim-eks / gpu-sim-gke).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

MODE="${1:-ramp}"
DEPLOY="${2:-gpu-driven}"
NS="${3:-default}"
STEP_SECONDS="${STEP_SECONDS:-60}"

ctx="$(kubectl config current-context 2>/dev/null || echo '<none>')"
echo "drive-load: mode=$MODE target=deploy/$DEPLOY ns=$NS context=$ctx step=${STEP_SECONDS}s"

kubectl -n "$NS" get deploy "$DEPLOY" >/dev/null 2>&1 || {
  echo "ERROR: deployment '$DEPLOY' not found in ns '$NS'." >&2
  echo "       Apply the opt-in extras first:  kubectl apply -f manifests/workloads/extras/" >&2
  exit 1
}

set_util() {
  local range="$1"
  echo "  -> util ${range}%  (holding ${STEP_SECONDS}s)"
  kubectl -n "$NS" patch deploy "$DEPLOY" --type merge \
    -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"run.ai/simulated-gpu-utilization\":\"${range}\"}}}}}" >/dev/null
  kubectl -n "$NS" rollout status "deploy/${DEPLOY}" --timeout=2m >/dev/null || true
  sleep "$STEP_SECONDS"
}

case "$MODE" in
  ramp)
    for r in "0-10" "20-30" "40-50" "60-70" "80-95" "60-70" "40-50" "20-30" "0-10"; do set_util "$r"; done
    ;;
  spikes)
    for _ in 1 2 3 4 5; do set_util "5-15"; set_util "90-99"; done
    ;;
  *)
    echo "usage: drive-load.sh <ramp|spikes> [deployment] [namespace]" >&2; exit 1 ;;
esac

echo "drive-load: done — annotation left at its last value; re-apply the manifest to reset."
