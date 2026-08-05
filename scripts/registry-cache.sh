#!/usr/bin/env bash
# registry-cache.sh [up|down|status] — pull-through image caches for the `local` target.
#
# WHAT THIS IS FOR. `kind delete cluster` destroys the node's containerd image store, so
# every cold `task local:up` re-pulls the whole stack from the internet. Measured on a
# fresh cluster, 2026-08-05: ~915 MB across 14 images, and 215s of a 555s run spent
# pulling. `grafana:13.1.0` alone is 352 MB and took 52.1s. Two pulls (node-exporter from
# quay.io, kube-state-metrics from registry.k8s.io) hit i/o timeouts, went
# ImagePullBackOff, and cost ~60s of dead time before succeeding on retry.
#
# These caches sit on the same Docker network as the kind node and survive
# `kind delete`. The second cold build reads from a container on the loopback bridge
# instead of from five registries on the internet.
#
#   ./scripts/registry-cache.sh up       # start them (once; they persist)
#   ./scripts/registry-cache.sh status   # what is running, and how much it has cached
#   ./scripts/registry-cache.sh down     # stop and remove (add --purge to drop the data)
#
# ⚠️ OPT-IN, AND `task local:up` MUST WORK WITHOUT IT. kind-up.sh only points containerd
# at a cache that is actually running; with these stopped, nothing is written and the
# node pulls exactly as it did before. That is deliberate: the one-shot path is the
# thing this repo advertises, and making it depend on five background containers would
# be a poor trade for a speed-up.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

require_tools "$CONTAINER_CLI"

ACTION="${1:-status}"
PURGE=0
shift || true
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    # Every unrecognised argument is rejected, like install.sh and teardown.sh. A
    # typo'd flag must fail loudly rather than silently do the non-flag thing.
    *) echo "ERROR: unknown argument '$arg'" >&2
       echo "usage: registry-cache.sh [up|down|status] [--purge]" >&2
       exit 1 ;;
  esac
done

# ⚠️ ONE CACHE PER UPSTREAM, BECAUSE registry:2 PROXIES EXACTLY ONE REMOTE.
# REGISTRY_PROXY_REMOTEURL is a single value, so a mirror for five registries is five
# containers. They are tiny when idle; the cost is the disk their volumes hold, which
# `down --purge` reclaims.
#
# The list itself is REGISTRY_CACHES in config.sh, because kind-up.sh needs the same
# one to write containerd's mirror config. See the comment there.
CACHES=("${REGISTRY_CACHES[@]}")

# The Docker network kind attaches its nodes to. Creating it here when absent is safe:
# kind reuses a network of this name rather than replacing it, so a cache started
# before the first cluster is on the right bridge when the node arrives.
CACHE_NETWORK="${CACHE_NETWORK:-kind}"
CACHE_IMAGE="${CACHE_IMAGE:-registry:2}"
# Published so you can curl a cache and see what it holds. Overridable because 5001-5005
# is a plausible clash on a developer machine, and a port conflict here should be a
# setting rather than a reason to give up on the cache.
CACHE_PORT_BASE="${CACHE_PORT_BASE:-5000}"

cache_name() { echo "${REGISTRY_CACHE_PREFIX}-$1"; }

ensure_network() {
  "$CONTAINER_CLI" network inspect "$CACHE_NETWORK" >/dev/null 2>&1 && return 0
  echo "==> creating container network '$CACHE_NETWORK' (kind will reuse it)"
  "$CONTAINER_CLI" network create "$CACHE_NETWORK" >/dev/null
}

up() {
  ensure_network
  local i=0 entry host slug upstream name port state
  for entry in "${CACHES[@]}"; do
    i=$(( i + 1 ))
    IFS='|' read -r host slug upstream <<< "$entry"
    name="$(cache_name "$slug")"
    port=$(( CACHE_PORT_BASE + i ))

    # Read the state into a variable rather than piping docker into grep: this file
    # runs under pipefail (rule 17), and there is nothing to gain by piping one word.
    state="$("$CONTAINER_CLI" inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"
    state="${state//[$'\n\r ']/}"
    case "$state" in
      running) echo "  ok      $name (already running, proxying $host)" ; continue ;;
      # A stopped container still holds its cached blobs. Start it rather than
      # recreating it, or `down` without --purge would silently mean --purge.
      exited|created) "$CONTAINER_CLI" start "$name" >/dev/null
                      echo "  started $name (proxying $host)" ; continue ;;
    esac

    "$CONTAINER_CLI" run -d --name "$name" \
      --restart unless-stopped \
      --network "$CACHE_NETWORK" \
      -p "127.0.0.1:${port}:5000" \
      -v "${name}-data:/var/lib/registry" \
      -e "REGISTRY_PROXY_REMOTEURL=${upstream}" \
      "$CACHE_IMAGE" >/dev/null
    echo "  created $name -> $upstream (localhost:${port})"
  done

  cat <<EOF

Caches are up. They are NOT used by an existing cluster: containerd reads the mirror
config at pull time, but kind-up.sh is what writes it, and it does that at cluster
creation. So:

  ./scripts/teardown.sh local --destroy && task local:up

The first build through a cold cache is no faster than before, because the cache is
fetching the same bytes once. Every build after it reads from here.
EOF
}

down() {
  local entry host slug upstream name
  for entry in "${CACHES[@]}"; do
    IFS='|' read -r host slug upstream <<< "$entry"
    : "$host" "$upstream"           # unused in this branch; read for the shared parse
    name="$(cache_name "$slug")"
    if [[ -n "$("$CONTAINER_CLI" ps -aq -f "name=^${name}$" 2>/dev/null || true)" ]]; then
      "$CONTAINER_CLI" rm -f "$name" >/dev/null
      echo "  removed $name"
    fi
    if [[ "$PURGE" == "1" ]]; then
      "$CONTAINER_CLI" volume rm "${name}-data" >/dev/null 2>&1 \
        && echo "  purged  ${name}-data" \
        || true
    fi
  done
  [[ "$PURGE" == "1" ]] \
    || echo "
  Cached blobs kept in the ${#CACHES[@]} kind-cache-*-data volumes. Drop them with:
    ./scripts/registry-cache.sh down --purge"
}

status() {
  local entry host slug upstream name state size any=0
  printf '  %-22s %-18s %-10s %s\n' CACHE UPSTREAM STATE CACHED
  for entry in "${CACHES[@]}"; do
    IFS='|' read -r host slug upstream <<< "$entry"
    : "$upstream"
    name="$(cache_name "$slug")"
    # `inspect` on a missing container prints an empty line to stdout before failing,
    # so normalise rather than relying on the `||` fallback alone.
    state="$("$CONTAINER_CLI" inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"
    state="${state//[$'\n\r ']/}"
    [[ -n "$state" ]] || state="-"
    [[ "$state" == "running" ]] && any=1
    # du inside the container: the volume's real size, which is the only number here
    # worth printing. `-` when the container is not running to read it.
    size="-"
    if [[ "$state" == "running" ]]; then
      size="$("$CONTAINER_CLI" exec "$name" du -sh /var/lib/registry 2>/dev/null | cut -f1 || echo '?')"
    fi
    printf '  %-22s %-18s %-10s %s\n' "$name" "$host" "$state" "$size"
  done
  if [[ "$any" == "0" ]]; then
    echo
    echo "  No cache is running. task local:up works exactly as it always has;"
    echo "  it just pulls every image from the internet on each new cluster."
    echo "  Start them with: ./scripts/registry-cache.sh up"
  fi
}

case "$ACTION" in
  up)     up ;;
  down)   down ;;
  status) status ;;
  *) echo "usage: registry-cache.sh [up|down|status] [--purge]" >&2; exit 1 ;;
esac
