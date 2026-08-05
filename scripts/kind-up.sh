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

  # ⚠️ ROUNDED, NOT FLOORED, AND THAT IS A FIX RATHER THAN A PREFERENCE.
  #
  # A guest NEVER reports the whole allocation — firmware and the kernel take a few
  # hundred MB before Linux counts MemTotal. Measured on colima/aarch64 2026-08-05:
  # `colima start --memory 8` yields MemTotal=8307175424, which is 7.738 GiB.
  #
  # Flooring that read 7, so EVERY THRESHOLD IN THIS FILE WAS SILENTLY ONE GiB HIGHER
  # THAN IT SAID. The consequences were not cosmetic:
  #   * the recommendation was unreachable at its own value — a VM given exactly the
  #     recommended 8 GiB warned "under the recommended 8 GiB", on every single run
  #   * the FLOOR refused correctly-sized machines: asked for 5, reports ~4.83, reads
  #     4, and the install is refused before anything is created. Same at the LITE
  #     floor of 3, which reports 2.83 and reads 2
  # The repo had documented the workaround in three places (allocate 4 to get 3.81)
  # rather than fixing the arithmetic that made it necessary.
  #
  # Rounding costs a little strictness at the boundary: a VM with a true 4.5 GiB now
  # reads 5 and passes a floor of 5. That is the right trade. This floor is a
  # heuristic guard against a runtime that cannot fit Prometheus at all — colima's
  # 2 GiB default still reads 2 and is still refused — and half a GiB of slack in it
  # is worth far less than refusing the configuration the README tells you to build.
  local mem_gib=$(( (mem_bytes + 512 * 1024 * 1024) / 1024 / 1024 / 1024 ))
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

# --- point containerd at whichever pull-through caches are running ---------------
#
# Written AFTER the cluster exists and on the reuse path too, because hosts.toml is read
# per pull rather than at containerd start-up: a cache started later is picked up by the
# next `task local:up` without recreating anything. (The config_path setting that makes
# this directory meaningful is the opposite — see kind/gpu-sim.yaml.)
#
# Nothing here is fatal. A cache that is not running is simply not mirrored, which is
# the documented default, and the only cost of getting it wrong is the pull time the
# cache exists to save.
configure_registry_mirrors() {
  local entry host slug upstream name state node mirrored=0 nodes cfg
  nodes="$(kind get nodes --name "$CLUSTER_NAME" 2>/dev/null || true)"
  [[ -n "$nodes" ]] || return 0

  for entry in "${REGISTRY_CACHES[@]}"; do
    IFS='|' read -r host slug upstream <<< "$entry"
    name="${REGISTRY_CACHE_PREFIX}-${slug}"
    state="$("$CONTAINER_CLI" inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"

    # ⚠️ A STOPPED CACHE MUST HAVE ITS MIRROR FILE REMOVED, not merely skipped.
    # hosts.toml lives in the node and outlives the container it points at, so
    # `registry-cache.sh down` on a reused cluster would otherwise leave containerd
    # dialling a host that no longer exists on every pull. That is survivable (the
    # `server` fallback below still fetches the image) but it buys a failed connection
    # per layer for nothing, and it is invisible unless you read the file.
    if [[ "$state" != "running" ]]; then
      for node in $nodes; do
        "$CONTAINER_CLI" exec "$node" rm -f "${CONTAINERD_CERTS_DIR}/${host}/hosts.toml" 2>/dev/null || true
      done
      continue
    fi

    for node in $nodes; do
      # `server` is set explicitly so the fallback is unambiguous: containerd tries the
      # [host.*] mirrors in order and the server last, so a cache that is up but broken
      # costs a retry rather than the image.
      "$CONTAINER_CLI" exec -i "$node" sh -c \
        "mkdir -p '${CONTAINERD_CERTS_DIR}/${host}' && cat > '${CONTAINERD_CERTS_DIR}/${host}/hosts.toml'" <<EOF
server = "${upstream}"

[host."http://${name}:5000"]
  capabilities = ["pull", "resolve"]
EOF
    done
    mirrored=$(( mirrored + 1 ))
  done

  if (( mirrored == 0 )); then
    echo "==> no pull-through cache running — images come from the internet on every new cluster"
    echo "    (./scripts/registry-cache.sh up makes the NEXT cold build read from disk)"
    return 0
  fi

  # The mirrors are inert without the config_path patch, and silently so. A cluster
  # created before that block existed is the realistic case, and "nothing got faster"
  # is a terrible thing to have to diagnose.
  cfg="$("$CONTAINER_CLI" exec "${nodes%%$'\n'*}" cat /etc/containerd/config.toml 2>/dev/null || true)"
  if [[ "$cfg" != *"config_path"* ]]; then
    echo "WARNING: ${mirrored} cache(s) running, but this cluster's containerd has no" >&2
    echo "         registry config_path, so it will IGNORE them and pull as usual." >&2
    echo "         The cluster predates the containerdConfigPatches block in $KIND_CONFIG." >&2
    echo "         Recreate it to benefit:  ./scripts/teardown.sh local --destroy && task local:up" >&2
    return 0
  fi
  echo "==> ${mirrored} pull-through cache(s) mirrored into containerd"
}
configure_registry_mirrors

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
