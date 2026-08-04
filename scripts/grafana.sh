#!/usr/bin/env bash
# grafana.sh <eks|gke> [--no-open] — one-command access to BOTH dashboards (GPU + LLM).
#
# Grafana is deliberately ClusterIP (see helm/kube-prometheus-stack/values.yaml), so
# reaching it means a port-forward. This wraps the three fiddly steps around that:
# selecting+guarding the right context, waiting until Grafana actually answers, and
# landing on each board by UID instead of hunting for them in the UI.
#
# One port-forward serves both boards — they live in the same Grafana, so the extra
# board costs another URL, not another forward.
#
# Anonymous Viewer auth is enabled in values.yaml, so no login is needed to LOOK at
# the dashboards. The admin password is printed for when you need to edit/configure.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

usage() { echo "usage: grafana.sh <eks|gke|local> [--no-open]" >&2; }

TARGET="${1:-}"
[[ -n "$TARGET" ]] || { usage; exit 1; }
shift
case "$TARGET" in
  eks) require_tools terraform aws ;;
  # gke-gcloud-auth-plugin is what kubectl exec's to mint a token for a GKE context,
  # and it is a separate install from gcloud. Without it the port-forward below dies
  # with a credential-plugin error that reads like a cluster problem; name it here.
  gke) require_tools terraform gcloud gke-gcloud-auth-plugin ;;
  # No credential plugin on the local path — kind writes a client cert straight into
  # the kubeconfig, so kubectl needs nothing beyond kind itself to resolve the context.
  local) require_tools kind ;;
  *)   echo "target must be eks|gke|local" >&2; usage; exit 1 ;;
esac

# Flags parsed as a list, not by position: with two boards there is more than one
# thing a caller may want to say, and `grafana.sh eks --no-open` silently ignoring a
# misplaced flag is worse than refusing it.
OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    *) echo "ERROR: unknown option '$arg'" >&2; usage; exit 1 ;;
  esac
done

CTX="$(ensure_context "$TARGET")"
KUBECTL=(kubectl --context "$CTX")

# label|uid|configmap|url — pipe-delimited because bash 3.2 (macOS) has no dict type,
# the same shape config.sh uses for its dashboard contract.
BOARDS=(
  "GPU|${DASHBOARD_UID}|${DASHBOARD_CM}|$(grafana_dashboard_url)"
  "LLM|${LLM_DASHBOARD_UID}|${LLM_DASHBOARD_CM}|$(grafana_llm_dashboard_url)"
)

PF_PID=""
cleanup() { [[ -n "$PF_PID" ]] && kill "$PF_PID" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

echo "==> context $CTX"
# The Grafana Service and the admin Secret share the grafana subchart's fullname,
# so one lookup names both — and neither is predicted from KPS_RELEASE.
GRAF_SVC="$(resolve_kps_or_die svc grafana)" || exit 1
"${KUBECTL[@]}" -n "$MONITORING_NS" port-forward "svc/${GRAF_SVC}" "${GRAFANA_PORT}:80" >/dev/null 2>&1 &
PF_PID=$!

# Poll rather than sleep: the forward can also die immediately (port already in use),
# and a dead PID is the actionable failure — not a timeout the user has to interpret.
HAVE_CURL=0
if command -v curl >/dev/null 2>&1; then HAVE_CURL=1; fi   # `cmd && VAR=1` would trip `set -e`
ready=0
for _ in $(seq 1 30); do
  if ! kill -0 "$PF_PID" 2>/dev/null; then
    echo "ERROR: port-forward exited — is localhost:${GRAFANA_PORT} already in use?" >&2
    echo "       Retry on another port: GRAFANA_PORT=3001 ./scripts/grafana.sh $TARGET" >&2
    exit 1
  fi
  if [[ "$HAVE_CURL" -eq 1 ]]; then
    curl -fsS "http://localhost:${GRAFANA_PORT}/api/health" >/dev/null 2>&1 && { ready=1; break; }
  else
    sleep 3; ready=1; break   # no curl: fall back to a fixed settle, the forward is up
  fi
  sleep 1
done
[[ "$ready" -eq 1 ]] || { echo "ERROR: Grafana did not answer on localhost:${GRAFANA_PORT} within 30s" >&2; exit 1; }

# Non-fatal: a missing/renamed secret must not block read-only access, which is the
# whole point of the anonymous Viewer role.
pw="$("${KUBECTL[@]}" -n "$MONITORING_NS" get secret "${GRAF_SVC}" \
      -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d 2>/dev/null || true)"

open_url() {
  if command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}

# Ask Grafana whether each board is actually there before advertising its link.
# The sidecar loads the dashboard ConfigMaps asynchronously, so straight after an
# install one board can be live while the other still 404s — which looks like a broken
# link rather than a race. Anonymous Viewer can read this API, so no auth is needed.
# Warn-only: a 404 board must not stop the other one being usable.
for entry in "${BOARDS[@]}"; do
  label="${entry%%|*}"; entry="${entry#*|}"
  uid="${entry%%|*}";   entry="${entry#*|}"
  cm="${entry%%|*}";    url="${entry#*|}"

  echo "==> $label: $url"
  if [[ "$HAVE_CURL" -eq 1 ]]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' \
            "http://localhost:${GRAFANA_PORT}/api/dashboards/uid/${uid}" 2>/dev/null || echo 000)"
    if [[ "$code" == "404" ]]; then
      echo "    WARNING: no dashboard with uid '${uid}' in Grafana yet." >&2
      echo "             The sidecar may still be importing it — retry shortly, or check:" >&2
      echo "               kubectl --context $CTX -n $MONITORING_NS get cm $cm" >&2
      continue   # don't open a tab onto a known 404
    fi
  fi
  # `[[ ... ]] && open_url` as the loop's last command would make the iteration exit
  # non-zero under `set -e` whenever --no-open is passed. Keep it an if.
  if [[ "$OPEN_BROWSER" -eq 1 ]]; then open_url "$url"; fi
done

echo "    anonymous Viewer access — no login needed to view"
if [[ -n "$pw" ]]; then
  echo "    to edit: log in as 'admin' / '$pw'"
else
  echo "    admin password: secret ${GRAF_SVC} not readable (view-only is unaffected)"
fi

echo "    Ctrl-C to stop the port-forward"
wait "$PF_PID"
