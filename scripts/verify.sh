#!/usr/bin/env bash
# verify.sh <eks|gke|local> [--byo] — assert this repo's acceptance criteria: the GPU
# checks 1-5 (including 3b/4b/4c/4d) and the LLM checks L1-L9. Both sets of numbers are
# cited elsewhere in the repo and in commit history — do not renumber them. New checks
# get a letter suffix on the one they belong with, which is why 3b is 3b.
#
# --byo: the monitoring stack was NOT installed by this repo (see install.sh
# --skip-monitoring). Everything about the simulators, scrapes, rules and dashboards
# is still asserted; only the claims that follow from THIS repo's Helm values are
# relaxed. See the BYO block below for exactly which, and why that is a short list.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

TARGET="${1:?usage: verify.sh <eks|gke|local> [--byo]}"
# Positional and validated, matching install.sh's --skip-monitoring.
BYO=0
case "${2:-}" in
  "")      ;;
  --byo)   BYO=1 ;;
  *) echo "ERROR: unknown argument '${2}'" >&2
     echo "usage: verify.sh <eks|gke|local> [--byo]" >&2
     exit 1 ;;
esac

# The per-target CLI that ensure_context shells out to. On local that is kind, which
# resolves the kubeconfig entry the same way the cloud CLIs do for their clusters.
case "$TARGET" in
  eks)   CLOUD_CLI=aws ;;
  gke)   CLOUD_CLI=gcloud ;;
  local) CLOUD_CLI=kind ;;
  *)     echo "target must be eks|gke|local" >&2; exit 1 ;;
esac
# preflight: verify.sh needs these directly (curl/python3 for PromQL; the target CLI for ensure_context)
for t in kubectl curl python3 "$CLOUD_CLI"; do
  command -v "$t" >/dev/null 2>&1 || { echo "ERROR: '$t' not found on PATH" >&2; exit 1; }
done

CTX="$(ensure_context "$TARGET")"
KUBECTL=(kubectl --context "$CTX")

FAILED=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
# SKIP is not a failure: it marks a check whose precondition is legitimately
# absent (e.g. no simulated GPU was free to bind). It must never be used to
# paper over a real mismatch.
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; }

# Appended to the failures whose diagnosis differs on a BYO cluster. "ServiceMonitor
# selector?" is the right first thought when this repo installed the stack and the
# wrong one when it did not: there the object is usually fine and simply was never
# ADOPTED, which is invisible from the object itself. Empty in the normal mode, so
# the existing messages are unchanged.
byo_hint() {
  [[ "$BYO" == "1" ]] || return 0
  printf ' [BYO: usually the selector label — your Prometheus adopts objects matching release=<its release>, this install applied release=%s. Set RELEASE_LABEL/KPS_RELEASE, or set ruleSelectorNilUsesHelmValues + serviceMonitorSelectorNilUsesHelmValues false on your side.]' "$RELEASE_LABEL"
}

# --- helper: run a PromQL instant query via a self-healing port-forward ---------
#
# ⚠️ THE PID LIVES IN A FILE, NOT A VARIABLE, AND THAT IS THE WHOLE FIX.
#
# Every caller reaches these through command substitution — `x="$(promql_count ...)"`
# — which runs in a SUBSHELL. A plain `PF_PID=$!` assigned there is discarded when the
# subshell exits, so the parent's PF_PID stayed empty forever, prom_pf_up() reported
# "no forward" on EVERY call, and prom_pf_ensure() therefore rebuilt the forward and
# slept 4s for EVERY QUERY. The self-healing forward never healed; it only ever
# re-established.
#
# MEASURED on CI run 30867055387 (2026-08-04): consecutive single-shot checks land
# exactly 4.03s apart, run after run, while the two checks that issue no PromQL land
# 0.0s apart. ~37 calls per run, ~149s of a 247s verify.sh spent asleep. It also
# leaked one orphaned `kubectl port-forward` per call.
#
# ⚠️ THE GRAFANA FORWARD BELOW IS THE SAME DESIGN AND WORKS, which is the tell worth
# keeping: grafana_uid_check() is a plain function call, so its `GRAF_PF_PID=$!`
# reaches the shell that reads it. Identical code, different call convention, opposite
# outcome. If a future helper backgrounds anything and is read through `$(...)`, it
# needs a file too.
#
# A file survives the subshell because the filesystem does. Established once up front
# (see below) so the common case is a real child of THIS shell and `wait` can reap it;
# a forward rebuilt later from inside a substitution is reparented, which is why every
# kill/wait here tolerates failure.
PF_PIDFILE="$(mktemp)"
prom_pf_up() {
  local p; p="$(cat "$PF_PIDFILE" 2>/dev/null || true)"
  [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null
}
# `wait` after `kill` reaps the job synchronously. Without it the shell reports the
# reaped background job itself ("Terminated: 15") straight to the terminal — output we
# cannot redirect, and which reads like a failure in the middle of a passing run.
prom_pf_stop() {
  local p; p="$(cat "$PF_PIDFILE" 2>/dev/null || true)"
  [[ -n "$p" ]] && { kill "$p" >/dev/null 2>&1 || true; wait "$p" 2>/dev/null || true; }
  : > "$PF_PIDFILE"
}
prom_pf_ensure() {  # (re)establish the forward if it isn't alive — survives long polls
  prom_pf_up && return 0
  "${KUBECTL[@]}" -n "$MONITORING_NS" port-forward "svc/${KPS_RELEASE}-prometheus" 9090:9090 >/dev/null 2>&1 &
  echo $! > "$PF_PIDFILE"
  sleep 4
}
trap 'prom_pf_stop; rm -f "$PF_PIDFILE"' EXIT
# returns the number of result series for a query (ensures the forward first)
promql_count() {
  prom_pf_ensure
  curl -sG "http://localhost:9090/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("data",{}).get("result",[])))' 2>/dev/null || echo 0
}
# The largest VALUE a query returns, or nan if it returns nothing. Diagnostic only —
# no check asserts on it. promql_count() counts series and cannot see the numbers
# inside them, which is fine for an assertion phrased to return zero series on failure
# and useless for saying HOW FAR a converging quantity still has to go. L8 uses it to
# report its residual while it waits.
promql_value() {
  prom_pf_ensure
  curl -sG "http://localhost:9090/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import sys,json
r = json.load(sys.stdin).get("data", {}).get("result", [])
print(max((float(s["value"][1]) for s in r), default=float("nan")))' 2>/dev/null || echo nan
}
# The label KEYS on the first result series of a query, one per line (empty if the
# query returns nothing). Used by check 3b, which asserts a label SET rather than a
# series count — promql_count above cannot see inside the braces.
promql_label_keys() {
  prom_pf_ensure
  curl -sG "http://localhost:9090/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import sys,json
r = json.load(sys.stdin).get("data", {}).get("result", [])
print("\n".join(sorted(r[0].get("metric", {}))) if r else "")' 2>/dev/null || true
}

# How long checks 3, 4c and L4b wait for the FIRST DCGM scrape to land, in WALL-CLOCK
# SECONDS.
#
# ⚠️ SECONDS, BECAUSE AN ATTEMPT COUNT WAS NEVER THE BUDGET IT LOOKED LIKE — and every
# poll in this file used to carry the same defect. This was `DCGM_POLL_ATTEMPTS=24`
# against a `sleep 5`, which reads as 120s and said "120s" in the failure messages
# below. The real budget was 216s here, and 312s in check 3, which issues two queries
# per pass: every promql_count() call ALSO paid an unconditional `sleep 4` rebuilding
# a port-forward that should have been reused (see the PID-file note above). Measured
# on CI 2026-08-04: 4.03s per PromQL call, ~37 calls, ~149s of a 247s run.
#
# So fixing that forward without first moving these bounds would have cut every
# timeout in this file by ~45% in one commit, silently, while the diff looked like a
# pure speed-up. That is why the budgets moved first. A deadline in seconds cannot
# drift away from its stated value again.
#
# ONE constant because checks 3 and 4c must move together, and a comment saying so was
# not enough — 4c asserts a recording rule DERIVED from the metric 3 asserts, so if 3's
# window is the shorter of the two, a slow runner produces the contradictory result
# "the input is missing but the thing computed from it is present". That is confusing
# in exactly the wrong direction: it reads as a selector fault when it is a timing one.
#
# 240s. The incident that raised this from 60s needed ~73s, on a run that took 8m34s
# against a typical 5m; the worst first-scrape wait measured since is 47.8s (CI run
# 30867055387, 2026-08-04). 240s is over 3x the worst known and still bounded, which
# is the point: a genuine ServiceMonitor selector mismatch never resolves, so this
# must fail rather than hang.
DCGM_POLL_SECONDS=240

echo "Verifying $TARGET (context: $CTX)"

# WHAT --byo RELAXES, AND WHY THE LIST IS THIS SHORT.
#
# The instinct is to skip a lot here. That would be wrong: almost every check in
# this file is about the SIMULATORS, the scrapes, the rules and the dashboards, and
# every one of those is exactly what a BYO user most needs asserted — they are the
# things a mismatched RELEASE_LABEL or sidecar label silently breaks. Skipping them
# would turn --byo into a mode that proves nothing on the install that needs proof
# most.
#
# So the only claim relaxed is the one that follows from THIS repo's Helm values
# rather than from anything it installed: anonymous Viewer access to Grafana
# (helm/kube-prometheus-stack/values.yaml). On a foreign Grafana that is the
# operator's choice, so 401/403 becomes a SKIP.
#
# ⚠️ 404 STAYS FATAL, and that is the point. A board that Grafana has never heard of
# means the sidecar did not import the ConfigMap — overwhelmingly because
# GRAFANA_DASHBOARD_LABEL does not match what their sidecar watches, which is the
# single most likely way a BYO install appears broken. Downgrading that to a skip
# would hide the failure this mode exists to surface.
if [[ "$BYO" == "1" ]]; then
  echo "  BYO mode: the monitoring stack was not installed by this repo."
  echo "    release '$KPS_RELEASE' · selector label 'release=$RELEASE_LABEL'"
  echo "    dashboards labelled '${GRAFANA_DASHBOARD_LABEL}=${GRAFANA_DASHBOARD_LABEL_VALUE}'"
  echo "    Relaxed: anonymous Grafana access (401/403 -> SKIP). Everything else is asserted."
fi

# 1. a node advertises nvidia.com/gpu allocatable > 0
max_gpu="$("${KUBECTL[@]}" get nodes -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' \
  | grep -vE '^$' | sort -rn | head -1 || echo 0)"
[[ "${max_gpu:-0}" =~ ^[0-9]+$ && "${max_gpu:-0}" -gt 0 ]] \
  && pass "nvidia.com/gpu allocatable = $max_gpu (no physical GPU)" \
  || fail "no node advertises nvidia.com/gpu — check node label $NODE_POOL_LABEL_KEY=$NODE_POOL_NAME and topology name"

# 2. a sample GPU workload is Running
running="$("${KUBECTL[@]}" get pods -l app.kubernetes.io/part-of=gpu-sim-workloads -A \
  --field-selector=status.phase=Running -o name 2>/dev/null | wc -l | tr -d ' ')"
[[ "${running:-0}" -gt 0 ]] && pass "$running sample GPU workload pod(s) Running" \
  || fail "no sample GPU workload Running"

# 3. DCGM scrape target up AND a known series returns data (promql_count self-establishes the port-forward)
#    Polled for exactly the reason 4c below is, and the two must stay consistent: the
#    ServiceMonitor is applied BEFORE the exporter it selects, so the target can be registered
#    and still not scraped once. This was single-shot and flaked in CI — `up` and
#    DCGM_FI_DEV_GPU_UTIL both read empty here, while the series DERIVED from that same metric
#    passed 60s later. A derived series cannot exist without its input, so the only reading is
#    that this check ran before the first scrape landed, not that anything was broken.
#    Shares DCGM_POLL_SECONDS with 4c — see the constant for why they are one value.
#
#    Established HERE, in this shell, rather than left to the first promql_count()
#    inside a `$(...)`. Both work now that the pid is in a file, but a forward opened
#    by the main shell is a real child of it, so prom_pf_stop can `wait` and reap it
#    instead of leaving the kill unacknowledged. This is also the one place the 4s
#    settle is genuinely paid; every later call reuses it.
prom_pf_ensure
up=0; util=0
deadline=$(( SECONDS + DCGM_POLL_SECONDS ))
while :; do
  up="$(promql_count 'up{job=~".*dcgm.*"} == 1')"
  util="$(promql_count 'DCGM_FI_DEV_GPU_UTIL')"
  [[ "${up:-0}" -gt 0 && "${util:-0}" -gt 0 ]] && break
  (( SECONDS >= deadline )) && break
  sleep 5
done
[[ "${up:-0}" -gt 0 ]] && pass "DCGM scrape target up" || fail "no DCGM scrape target up (ServiceMonitor selector?)$(byo_hint)"
[[ "${util:-0}" -gt 0 ]] && pass "DCGM_FI_DEV_GPU_UTIL returns $util series" || fail "DCGM_FI_DEV_GPU_UTIL empty"

# 3b. The CLUSTER side of the DCGM surface contract — the same file
#     compose/gpu-metrics-sim.py --selftest asserts against, so one committed
#     artefact covers two independent producers of this surface. Without it, a
#     chart bump that renames a series or a label fails here loudly and lets the
#     compose path drift in silence.
#
#     ⚠️ A SUBSET, NOT AN EXACT MATCH, and an exact match would fail on day one.
#     Series arriving through Prometheus carry labels the exporter never emitted —
#     job, instance, namespace, pod, endpoint, service — attached from the
#     ServiceMonitor's target at scrape time. The exporter's own pod/namespace
#     labels arrive renamed to exported_* because target labels win the collision
#     (docs/observability.md). So extra keys are expected and are not drift; what
#     must hold is that every series and every label key the contract names is
#     present. The contract file's header states both semantics.
#
#     Not polled: check 3 above has already waited out the first scrape, so
#     anything missing here is missing rather than late.
DCGM_CONTRACT="tests/contracts/dcgm-surface.json"
if [[ ! -f "$DCGM_CONTRACT" ]]; then
  fail "3b $DCGM_CONTRACT not found — the DCGM surface contract is what makes the compose and cluster producers comparable (run from the repo root)"
else
  contract_series="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["series"]))' "$DCGM_CONTRACT")"
  contract_labels="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["labels"]))' "$DCGM_CONTRACT")"
  surface_ok=1
  while IFS= read -r cseries; do
    [[ -n "$cseries" ]] || continue
    keys="$(promql_label_keys "$cseries")"
    if [[ -z "$keys" ]]; then
      fail "3b contract series $cseries returns nothing — the fake exporter's surface has changed, or the chart pin moved (config.sh FAKE_GPU_CHART_VERSION)"
      surface_ok=0
      continue
    fi
    missing=""
    while IFS= read -r clabel; do
      [[ -n "$clabel" ]] || continue
      printf '%s\n' "$keys" | grep -qx -- "$clabel" || missing="$missing $clabel"
    done <<< "$contract_labels"
    if [[ -n "$missing" ]]; then
      fail "3b $cseries is missing contract label key(s):$missing — the dashboard legends and the recording-rule joins bind to these, so a rename blanks a legend without blanking the panel"
      surface_ok=0
    fi
  done <<< "$contract_series"
  [[ "$surface_ok" -eq 1 ]] \
    && pass "3b cluster exporter satisfies the DCGM surface contract ($DCGM_CONTRACT)"
fi

# 4. OUR DCGM dashboard ConfigMap is present *by name* (not just any grafana_dashboard cm,
#    of which the chart ships several) AND its core query returns data — the scriptable
#    proxy for "panels are non-empty".
if "${KUBECTL[@]}" -n "$MONITORING_NS" get cm "$DASHBOARD_CM" >/dev/null 2>&1; then
  pass "DCGM dashboard ConfigMap '$DASHBOARD_CM' present (sidecar-discovered)"
else
  fail "DCGM dashboard ConfigMap '$DASHBOARD_CM' not found"
fi
[[ "${util:-0}" -gt 0 ]] && pass "dashboard core series (DCGM_FI_DEV_GPU_UTIL) has data → panels non-empty" \
  || fail "dashboard core series empty → panels would be blank (label mismatch?)"

# 4c. The temp/power panels query series the fake exporter does NOT emit — they exist only
#     because of the recording rules in manifests/alerts/. That coupling is invisible from
#     the dashboard JSON, so assert it: a dropped/renamed rule shows up here rather than as
#     two quietly blank panels nobody notices for a week.
#     Polled, not single-shot: a recording rule only materialises on its next evaluation
#     (30s), so DCGM_FI_DEV_GPU_UTIL can be scraped and present while the derived series
#     is still one tick away. A single-shot check would flake right after install.
for m in DCGM_FI_DEV_GPU_TEMP DCGM_FI_DEV_POWER_USAGE; do
  n=0
  deadline=$(( SECONDS + DCGM_POLL_SECONDS ))
  while :; do
    n="$(promql_count "$m")"
    [[ "${n:-0}" -gt 0 ]] && break
    (( SECONDS >= deadline )) && break
    sleep 5
  done
  [[ "${n:-0}" -gt 0 ]] && pass "derived series $m returns $n series (recording rule live)" \
    || fail "$m empty after ${DCGM_POLL_SECONDS}s → dashboard temp/power panel blank (are the recording rules in manifests/alerts/ applied?)$(byo_hint)"
done

# 4b. The advertised access path itself: fetch the board by UID over an UNAUTHENTICATED
#     request. One check covers both halves — the sidecar really imported it under
#     $DASHBOARD_UID (so the /d/<uid> link install.sh prints resolves), and anonymous
#     Viewer auth is live (so that link needs no password).
#     Self-healing and established on demand, exactly like the Prometheus forward
#     above — and for the same reason. The GPU board is fetched here; the LLM board
#     is fetched ~130 lines later, and between them sits the 6-minute
#     GPUHighUtilization poll. A kubectl port-forward does not survive that idle, so
#     a forward opened once and never re-checked is already dead by the second
#     fetch: the GPU check passes, the LLM check reports http 000, and the two
#     disagree about a Grafana that was healthy the whole time.
GRAF_PF_PID=""
graf_pf_up()   { [[ -n "$GRAF_PF_PID" ]] && kill -0 "$GRAF_PF_PID" 2>/dev/null; }
graf_pf_stop() {  # `wait` reaps the job quietly — see prom_pf_stop
  [[ -n "$GRAF_PF_PID" ]] && { kill "$GRAF_PF_PID" >/dev/null 2>&1 || true; wait "$GRAF_PF_PID" 2>/dev/null || true; }
  GRAF_PF_PID=""
}
graf_pf_ensure() {  # (re)establish the forward if it isn't alive — survives long polls
  graf_pf_up && return 0
  "${KUBECTL[@]}" -n "$MONITORING_NS" port-forward "svc/${KPS_RELEASE}-grafana" "${GRAFANA_PORT}:80" >/dev/null 2>&1 &
  GRAF_PF_PID=$!; sleep 4
}
trap 'prom_pf_stop; graf_pf_stop; rm -f "$PF_PIDFILE"' EXIT

# Shared by every dashboard check: fetch a board by UID over an UNAUTHENTICATED
# request, and translate the HTTP status into an actionable message. One
# implementation so the GPU and LLM boards can never be checked differently.
# 40s. This loop's bound was always honest — graf_pf_ensure() is called from a plain
# function body, so its forward really is reused and a pass really did cost ~2s — but
# it is expressed in seconds anyway so that "every poll in this file is bounded in
# wall-clock seconds" is a property you can grep for rather than one you have to
# audit loop by loop.
GRAFANA_UID_POLL_SECONDS=40
grafana_uid_check() {
  local uid="$1" label="$2" url="$3" code=000
  local deadline=$(( SECONDS + GRAFANA_UID_POLL_SECONDS ))
  while :; do
    graf_pf_ensure                    # rebuilds the forward if it idled out
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${GRAFANA_PORT}/api/dashboards/uid/${uid}" || echo 000)"
    [[ "$code" == "200" ]] && break
    (( SECONDS >= deadline )) && break
    # Died mid-fetch (rather than merely answering badly)? Drop the pid so the next
    # pass rebuilds instead of curling at a port nothing is listening on.
    graf_pf_up || GRAF_PF_PID=""
    sleep 2
  done
  case "$code" in
    200) pass "$label dashboard reachable anonymously at $url" ;;
    401|403)
      # The one claim --byo relaxes — see the BYO block above. Under our own values
      # this is a real failure; on a foreign Grafana it is the operator's choice, and
      # the board being PRESENT is what we actually needed to establish.
      if [[ "$BYO" == "1" ]]; then
        skip "$label dashboard uid '$uid' exists but needs auth — anonymous Viewer is this repo's values choice, not yours (http $code)"
      else
        fail "$label dashboard uid '$uid' needs auth — anonymous Viewer not enabled (helm/kube-prometheus-stack/values.yaml)"
      fi
      ;;
    404)
      # Fatal in BOTH modes, and MORE informative in BYO: on a foreign cluster the
      # overwhelmingly likely cause is a sidecar label this repo cannot know.
      if [[ "$BYO" == "1" ]]; then
        fail "no dashboard with uid '$uid' in Grafana — the ConfigMap exists but the sidecar never imported it. Does your Grafana watch '${GRAFANA_DASHBOARD_LABEL}=${GRAFANA_DASHBOARD_LABEL_VALUE}' (GRAFANA_DASHBOARD_LABEL), and does it search namespace '$MONITORING_NS'?"
      else
        fail "no dashboard with uid '$uid' in Grafana — the /d/<uid> link would 404 (sidecar not imported it yet?)"
      fi
      ;;
    *)   fail "could not reach Grafana on localhost:${GRAFANA_PORT} (http $code) — the port-forward could not be established or kept alive. Is ${GRAFANA_PORT} already in use? try GRAFANA_PORT=3001" ;;
  esac
}
grafana_uid_check "$DASHBOARD_UID" "GPU" "$(grafana_dashboard_url)"

# 4d. Utilisation is actually BEING DRIVEN, not merely present.
#     Every check above is satisfied by a series that exists and reads 0: the exporter
#     emits DCGM_FI_DEV_GPU_UTIL for every simulated GPU whether or not any pod annotation
#     is taking effect. So a broken annotation path — the exact failure gpu-workloads.yaml
#     warns about (annotation on the Deployment instead of the pod template) — would leave
#     every check green with flat-zero panels. Assert the 'busy' workload's band (85-99)
#     is really reaching the metric.
#     Polled: the pod must be scheduled, admitted and scraped first.
#     DELIBERATELY NOT on DCGM_POLL_SECONDS, though it polls the same metric. By the
#     time this runs, check 3 has already established that DCGM_FI_DEV_GPU_UTIL exists,
#     so this budget is measuring something else entirely: whether the ANNOTATION is
#     reaching the exporter. Sharing the constant would tie an annotation-propagation
#     budget to a first-scrape one and make both harder to reason about.
#     120s: the effective budget before the port-forward fix was 108s (12 passes at 4s
#     of forward plus 5s of sleep) and the stated one was 60s. Neither has ever been
#     approached — worst measured is one pass — so this rounds the true figure up
#     rather than reasoning from the number that was only ever nominal.
UTIL_DRIVEN_POLL_SECONDS=120
util_hi=0
deadline=$(( SECONDS + UTIL_DRIVEN_POLL_SECONDS ))
while :; do
  util_hi="$(promql_count 'max(DCGM_FI_DEV_GPU_UTIL) > 80')"
  [[ "${util_hi:-0}" -gt 0 ]] && break
  (( SECONDS >= deadline )) && break
  sleep 5
done
[[ "${util_hi:-0}" -gt 0 ]] && pass "simulated utilisation is being driven (max DCGM_FI_DEV_GPU_UTIL > 80)" \
  || fail "no GPU above 80% util after ${UTIL_DRIVEN_POLL_SECONDS}s — the 'gpu-busy' annotation is not reaching the exporter. Check that run.ai/simulated-gpu-utilization sits on spec.template.metadata.annotations, not the Deployment's metadata."

# 5. THE utilisation alert can reach 'firing' (poll, waiting out the rule's for: duration).
#    Named exactly, NOT alertname=~".*GPU.*": GPUHighMemoryUsage is allocation-driven, so
#    it fires permanently for every GPU that has a pod on it (a busy GPU reports
#    FB_USED=all/FB_FREE=0 — see docs/observability.md). A wildcard here is satisfied by
#    that alert alone and would pass with utilisation stuck at zero, which is the one
#    thing this check exists to prove.
#    360s, and the message now says what the code does. It used to say 6m while
#    granting 504s, for the reason DCGM_POLL_SECONDS documents. The rule's `for:` is
#    1m and the worst measured wait is 46.1s, so 360s keeps ~8x margin.
GPU_ALERT_POLL_SECONDS=360
echo "  (polling up to 6m for GPUHighUtilization to fire...)"
fired=0
deadline=$(( SECONDS + GPU_ALERT_POLL_SECONDS ))
while :; do
  n="$(promql_count 'ALERTS{alertstate="firing", alertname="GPUHighUtilization"}')"
  if [[ "${n:-0}" -gt 0 ]]; then fired=1; break; fi
  (( SECONDS >= deadline )) && break
  sleep 10
done
[[ "$fired" -eq 1 ]] && pass "GPUHighUtilization is firing" \
  || fail "GPUHighUtilization did not fire within timeout (check the 'gpu-busy' workload util range 85-99 vs the rule's >80 threshold and 1m for:)"

# ============================================================================
# LLM simulation checks — numbered L1..L9 so they can never be confused with
# the GPU checks above (which already use 1..5 and 4b/4c/4d).
#
# ⚠️ EVERY check below is phrased so it returns ZERO SERIES on failure.
#    promql_count() counts result series; it does not evaluate them. A bare
#    histogram_quantile(...) returns a series even when its value is NaN, and
#    sum(a)/sum(b) returns one when b is zero (+Inf) — so a naive check would
#    count 1 and pass with no data at all. Comparison operators filter both:
#    NaN > 0 is false, and bounding the top end catches +Inf.
# ============================================================================
echo
echo "LLM simulation:"

# L1. the simulators are being scraped
llm_up="$(promql_count 'up{job="llm-sim"} == 1')"
[[ "${llm_up:-0}" -gt 0 ]] && pass "L1 $llm_up LLM scrape target(s) up" \
  || fail "L1 no LLM scrape target up — is the llm-sim ServiceMonitor selecting the Service? (kubectl -n $LLM_NS get svc --show-labels)$(byo_hint)"

# L2. a counter is ADVANCING, not merely present
llm_tok="$(promql_count 'rate(vllm:generation_tokens_total[1m]) > 0')"
[[ "${llm_tok:-0}" -gt 0 ]] && pass "L2 generation tokens advancing ($llm_tok model(s))" \
  || fail "L2 rate(vllm:generation_tokens_total[1m]) is zero — simulators serving but not completing requests?"

# L3. the TTFT histogram is well-formed AND the healthy tenant is genuinely
#     healthy. Scoped to the steady model: the rules aggregate by(model_name)
#     and llm-saturated is degraded on purpose, so an unscoped bound would be
#     asserting against the tenant we deliberately broke.
#     The upper bound is the alert threshold itself, so this checks the capacity
#     model end to end rather than merely that a number exists.
llm_obs="$(promql_count 'rate(vllm:time_to_first_token_seconds_count[5m]) > 0')"
[[ "${llm_obs:-0}" -gt 0 ]] && pass "L3 TTFT histogram is receiving observations" \
  || fail "L3 no TTFT observations — histogram present but empty?"

llm_p95=0
deadline=$(( SECONDS + 180 ))   # recording rules need a couple of evaluations to exist
while :; do
  llm_p95="$(promql_count "llm:ttft:p95_5m{model_name=\"${LLM_STEADY_MODEL}\"} > 0 and llm:ttft:p95_5m{model_name=\"${LLM_STEADY_MODEL}\"} < 2")"
  [[ "${llm_p95:-0}" -gt 0 ]] && break
  (( SECONDS >= deadline )) && break
  sleep 5
done
[[ "${llm_p95:-0}" -gt 0 ]] && pass "L3 steady tenant p95 TTFT is finite and under the 2s alert threshold" \
  || fail "L3 llm:ttft:p95_5m for '$LLM_STEADY_MODEL' is absent, NaN, or >=2s — recording rule applied? capacity model still balanced? (see manifests/llm/10-profiles.yaml)"

# L3b. no tenant is insane. The expected count is DERIVED, not hardcoded:
#      applying the opt-in llm-driven extras takes it from 2 to 3, and a
#      hardcoded 2 would fail the moment someone used them. One
#      llmsim_profile_generation series exists per running simulator pod.
# Bound is 120s, not 60s. A saturated tenant's TTFT is the REAL queue wait now
# (measured from the clock, not modelled from queue depth), so by Little's Law it
# is (max_in_flight - max_concurrency) / capacity = 160 / 2.74 ~= 58s, and p95 sits
# above the mean. The old 60s bound suited the previous model, whose synthetic
# penalty of 0.05s per queued request implied a 20 rps service rate that the rest
# of the model contradicted.
#
# ⚠️ WHAT THIS BOUND IS REALLY TESTING, since the V1 bucket sync. 58s lands in
# V1's (40, 80] TTFT bucket, so the REPORTED p95 is quantised to
# 40 + 40*0.95 = 78 for any true latency inside that band — comfortably under
# 120. But the next bucket up is (80, 160], which interpolates to 152, so this
# check does not degrade gracefully: it passes at 78 and then jumps straight past
# 120 the moment the true p95 crosses 80s. Read it as "the queue wait has not
# escaped the (40, 80] band", not as a 120s budget with 42s of headroom. If you
# raise max_in_flight or cut capacity, re-derive it rather than nudging it up.
llm_sane="$(promql_count 'count(llm:ttft:p95_5m < 120) == count(llmsim_profile_generation)')"
[[ "${llm_sane:-0}" -gt 0 ]] && pass "L3b every running simulator has a sane p95 TTFT (<120s)" \
  || fail "L3b at least one tenant has p95 TTFT >= 120s or is missing a recording-rule series"

# L4. cross-domain, cluster-aggregate. Both operands filtered so a zero
#     denominator yields no series rather than +Inf.
llm_tpw="$(promql_count '(sum(rate(vllm:generation_tokens_total[5m])) > 0) / (sum(DCGM_FI_DEV_POWER_USAGE) > 0)')"
[[ "${llm_tpw:-0}" -gt 0 ]] && pass "L4 cross-domain tokens-per-watt returns a finite value" \
  || fail "L4 cross-domain expression empty — are both DCGM_FI_DEV_POWER_USAGE (derived) and the vllm counters present?"

# L4b. GPU attribution, CONDITIONAL. Absent binding = SKIP (llm-saturated runs
#      unbound by design, and llm-steady runs unbound if no GPU was free).
#      Present-but-not-matching is a real failure and must not be skipped.
#
# JOINED ON THE POD, NOT ON A UUID — and that is not a style choice. The value the
# device plugin injects as MOCK_NVIDIA_VISIBLE_DEVICES is its own per-allocation id
# (bare random v4), while the exporter labels the same GPU with a deterministic
# "GPU-"-prefixed v5 id from the topology ConfigMap. `on (UUID)` therefore matches
# NOTHING, forever, on chart 0.0.59 — see detect_binding() in scripts/llm-sim.py.
#
# The fake exporter does label each allocated GPU with its consuming pod, but
# Prometheus renames those to exported_namespace/exported_pod because the scrape
# target's own namespace/pod labels win the collision. label_replace maps them back
# so the join has a common key.
#
# POLLED, and on DCGM_POLL_SECONDS because it waits on the same producer. The two
# sides of this join do not appear together: the simulator emits
# llmsim_gpu_binding_info as soon as it reads MOCK_NVIDIA_VISIBLE_DEVICES at start-up,
# whereas exported_pod only appears once the fake exporter has re-read the topology
# after the allocation AND Prometheus has scraped it again. Single-shot, this asserted
# "the binding is present but nothing matches it" during the window where that is
# simply not true yet — it failed on the LITE leg while full passed, on identical
# binding logs. Waiting does not weaken it: a binding that never resolves still fails,
# which is the case this check exists to catch.
llm_bind="$(promql_count 'llmsim_gpu_binding_info')"
if [[ "${llm_bind:-0}" -gt 0 ]]; then
  llm_join=0
  deadline=$(( SECONDS + DCGM_POLL_SECONDS ))
  while :; do
    llm_join="$(promql_count 'llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu) label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""}, "namespace", "$1", "exported_namespace", "(.*)"), "pod", "$1", "exported_pod", "(.*)")')"
    [[ "${llm_join:-0}" -gt 0 ]] && break
    (( SECONDS >= deadline )) && break
    sleep 5
  done
  [[ "${llm_join:-0}" -gt 0 ]] && pass "L4b GPU binding resolves to a real DCGM series (joined on pod)" \
    || fail "L4b llmsim_gpu_binding_info exists but after ${DCGM_POLL_SECONDS}s no DCGM_FI_DEV_GPU_UTIL series is labelled with that pod"
else
  skip "L4b no simulator holds a simulated GPU (unbound) — nothing to join"
fi

# L5. the LLM dashboard, over the same unauthenticated path as the GPU board.
if "${KUBECTL[@]}" -n "$MONITORING_NS" get cm "$LLM_DASHBOARD_CM" >/dev/null 2>&1; then
  pass "L5 LLM dashboard ConfigMap '$LLM_DASHBOARD_CM' present (sidecar-discovered)"
else
  fail "L5 LLM dashboard ConfigMap '$LLM_DASHBOARD_CM' not found"
fi
grafana_uid_check "$LLM_DASHBOARD_UID" "L5 LLM" "$(grafana_llm_dashboard_url)"

# L6. the alert. Driven by the llm-saturated Deployment that install.sh applies —
#     verify.sh does NOT patch a profile to make this true. Selected by EXACT
#     name: a wildcard would be satisfied by LLMQueueBacklog, which is also
#     permanently firing, and would pass with the latency model broken.
#     300s, and the message now matches: it said 5m while granting 420s. The rule's
#     `for:` is 2m and the worst measured wait is 60.2s.
LLM_ALERT_POLL_SECONDS=300
echo "  (polling up to 5m for LLMHighTTFT to fire...)"
llm_fired=0
deadline=$(( SECONDS + LLM_ALERT_POLL_SECONDS ))
while :; do
  n="$(promql_count 'ALERTS{alertstate="firing", alertname="LLMHighTTFT"}')"
  if [[ "${n:-0}" -gt 0 ]]; then llm_fired=1; break; fi
  (( SECONDS >= deadline )) && break
  sleep 10
done
[[ "$llm_fired" -eq 1 ]] && pass "L6 LLMHighTTFT is firing (driven by llm-saturated)" \
  || fail "L6 LLMHighTTFT did not fire — is llm-saturated Running, and is its arrival_rate_rps still above the 2.74 rps capacity? (manifests/llm/10-profiles.yaml)"

# L7. queue time and prefix caching actually SURVIVE A REAL SCRAPE.
#     Everything else that covers these two families is a selftest assertion or a
#     promtool test, and neither of those leaves the repo: --selftest proves the
#     simulator emits a series, and this proves Prometheus receives one. They are
#     different claims, and the gap between them is where a ServiceMonitor,
#     a relabel rule or an exposition-format mistake lives.
#
#     Single-shot, unlike L3 — by the time this runs, L6 has just spent up to
#     five minutes polling, so the first scrape landed long ago. Anything empty
#     here is missing, not late.
llm_qt="$(promql_count 'rate(vllm:request_queue_time_seconds_count[5m]) > 0')"
[[ "${llm_qt:-0}" -gt 0 ]] && pass "L7 queue-time histogram is receiving observations" \
  || fail "L7 no vllm:request_queue_time_seconds observations — histogram present but empty, or not scraped at all"

# `and` on two label-less vectors: empty if EITHER counter is absent, which keeps
# this inside the zero-series-on-failure rule this block opens with.
llm_pc="$(promql_count 'count(vllm:prefix_cache_queries_total) > 0 and count(vllm:prefix_cache_hits_total) > 0')"
[[ "${llm_pc:-0}" -gt 0 ]] && pass "L7 both prefix-cache counters are present" \
  || fail "L7 vllm:prefix_cache_queries_total and/or _hits_total absent — are the simulator pods running the current scripts/llm-sim.py? (install.sh rebuilds the llm-sim-script ConfigMap, but a running pod keeps the old mount until it restarts)"

# L8. the request phase breakdown survives a real scrape, for the same reason
#     L7 exists: --selftest proves the simulator EMITS the three histograms and
#     promtool proves the PromQL over them is right, and neither of those leaves
#     the repo. This proves Prometheus RECEIVES them. The gap between those
#     claims is where a ServiceMonitor, a relabel rule or an exposition-format
#     mistake lives.
#
#     ⚠️ L8 is the next free label in THIS script and that is what decides it.
#     prompt-llm-sim.md also uses L7/L8, but says at :861 that neither is a
#     verify.sh check — its L7 is "every pre-existing check still passes" and
#     its L8 is "teardown.sh removes the namespace". verify.sh has since taken
#     L7 for the queue-time and prefix-cache assertion above, so that label
#     already means two things depending on which document you read. Skipping to
#     L9 to dodge the clash would leave a hole in this script's sequence to
#     protect a label in a brief that explicitly is not about this script.
#     verify.sh's labels are authoritative for verify.sh, and they are
#     contiguous.
#
#     Single-shot, in the style of L7: by the time this runs L6 has polled for
#     up to five minutes, so the first scrape landed long ago. Anything empty
#     here is missing, not late.
#
#     All three in ONE expression, `and`-ed on the counts. `and` between
#     label-less vectors yields nothing if ANY of them is absent, which keeps
#     this inside the zero-series-on-failure rule this block opens with — and
#     rate(..._count[5m]) > 0 is the ADVANCING form, so a histogram that exists
#     and never receives an observation fails rather than passing on presence.
llm_phases="$(promql_count 'count(rate(vllm:request_prefill_time_seconds_count[5m]) > 0) > 0
  and count(rate(vllm:request_decode_time_seconds_count[5m]) > 0) > 0
  and count(rate(vllm:request_inference_time_seconds_count[5m]) > 0) > 0')"
[[ "${llm_phases:-0}" -gt 0 ]] && pass "L8 all three request phase histograms are receiving observations" \
  || fail "L8 one or more of vllm:request_{prefill,decode,inference}_time_seconds is absent or not advancing — are the simulator pods running the current scripts/llm-sim.py? (install.sh rebuilds the llm-sim-script ConfigMap and the checksum annotation rolls the pods, but a pod that never rolled keeps the old mount)"

# And that the breakdown ADDS UP against what Prometheus actually recorded.
# promtool asserts this over fixtures; this asserts it over real observations,
# which is a different claim — the fixtures cannot catch a rule that was applied
# to the cluster in a stale form, or a phase histogram wired to the wrong term.
#
# RELATIVE, not absolute, and that distinction is the whole of the third fix
# below. Polled, because four recording rules need a couple of evaluations to
# exist at all.
#
# ⚠️ COUNTED, not asserted as "at least one" — and the right-hand side is the
# part that took two CI runs to get right, so it is written down.
#
# FIRST DRAFT: `count(...) > 0`. Both legs passed it reporting ONE tenant on a
# cluster running two — a saturated tenant whose breakdown had stopped summing
# would have hidden behind the healthy one. That is the hollow-green failure
# this file exists to prevent, reintroduced by the check meant to prevent it.
#
# SECOND DRAFT: `== count(llm:e2e:mean5m)`. Too strict, and it FAILED CI for a
# reason that is not a fault: a tenant can carry a recorded e2e mean at an
# evaluation instant where one of its phase means has not yet produced a sample
# (rate() needs two points in the window, and the four rules are not guaranteed
# to cross that line on the same evaluation). The left side then has fewer
# series than the right forever, on a rig that is working perfectly.
#
# THIRD DRAFT — and the one that matters: `abs(...) < 1e-6`, an ABSOLUTE bound.
# It failed the lite leg with 1 of 2 tenants summing, both carrying a complete
# breakdown. Not a missing rule: one tenant genuinely exceeded the bound.
#
# ⚠️ THE BOUND WAS THE WRONG SHAPE, not the wrong number, and the earlier
# measurement that justified it was taken under conditions the cluster does not
# reproduce. It ran the simulator from a clock starting at ZERO and got ~1e-15.
# A cluster starts it at time.monotonic(). e2e is read off the clock as
# (admit + prefill + decode) - arrived, so the cancellation happens between two
# LARGE numbers, and float64's absolute precision near X is X*2^-52 — the error
# therefore scales with the CLOCK, not with the latency. MEASURED, by rerunning
# the same offline replication with the start time moved:
#
#     clock start   worst residual
#     0 .. 1e4      ~1e-15
#     1e6            1.2e-9
#     1e9            3.7e-9
#
# Prometheus's rate() adds more on top: it extrapolates to the window edges and
# corrects for counter resets, and neither correction is proportional across
# four series whose magnitudes differ by three orders (queue ~58s, prefill
# ~0.08s). Those corrections do not cancel in the subtraction.
#
# An absolute bound on a quantity whose error scales with its inputs was never
# going to hold. So the comparison is RELATIVE — the breakdown must account for
# essentially all of e2e, rather than land within a fixed number of seconds of
# it. A genuinely mis-wired phase moves this by a LARGE FRACTION of e2e (a
# dropped term is 0.1-0.9 of it), so 1e-3 keeps three orders of margin over the
# fault it exists to catch while being immune to the arithmetic above.
#
# clamp_min on the denominator for the same reason the rules themselves use it:
# an idle tenant has all four means at zero, and 0/0 would be NaN — which is not
# < 1e-3, so it would be counted as a failure rather than as the nothing it is.
#
# SO: compare against the tenants for which the arithmetic PRODUCED A VALUE AT
# ALL. `count(expr)` counts exactly the label sets where all four means were
# present, so this asserts "every tenant with a complete breakdown has one that
# adds up" — which is the claim worth making. A tenant whose phase means are
# permanently missing is not silently excused: the assertion above this one
# already fails if any of the three histograms is absent or not advancing.
# ⚠️ 420s, AND THE ONLY BUDGET IN THIS FILE THAT WAS GENUINELY TOO SMALL. Measured
# across three CI runs on 2026-08-04: the `full` leg converges in 12.1s every time, to
# two decimal places, while `lite` took 12.1s, 129.5s and 201.7s on the same commit
# range. Against the effective budget of ~212s (24 passes at 4s of port-forward plus
# 5s of sleep), the 201.7s run consumed 22 of 24 attempts — roughly 18 seconds from a
# red leg, on a rig that was working perfectly. Every other bound here is being
# tightened toward its stated value; this one is doubled, because the evidence points
# the other way.
#
# ⚠️ WHY `lite` VARIES BY 17x IS NOT YET KNOWN, and the obvious causes are ruled out
# rather than assumed: the diagnostics bundle for the 201.7s run shows Prometheus at
# RESTARTS 0, no OOM or eviction events, and rule-group evaluationTime of 2.4ms on a
# 30s interval. Prometheus was healthy and fast on that leg. What remains is the
# rate()-over-a-partly-filled-window arithmetic the block above anatomises, which is a
# claim needing measurement rather than another paragraph of reasoning. Hence the
# instrumentation below: every future run now reports how long this took and what the
# residual was while it waited, so the next slow leg explains itself instead of
# needing a rerun to reproduce.
L8_POLL_SECONDS=420
llm_sums=0
l8_started=$SECONDS
l8_passes=0
deadline=$(( SECONDS + L8_POLL_SECONDS ))
while :; do
  llm_sums="$(promql_count 'count(abs((llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m)
      / clamp_min(llm:e2e:mean5m, 1e-9) < 1e-3)
    == count((llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m)')"
  [[ "${llm_sums:-0}" -gt 0 ]] && break
  (( SECONDS >= deadline )) && break
  # Every ~30s, not every pass: enough to shape the convergence curve in the log,
  # quiet enough that a normal run (which exits on the first pass) prints nothing.
  l8_passes=$(( l8_passes + 1 ))
  if (( l8_passes % 6 == 0 )); then
    printf '    L8 still converging after %ds — worst relative residual %s (want < 1e-3)\n' \
      "$(( SECONDS - l8_started ))" \
      "$(promql_value 'max(abs((llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m) / clamp_min(llm:e2e:mean5m, 1e-9))')"
  fi
  sleep 5
done
l8_elapsed=$(( SECONDS - l8_started ))
# Reported on both paths so a future failure is diagnosable from the summary
# rather than from a rerun: "3 of 4" and "0 of 0" are different faults.
llm_ok_n="$(promql_count 'abs((llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m) / clamp_min(llm:e2e:mean5m, 1e-9) < 1e-3')"
llm_all_n="$(promql_count '(llm:queue:mean5m + llm:prefill:mean5m + llm:decode:mean5m) - llm:e2e:mean5m')"
[[ "${llm_sums:-0}" -gt 0 ]] && pass "L8 the phase breakdown accounts for end-to-end latency (${llm_ok_n:-0}/${llm_all_n:-0} tenant(s) with a complete breakdown, within 0.1%, converged in ${l8_elapsed}s)" \
  || fail "L8 only ${llm_ok_n:-0} of ${llm_all_n:-0} tenant(s) with a complete breakdown account for llm:e2e:mean5m to within 0.1% after ${L8_POLL_SECONDS}s — all four recording rules applied, and none swapped back to a quantile? (means are additive, percentiles are not). ${llm_all_n:-0} = 0 means the four mean rules are not evaluating at all."

# L9. the four SLO ratios exist and are evaluating.
#
#     EXISTENCE, DELIBERATELY NOT A THRESHOLD. The obvious check here is "the
#     steady tenant is above the 99% objective", and it is the wrong one: five
#     minutes after install rate() is still under-reading through the documented
#     warm-up, so a 0.99 comparison passes most days and fails occasionally for
#     reasons that have nothing to do with the rules. A check that flakes gets
#     weakened rather than understood. L3b's bounds are loose for the same
#     reason. If a value assertion is wanted later, bound it far from the
#     threshold — the steady ratio is 1.0, so `> 0.5` catches a broken rule and
#     never flakes.
#
#     WHAT THIS CATCHES THAT NOTHING ELSE DOES: promtool proves the PromQL is
#     right over fixtures, and neither it nor --selftest leaves the repo. Only a
#     live Prometheus can show that `le="2.5"` matched a bucket that really is
#     exposed under that exact label string. A boundary that is not in
#     TTFT_BUCKETS, or a client that formats `le` differently, produces an EMPTY
#     ratio here while every promtool case still passes — which is exactly the
#     silent failure the rule file's ⚠️ is about.
#
#     All four `and`-ed, so a single missing window fails: the slow-burn alert
#     reads slo_ratio6h and slo_ratio30m, and those are the two least likely to
#     be noticed missing because nothing on this rig drives that alert.
llm_slo=0
deadline=$(( SECONDS + 180 ))
while :; do
  llm_slo="$(promql_count 'count(llm:ttft:slo_ratio5m) > 0
    and count(llm:ttft:slo_ratio30m) > 0
    and count(llm:ttft:slo_ratio1h) > 0
    and count(llm:ttft:slo_ratio6h) > 0')"
  [[ "${llm_slo:-0}" -gt 0 ]] && break
  (( SECONDS >= deadline )) && break
  sleep 5
done
llm_slo_n="$(promql_count 'llm:ttft:slo_ratio5m')"
[[ "${llm_slo:-0}" -gt 0 ]] && pass "L9 all four TTFT SLO ratios are recorded (${llm_slo_n:-0} tenant(s) on the 5m window)" \
  || fail "L9 one or more of llm:ttft:slo_ratio{5m,30m,1h,6h} is absent — is the current llm-prometheusrule.yaml applied, and does le=\"2.5\" match a bucket your exposition actually carries? A boundary not in TTFT_BUCKETS matches nothing and records an empty series while every promtool case still passes"

graf_pf_stop
prom_pf_stop

echo
[[ "$FAILED" -eq 0 ]] && { echo "ALL CHECKS PASSED"; exit 0; } || { echo "SOME CHECKS FAILED"; exit 1; }
