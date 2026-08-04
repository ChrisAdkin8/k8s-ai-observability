#!/usr/bin/env bash
# prometheus.sh <eks|gke> [--no-open] — one-command access to the Prometheus console.
#
# The sibling of grafana.sh, for the layer underneath it. Grafana answers "what do the
# boards show"; this answers "is the data there at all" — the expression browser, the
# scrape targets, and the rule/alert state, which is where you go when a panel is empty
# and you need to know whether nothing is produced, nothing is scraped, or nothing
# matches your query.
#
# Prometheus is ClusterIP (helm/kube-prometheus-stack/values.yaml sets no Service type,
# and the chart's default is ClusterIP), so reaching the console means a port-forward.
# There is no auth in front of it — which is safe for exactly the same reason the
# anonymous Grafana Viewer is: the only way in is a port-forward, which already
# requires cluster RBAC.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

usage() { echo "usage: prometheus.sh <eks|gke|local> [--no-open]" >&2; }

TARGET="${1:-}"
[[ -n "$TARGET" ]] || { usage; exit 1; }
shift
case "$TARGET" in
  eks) require_tools terraform aws ;;
  gke) require_tools terraform gcloud gke-gcloud-auth-plugin ;;
  local) require_tools kind ;;
  *)   echo "target must be eks|gke|local" >&2; usage; exit 1 ;;
esac

OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    *) echo "ERROR: unknown option '$arg'" >&2; usage; exit 1 ;;
  esac
done

CTX="$(ensure_context "$TARGET")"
KUBECTL=(kubectl --context "$CTX")

PF_PID=""
cleanup() { [[ -n "$PF_PID" ]] && kill "$PF_PID" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

echo "==> context $CTX"
PROM_SVC="$(resolve_kps_or_die svc prometheus)" || exit 1
"${KUBECTL[@]}" -n "$MONITORING_NS" port-forward "svc/${PROM_SVC}" \
  "${PROMETHEUS_PORT}:9090" >/dev/null 2>&1 &
PF_PID=$!

# Poll rather than sleep, and treat a dead forward as the actionable failure — the same
# reasoning as grafana.sh. /-/ready is the right probe: it goes 503 while Prometheus is
# still replaying its WAL, so a "ready" console is one whose queries will actually answer.
HAVE_CURL=0
if command -v curl >/dev/null 2>&1; then HAVE_CURL=1; fi
ready=0
for _ in $(seq 1 30); do
  if ! kill -0 "$PF_PID" 2>/dev/null; then
    echo "ERROR: port-forward exited — is localhost:${PROMETHEUS_PORT} already in use?" >&2
    echo "       Retry on another port: PROMETHEUS_PORT=9091 ./scripts/prometheus.sh $TARGET" >&2
    exit 1
  fi
  if [[ "$HAVE_CURL" -eq 1 ]]; then
    curl -fsS "http://localhost:${PROMETHEUS_PORT}/-/ready" >/dev/null 2>&1 && { ready=1; break; }
  else
    sleep 3; ready=1; break   # no curl: fall back to a fixed settle, the forward is up
  fi
  sleep 1
done
[[ "$ready" -eq 1 ]] || { echo "ERROR: Prometheus was not ready on localhost:${PROMETHEUS_PORT} within 30s" >&2; exit 1; }

open_url() {
  if command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}

echo "==> console  $(prometheus_url)"
echo "    query    $(prometheus_query_url)      expression browser"
echo "    targets  $(prometheus_targets_url)    is every exporter being scraped?"
echo "    alerts   $(prometheus_alerts_url)     what is firing right now"
echo "    rules    $(prometheus_rules_url)      recording + alert rule health"

# Non-fatal, and worth printing before the browser steals focus: a target that is down is
# the single most common reason a dashboard panel is empty, and it is invisible from
# Grafana. Counted here rather than listed, so the output stays one line.
if [[ "$HAVE_CURL" -eq 1 ]] && command -v python3 >/dev/null 2>&1; then
  down="$(curl -fsS "http://localhost:${PROMETHEUS_PORT}/api/v1/targets" 2>/dev/null \
    | python3 -c 'import sys,json
try:
    t=json.load(sys.stdin)["data"]["activeTargets"]
except Exception:
    sys.exit(0)
bad=[x for x in t if x.get("health")!="up"]
print(f"{len(bad)}/{len(t)}")
for x in bad[:5]:
    print("      "+x.get("scrapePool","?")+" -> "+(x.get("lastError") or "down")[:90])
' 2>/dev/null || true)"
  if [[ -n "$down" && "${down%%/*}" != "0" ]]; then
    echo "    ⚠ ${down%%$'\n'*} scrape targets are NOT up:"
    echo "${down#*$'\n'}"
  fi
fi

if [[ "$OPEN_BROWSER" -eq 1 ]]; then open_url "$(prometheus_query_url)"; fi

echo "    Ctrl-C to stop the port-forward"
wait "$PF_PID"
