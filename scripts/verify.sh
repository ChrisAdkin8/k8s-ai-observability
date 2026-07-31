#!/usr/bin/env bash
# verify.sh <eks|gke|local> [--byo] — assert this repo's acceptance criteria: the GPU
# checks 1-5 (including 3b/4b/4c/4d) and the LLM checks L1-L7. Both sets of numbers are
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
PF_PID=""
prom_pf_up()   { [[ -n "$PF_PID" ]] && kill -0 "$PF_PID" 2>/dev/null; }
# `wait` after `kill` reaps the job synchronously. Without it the shell reports the
# reaped background job itself ("Terminated: 15") straight to the terminal — output we
# cannot redirect, and which reads like a failure in the middle of a passing run.
prom_pf_stop() {
  [[ -n "$PF_PID" ]] && { kill "$PF_PID" >/dev/null 2>&1 || true; wait "$PF_PID" 2>/dev/null || true; }
  PF_PID=""
}
prom_pf_ensure() {  # (re)establish the forward if it isn't alive — survives long polls
  prom_pf_up && return 0
  "${KUBECTL[@]}" -n "$MONITORING_NS" port-forward "svc/${KPS_RELEASE}-prometheus" 9090:9090 >/dev/null 2>&1 &
  PF_PID=$!; sleep 4
}
trap prom_pf_stop EXIT
# returns the number of result series for a query (ensures the forward first)
promql_count() {
  prom_pf_ensure
  curl -sG "http://localhost:9090/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("data",{}).get("result",[])))' 2>/dev/null || echo 0
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

# How long checks 3 and 4c wait for the FIRST DCGM scrape to land, in 5s attempts.
#
# ONE constant because those two must move together, and a comment saying so was not
# enough — 4c asserts a recording rule DERIVED from the metric 3 asserts, so if 3's
# window is the shorter of the two, a slow runner produces the contradictory result
# "the input is missing but the thing computed from it is present". That is confusing
# in exactly the wrong direction: it reads as a selector fault when it is a timing one.
#
# RAISED FROM 12 (60s) after a real CI failure on a docs-only commit. Check 3 gave up
# at 60s and DCGM_FI_DEV_GPU_TEMP — which cannot exist without DCGM_FI_DEV_GPU_UTIL —
# passed 13 seconds later. The run took 8m34s against a typical 5m, so the runner was
# slow rather than the stack broken. 60s was already a deliberate choice (see check 3)
# and simply had no margin on a bad day.
#
# Still bounded, and that is the point: a genuine ServiceMonitor selector mismatch
# never resolves, so this must fail rather than hang. 120s is ~8 scrape intervals at
# the 15s the ServiceMonitors set — generous for a first scrape, still quick to fail.
DCGM_POLL_ATTEMPTS=24

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
#    Shares DCGM_POLL_ATTEMPTS with 4c — see the constant for why they are one value and
#    why it was raised from 60s to 120s.
up=0; util=0
for _ in $(seq 1 "$DCGM_POLL_ATTEMPTS"); do
  up="$(promql_count 'up{job=~".*dcgm.*"} == 1')"
  util="$(promql_count 'DCGM_FI_DEV_GPU_UTIL')"
  [[ "${up:-0}" -gt 0 && "${util:-0}" -gt 0 ]] && break
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
  for _ in $(seq 1 "$DCGM_POLL_ATTEMPTS"); do
    n="$(promql_count "$m")"
    [[ "${n:-0}" -gt 0 ]] && break
    sleep 5
  done
  [[ "${n:-0}" -gt 0 ]] && pass "derived series $m returns $n series (recording rule live)" \
    || fail "$m empty after $((DCGM_POLL_ATTEMPTS * 5))s → dashboard temp/power panel blank (are the recording rules in manifests/alerts/ applied?)$(byo_hint)"
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
trap 'prom_pf_stop; graf_pf_stop' EXIT

# Shared by every dashboard check: fetch a board by UID over an UNAUTHENTICATED
# request, and translate the HTTP status into an actionable message. One
# implementation so the GPU and LLM boards can never be checked differently.
grafana_uid_check() {
  local uid="$1" label="$2" url="$3" code=000
  for _ in $(seq 1 20); do
    graf_pf_ensure                    # rebuilds the forward if it idled out
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${GRAFANA_PORT}/api/dashboards/uid/${uid}" || echo 000)"
    [[ "$code" == "200" ]] && break
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
#     DELIBERATELY NOT on DCGM_POLL_ATTEMPTS, though it polls the same metric. By the
#     time this runs, check 3 has already established that DCGM_FI_DEV_GPU_UTIL exists,
#     so this 60s is measuring something else entirely: whether the ANNOTATION is
#     reaching the exporter. Sharing the constant would tie an annotation-propagation
#     budget to a first-scrape one and make both harder to reason about.
util_hi=0
for _ in $(seq 1 12); do
  util_hi="$(promql_count 'max(DCGM_FI_DEV_GPU_UTIL) > 80')"
  [[ "${util_hi:-0}" -gt 0 ]] && break
  sleep 5
done
[[ "${util_hi:-0}" -gt 0 ]] && pass "simulated utilisation is being driven (max DCGM_FI_DEV_GPU_UTIL > 80)" \
  || fail "no GPU above 80% util after 60s — the 'gpu-busy' annotation is not reaching the exporter. Check that run.ai/simulated-gpu-utilization sits on spec.template.metadata.annotations, not the Deployment's metadata."

# 5. THE utilisation alert can reach 'firing' (poll, waiting out the rule's for: duration).
#    Named exactly, NOT alertname=~".*GPU.*": GPUHighMemoryUsage is allocation-driven, so
#    it fires permanently for every GPU that has a pod on it (a busy GPU reports
#    FB_USED=all/FB_FREE=0 — see docs/observability.md). A wildcard here is satisfied by
#    that alert alone and would pass with utilisation stuck at zero, which is the one
#    thing this check exists to prove.
echo "  (polling up to 6m for GPUHighUtilization to fire...)"
fired=0
for _ in $(seq 1 36); do
  n="$(promql_count 'ALERTS{alertstate="firing", alertname="GPUHighUtilization"}')"
  if [[ "${n:-0}" -gt 0 ]]; then fired=1; break; fi
  sleep 10
done
[[ "$fired" -eq 1 ]] && pass "GPUHighUtilization is firing" \
  || fail "GPUHighUtilization did not fire within timeout (check the 'gpu-busy' workload util range 85-99 vs the rule's >80 threshold and 1m for:)"

# ============================================================================
# LLM simulation checks — numbered L1..L7 so they can never be confused with
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
for _ in $(seq 1 24); do   # recording rules need a couple of evaluations to exist
  llm_p95="$(promql_count "llm:ttft:p95_5m{model_name=\"${LLM_STEADY_MODEL}\"} > 0 and llm:ttft:p95_5m{model_name=\"${LLM_STEADY_MODEL}\"} < 2")"
  [[ "${llm_p95:-0}" -gt 0 ]] && break
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
llm_bind="$(promql_count 'llmsim_gpu_binding_info')"
if [[ "${llm_bind:-0}" -gt 0 ]]; then
  llm_join="$(promql_count 'llmsim_gpu_binding_info * on (namespace, pod) group_left(UUID, gpu) label_replace(label_replace(DCGM_FI_DEV_GPU_UTIL{exported_pod!=""}, "namespace", "$1", "exported_namespace", "(.*)"), "pod", "$1", "exported_pod", "(.*)")')"
  [[ "${llm_join:-0}" -gt 0 ]] && pass "L4b GPU binding resolves to a real DCGM series (joined on pod)" \
    || fail "L4b llmsim_gpu_binding_info exists but no DCGM_FI_DEV_GPU_UTIL series is labelled with that pod"
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
echo "  (polling up to 5m for LLMHighTTFT to fire...)"
llm_fired=0
for _ in $(seq 1 30); do
  n="$(promql_count 'ALERTS{alertstate="firing", alertname="LLMHighTTFT"}')"
  if [[ "${n:-0}" -gt 0 ]]; then llm_fired=1; break; fi
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

graf_pf_stop
prom_pf_stop

echo
[[ "$FAILED" -eq 0 ]] && { echo "ALL CHECKS PASSED"; exit 0; } || { echo "SOME CHECKS FAILED"; exit 1; }
