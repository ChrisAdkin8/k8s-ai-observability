#!/usr/bin/env bash
# kind-up.sh — Phase 1 for the `local` target: create the kind cluster.
#
# The no-cloud equivalent of `terraform apply`. Same contract as the cloud roots: it
# produces a cluster whose nodes already carry the GPU-sim label, and it does NOT touch
# Helm or the manifests — that is Phase 2 (scripts/install.sh), kept separate so the
# Kubernetes tooling is never pointed at a cluster that does not exist yet.
#
#   ./scripts/kind-up.sh          # create (or reuse) the cluster
#   ./scripts/install.sh local    # Phase 2
#   ./scripts/verify.sh local
#
# Idempotent: an existing cluster of the same name is reused, not rebuilt. To start
# clean, delete it first — ./scripts/teardown.sh local --destroy
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

require_tools kind

# The label and the version pin live in kind/gpu-sim.yaml, and this is the step that
# bakes them into a real cluster — so assert them BEFORE creating anything. A drifted
# config caught here costs nothing; caught after `helm install` it costs a teardown.
assert_gpu_contract
assert_kind_contract

# --- container-runtime preflight ----------------------------------------------
# kind needs a running Docker-API or Podman runtime, and this stack needs that runtime
# to have been given enough of the host. Checked here, with the fix in the message,
# because the alternative failure is Prometheus sitting Pending forever — which reads
# as "the repo is broken" rather than "the VM has 2 GiB".
runtime_preflight() {
  [[ "${KIND_SKIP_PREFLIGHT:-0}" == "1" ]] && { echo "==> preflight skipped (KIND_SKIP_PREFLIGHT=1)"; return 0; }

  local info="" runtime="" mem_bytes="" cpus=""
  if command -v docker >/dev/null 2>&1 && info="$(docker info --format '{{.MemTotal}} {{.NCPU}}' 2>/dev/null)" && [[ -n "${info// /}" ]]; then
    runtime="docker"
  elif command -v podman >/dev/null 2>&1 && info="$(podman info --format '{{.Host.MemTotal}} {{.Host.CPUs}}' 2>/dev/null)" && [[ -n "${info// /}" ]]; then
    runtime="podman"
    # kind talks to podman only when told to; without this it looks for a Docker socket
    # that is not there and reports it as "Cannot connect to the Docker daemon".
    export KIND_EXPERIMENTAL_PROVIDER=podman
  else
    cat >&2 <<'EOF'
ERROR: no running container runtime found (kind needs Docker or Podman).

  On macOS, colima is the least-friction option — and it sidesteps the Docker
  Desktop subscription question entirely:

    brew install colima kind
    colima start --cpu 4 --memory 8 --disk 40

  Podman works too (kind is told to use it automatically):

    brew install podman kind && podman machine init --cpus 4 --memory 8192 && podman machine start
EOF
    exit 1
  fi

  mem_bytes="${info%% *}"; cpus="${info##* }"
  # Non-numeric means the runtime answered in a shape we don't understand. Warn and
  # continue rather than block: an unparsed field is not evidence of a small machine.
  if ! [[ "$mem_bytes" =~ ^[0-9]+$ && "$cpus" =~ ^[0-9]+$ ]]; then
    echo "WARNING: could not read memory/CPU from $runtime — skipping the sizing check." >&2
    return 0
  fi

  local mem_gib=$(( mem_bytes / 1024 / 1024 / 1024 ))
  echo "==> runtime: $runtime, ${mem_gib} GiB / ${cpus} CPU"

  if (( mem_gib < KIND_MIN_MEMORY_GIB || cpus < KIND_MIN_CPUS )); then
    cat >&2 <<EOF
ERROR: the container runtime is too small for this stack.
       have ${mem_gib} GiB / ${cpus} CPU, need at least ${KIND_MIN_MEMORY_GIB} GiB / ${KIND_MIN_CPUS} CPU
       (recommended ${KIND_WANT_MEMORY_GIB} GiB / ${KIND_WANT_CPUS} CPU).

       Prometheus alone requests 1Gi and limits 2Gi; below the floor above it never
       leaves Pending and every acceptance check fails for a reason nothing states.

       colima:         colima stop && colima start --cpu ${KIND_WANT_CPUS} --memory ${KIND_WANT_MEMORY_GIB} --disk 40
       Docker Desktop: Settings -> Resources -> raise Memory to ${KIND_WANT_MEMORY_GIB} GB, CPUs to ${KIND_WANT_CPUS}
       podman:         podman machine stop && podman machine set --cpus ${KIND_WANT_CPUS} --memory $((KIND_WANT_MEMORY_GIB * 1024)) && podman machine start

       Override deliberately (expect Pending pods): KIND_SKIP_PREFLIGHT=1 $0
EOF
    exit 1
  fi

  if (( mem_gib < KIND_WANT_MEMORY_GIB || cpus < KIND_WANT_CPUS )); then
    echo "WARNING: ${mem_gib} GiB / ${cpus} CPU is under the recommended ${KIND_WANT_MEMORY_GIB} GiB / ${KIND_WANT_CPUS} CPU." >&2
    echo "         The install should work but will be slow, and headroom for the opt-in extras is thin." >&2
  fi
}
runtime_preflight

# --- create ---------------------------------------------------------------------
# sigpipe-ok: one short line per kind cluster — bytes, not kilobytes, so it has
# finished writing long before grep can close the pipe.
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "==> kind cluster '$CLUSTER_NAME' already exists — reusing it"
else
  echo "==> creating kind cluster '$CLUSTER_NAME' from $KIND_CONFIG"
  # --name is NOT passed: the name lives in the config file (asserted against
  # CLUSTER_NAME above), and passing both is a way for the two to disagree.
  if ! kind create cluster --config "$KIND_CONFIG" --wait 120s; then
    cat >&2 <<EOF

ERROR: kind failed to create the cluster.

  Two things in $KIND_CONFIG are worth suspecting first:
    * the node image tag — kind ties node images to kind RELEASES. Check that
      kindest/node:v${K8S_VERSION}.x is one your kind build ships (kind --version).
    * the kubeadmConfigPatches block — it is optional polish that only makes the
      control-plane scrape targets green. Deleting it is a safe way to isolate.
EOF
    exit 1
  fi
fi

CTX="$(ensure_context local)"

# The invariant, checked against the cluster that now exists rather than the file that
# was supposed to produce it. kind silently accepts a labels block it cannot apply, so
# asserting the YAML is not the same as asserting the node.
if [[ -z "$(kubectl --context "$CTX" get nodes -l "${NODE_POOL_LABEL_KEY}=${NODE_POOL_NAME}" -o name 2>/dev/null)" ]]; then
  echo "ERROR: no node carries ${NODE_POOL_LABEL_KEY}=${NODE_POOL_NAME}." >&2
  echo "       The fake operator would watch a pool no node is labelled for:" >&2
  echo "       a green install with ZERO GPUs. Check the labels block in $KIND_CONFIG," >&2
  echo "       then recreate: ./scripts/teardown.sh local --destroy && $0" >&2
  exit 1
fi

cat <<EOF

Cluster ready (context: $CTX). No cloud account, no credentials, no spend.

Next:
  ./scripts/install.sh local     # Phase 2 — observability + fake GPU + LLM stacks
  ./scripts/verify.sh local

Or in one step:  task local:up
EOF
