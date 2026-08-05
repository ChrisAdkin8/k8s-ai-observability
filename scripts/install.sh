#!/usr/bin/env bash
# install.sh <eks|gke|local> [--skip-monitoring] — Phase 2: deploy observability +
# fake GPU stacks.
# Assumes Phase 1 has created the cluster with the GPU-sim node label — `terraform apply`
# on the clouds, ./scripts/kind-up.sh on local. Phase 2 itself is cloud-agnostic: the only
# thing that differs per target is how the kubecontext is obtained.
#
# --skip-monitoring: the cluster ALREADY HAS Prometheus. Step [2/5] is skipped and
# everything else proceeds unchanged. Two things then become the caller's problem, and
# both fail silently if wrong — see RELEASE_LABEL and GRAFANA_DASHBOARD_LABEL in
# config.sh:
#
#   KPS_RELEASE=my-monitoring ./scripts/install.sh local --skip-monitoring
#
# That one variable covers the selector label (it defaults from KPS_RELEASE) and the
# Service names grafana.sh and prometheus.sh port-forward to.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

TARGET="${1:?usage: install.sh <eks|gke|local> [--skip-monitoring]}"
# Positional, matching teardown.sh's `--destroy` rather than introducing getopts into
# one script and leaving the other inconsistent. teardown.sh rejects unknown arguments
# the same way, for the same reason.
#
# ⚠️ EVERY ARGUMENT IS CHECKED, NOT JUST $2, AND THAT USED TO BE FALSE. This was a
# `case "${2:-}"` and nothing else, so `install.sh local --skip-monitoring --lite`
# accepted `--lite` in silence. `--lite` is the realistic typo, because LITE is an
# environment variable and not a flag: the user gets the FULL stack, on a machine
# they sized for the trimmed one, with nothing said. A typo'd flag must fail loudly
# rather than silently do the non-flag thing.
SKIP_MONITORING=0
shift || true                       # $1 is the target, already consumed above
for arg in "$@"; do
  case "$arg" in
    --skip-monitoring)  SKIP_MONITORING=1 ;;
    *) echo "ERROR: unknown argument '$arg'" >&2
       echo "usage: install.sh <eks|gke|local> [--skip-monitoring]" >&2
       echo "       LITE is an ENVIRONMENT VARIABLE, not a flag:  LITE=1 $0 $TARGET" >&2
       exit 1 ;;
  esac
done

# python3 is listed explicitly because this script genuinely needs it — the dashboard
# JSON check in assert_dashboard_contract and the simulator checksum in [5/5] both
# shell out to it. Without it here, a missing python3 surfaces as
# "<board> is not valid JSON", which sends you to inspect a file that is fine.
case "$TARGET" in
  eks)   require_tools terraform aws python3 ;;
  gke)   require_tools terraform gcloud python3 ;;
  local) require_tools kind python3 ;;
  *)     echo "target must be eks|gke|local" >&2; exit 1 ;;
esac

# ⚠️ LITE has nothing to trim when there is no stack to trim it from. It is an
# OVERLAY on helm/kube-prometheus-stack/values.yaml (config.sh KPS_VALUES), and the
# only consumer of that array is the Helm install being skipped — so under
# --skip-monitoring it does precisely nothing.
#
# Said out loud rather than documented quietly, because a flag that silently does
# nothing is the failure mode this repo writes assertions against. Not fatal: the two
# are a redundant combination, not a contradictory one, and refusing would break
# anyone who exports LITE=1 in their shell for the local target.
if [[ "$SKIP_MONITORING" == "1" && "$LITE" == "1" ]]; then
  echo "NOTE: LITE=1 is IGNORED under --skip-monitoring." >&2
  echo "      It only trims the kube-prometheus-stack values, and that install is" >&2
  echo "      being skipped. The size of the monitoring stack on this cluster is" >&2
  echo "      whoever installed it's decision, not this script's." >&2
fi

assert_manifest_namespaces   # fail loudly if config.sh drifted from the static manifests
assert_gpu_contract          # ...and if the nodePool name/label drifted from values.yaml
assert_dashboard_contract    # ...and if either dashboard's name/uid drifted (the /d/<uid> links)
assert_llm_contract          # ...and if the LLM model_name identities drifted or collided
assert_terraform_contract "$TARGET"   # ...and if config.sh drifted from what Terraform built
# local has no Terraform, so the line above no-ops there; kind/gpu-sim.yaml carries the
# same invariants and this is what cross-checks them.
if [[ "$TARGET" == "local" ]]; then assert_kind_contract; fi
CTX="$(ensure_context "$TARGET")"
KUBECTL=(kubectl --context "$CTX")
HELM=(helm --kube-context "$CTX")

# The one assertion that needs a live cluster, hence its position rather than beside
# the others above. Still runs before anything is created.
if [[ "$SKIP_MONITORING" == "1" ]]; then assert_monitoring_crds "$CTX"; fi

# Apply a manifest with the `release:` selector rewritten to $RELEASE_LABEL.
#
# The files carry `release: kube-prometheus-stack` statically, which is right for this
# repo's own install and wrong for a BYO cluster whose release is named anything else.
# --local rewrites it on the way past without a templating engine, exactly as the
# dashboard ConfigMaps are labelled below. --overwrite because the label is already
# there; without it `kubectl label` refuses rather than replacing.
apply_with_release_label() {
  local f
  for f in "$@"; do
    "${KUBECTL[@]}" label --local -f "$f" --overwrite "release=$RELEASE_LABEL" -o yaml \
      | "${KUBECTL[@]}" apply -f -
  done
}

# --- the early apply, and the three minutes it exists to save -------------------
#
# ⚠️ THE ServiceMonitors AND RULES ARE APPLIED TWICE, ON PURPOSE. This one races the
# Helm install; [2b] below applies the same files again, unconditionally. The second
# apply is not a leftover — it is what makes this one safe to lose.
#
# WHAT IT COSTS TO APPLY THEM LATE. Measured on a fresh kind cluster, 2026-08-05:
# [2b] applied them at 08:55:21, Prometheus recorded its next successful config
# reload at 08:58:25, and the first nvidia-dcgm-exporter sample landed at 08:58:31 —
# 184s after the objects existed. The chart's OWN scrape targets were all being
# collected by 08:55:46. verify.sh check 3 therefore sat in its first-scrape poll for
# 126.5s of a 288s run, waiting for a target whose ServiceMonitor had been applied
# before the check even started.
#
# THE CAUSE IS THE RELOADER, NOT THE OPERATOR. prometheus-operator regenerates the
# config Secret in seconds. The config-reloader sidecar then has to notice: its
# `--watch-interval` defaults to **3m0s** (read from `prometheus-config-reloader
# --help` inside the running pod) and the inotify path does not fire for the gzipped
# Secret volume, so an object applied after Prometheus is running waits for the next
# poll. Confirmed rather than inferred: a throwaway ServiceMonitor applied to a
# settled cluster took 70s to reach the scrape config — a different draw from the
# same 3-minute cycle. Expect uniform 0-180s, mean ~90s, every cold install.
#
# An object that exists BEFORE the operator writes the first config needs no reload
# at all. The CRDs are Established within seconds of `helm upgrade` starting, while
# the Prometheus pod did not start until 175s into the same run (it is behind a
# 352 MB Grafana pull and friends), so the window is wide — but it IS a race, and
# losing it must not matter.
#
# ⚠️ SO EVERY FAILURE HERE IS REPORTED AND NONE IS FATAL. If the CRDs never arrive,
# if the webhook is not serving yet, if the apply fails for any reason at all, [2b]
# still applies the same files and the install is exactly what it was before this
# function existed: slower, never broken. A silent skip would be the failure mode
# this repo writes assertions against, hence the messages.
#
# The rules go in the same pass as the ServiceMonitors because they ride the same
# reload: the operator renders PrometheusRules into the rulefile ConfigMaps the
# reloader watches as directories, on the same 3-minute poll. Fixing only the
# scrapes would just move the wait to verify.sh check 4c, which asserts a series
# those rules derive.
SM_EARLY_POLL_SECONDS=120   # wall-clock, like every poll in verify.sh
apply_observability_objects_early() {
  local deadline=$(( SECONDS + SM_EARLY_POLL_SECONDS )) out=""
  local sm_crd="servicemonitors.monitoring.coreos.com"
  local rule_crd="prometheusrules.monitoring.coreos.com"
  # Read into variables rather than piped into grep: this file runs under pipefail,
  # and a `kubectl ... | grep -q` on a one-word output is the SIGPIPE shape rule 17
  # is about. There is nothing to gain by piping a single field.
  local sm_est rule_est ns
  while :; do
    sm_est="$("${KUBECTL[@]}" get crd "$sm_crd" \
      -o jsonpath='{.status.conditions[?(@.type=="Established")].status}' 2>/dev/null || true)"
    rule_est="$("${KUBECTL[@]}" get crd "$rule_crd" \
      -o jsonpath='{.status.conditions[?(@.type=="Established")].status}' 2>/dev/null || true)"
    ns="$("${KUBECTL[@]}" get ns "$MONITORING_NS" -o name 2>/dev/null || true)"
    if [[ "$sm_est" == "True" && "$rule_est" == "True" && -n "$ns" ]]; then
      # The CRDs exist; the PrometheusRule validating webhook may not be serving
      # yet, and that is a transient rejection rather than a fault — retry until
      # the deadline. `apply` is idempotent, so re-applying the ServiceMonitors
      # while waiting for the webhook costs nothing.
      if out="$(apply_with_release_label manifests/servicemonitor/*.yaml manifests/alerts/*.yaml 2>&1)"; then
        echo "    ServiceMonitors + rules applied before Prometheus started"
        echo "    (they land in its FIRST config — no 3m config-reloader poll to wait out)"
        return 0
      fi
    fi
    if (( SECONDS >= deadline )); then
      echo "    (could not apply them early within ${SM_EARLY_POLL_SECONDS}s — [2b] will apply them"
      echo "     as usual. Nothing is broken; expect up to 3m more before the first scrape.)"
      # Last line via parameter expansion rather than `| tail -1`: command
      # substitution has already stripped the trailing newline, and this file runs
      # under pipefail (rule 17).
      [[ -n "$out" ]] && echo "     last error: ${out##*$'\n'}"
      return 0
    fi
    sleep 3
  done
}

echo "==> [1/5] Helm repos"
"${HELM[@]}" repo add prometheus-community "$KPS_REPO" >/dev/null 2>&1 || true
"${HELM[@]}" repo add fake-gpu-operator "$FAKE_GPU_REPO" >/dev/null 2>&1 || true
"${HELM[@]}" repo update >/dev/null

if [[ "$SKIP_MONITORING" == "1" ]]; then
  echo "==> [2/5] kube-prometheus-stack — SKIPPED (--skip-monitoring)"
  echo "    Using the monitoring stack already on this cluster."
  echo "    release '$KPS_RELEASE' · rule/ServiceMonitor selector label 'release=$RELEASE_LABEL'"
  echo "    dashboards labelled '${GRAFANA_DASHBOARD_LABEL}=${GRAFANA_DASHBOARD_LABEL_VALUE}'"
  echo "    ⚠️ If either label is wrong there is no error — the rules never evaluate,"
  echo "       the scrapes never happen and the boards stay empty. ./scripts/verify.sh"
  echo "       $TARGET --byo is what tells you."
else
  echo "==> [2/5] kube-prometheus-stack (first, so ServiceMonitor/PrometheusRule CRDs + admission webhook exist)"
  [[ "$LITE" == "1" ]] && echo "    LITE=1 — trimmed stack, see helm/kube-prometheus-stack/values-lite.yaml"
  # KPS_VALUES is built in config.sh so kind-up.sh's sizing floor and this values
  # stack can never disagree about which profile is being installed.
  #
  # ⚠️ BACKGROUNDED SO THE APPLY ABOVE CAN RACE IT — see
  # apply_observability_objects_early for the 184s that buys, and for why losing
  # the race is harmless. `--wait` still blocks the install: `wait` below is where
  # this branch actually finishes, and its exit status is still the helm one.
  #
  # No trap is needed to clean this up on Ctrl-C. Job control is off in a
  # non-interactive shell, so the background helm shares this script's process
  # group and receives the terminal's SIGINT along with it.
  "${HELM[@]}" upgrade --install "$KPS_RELEASE" "$KPS_CHART" \
    --version "$KPS_CHART_VERSION" \
    --namespace "$MONITORING_NS" --create-namespace \
    "${KPS_VALUES[@]}" \
    --wait --timeout 15m &
  kps_pid=$!
  apply_observability_objects_early
  # `if !` rather than a bare `wait`, so a failed install reports as one thing
  # instead of as `set -e` killing the script at a line that reads like a no-op.
  if ! wait "$kps_pid"; then
    echo "ERROR: the kube-prometheus-stack install failed (helm output above)." >&2
    exit 1
  fi
  # Readiness (not just ordering): the PrometheusRule validating webhook must be serving
  # before we apply custom rules. `helm --wait` above already blocks until the operator +
  # its webhook are Ready, so this is a redundant, NON-FATAL belt-and-braces check
  # (kept resilient to a renamed release — a wrong name must not abort a healthy install).
  # ⚠️ RESOLVED, NOT CONSTRUCTED. "${KPS_RELEASE}-operator" only exists when the
  # release name contains the chart name. This branch is the greenfield install,
  # so the default release makes it correct — but set KPS_RELEASE=my-monitoring
  # WITHOUT --skip-monitoring and the Deployment is
  # my-monitoring-kube-prometheus-s-operator, the lookup matched nothing, and
  # `|| true` swallowed it: the wait silently did not happen.
  if op="$(resolve_kps deploy operator)"; then
    "${KUBECTL[@]}" -n "$MONITORING_NS" rollout status "deploy/${op}" --timeout=5m || true
  else
    echo "    (no operator Deployment found under release '$KPS_RELEASE' — skipping the redundant wait)"
  fi
fi

echo "==> [2b] apply dashboards + ServiceMonitors + alert rules (CRDs present & webhook ready)"
# Dashboards are built from the .json files rather than shipped as hand-maintained
# ConfigMaps, exactly as the llm-sim script ConfigMap is built from scripts/llm-sim.py
# further down. The board is then ONE artefact: the file Grafana provisioning mounts in
# the compose path, the file you upload to grafana.com, and the file this ConfigMap
# wraps — so they cannot disagree. `create --dry-run | apply` keeps it idempotent.
#
# Two labels, for two different consumers:
#   grafana_dashboard=1                        — what the kube-prometheus-stack sidecar
#     selects on. Without it the ConfigMap is created and silently never imported.
#   app.kubernetes.io/part-of=gpu-sim-dashboards — ownership, so teardown.sh can remove
#     OUR boards without touching the several the chart ships under the same sidecar
#     label. Deleting by grafana_dashboard=1 alone would take the chart's with them.
for board in manifests/dashboards/*.json; do
  cm="$(dashboard_configmap_name "$board")"
  "${KUBECTL[@]}" -n "$MONITORING_NS" create configmap "$cm" \
    --from-file="$(basename "$board")=$board" \
    --dry-run=client -o yaml \
    | "${KUBECTL[@]}" label --local -f - \
        "${GRAFANA_DASHBOARD_LABEL}=${GRAFANA_DASHBOARD_LABEL_VALUE}" \
        "$DASHBOARD_OWNER_LABEL" -o yaml \
    | "${KUBECTL[@]}" apply -f -
done
# Relabelled on the way past rather than applied as-is: the `release:` selector in
# these four files is right for this repo's own install and wrong for any other, and
# on a BYO cluster a wrong one is silent — see RELEASE_LABEL in config.sh.
#
# ⚠️ THE SECOND APPLY, AND IT STAYS UNCONDITIONAL. On the greenfield path
# apply_observability_objects_early has usually applied these already and this is a
# no-op; under --skip-monitoring, or whenever that race is lost, this is the only
# apply there is. Making it conditional on the early one having succeeded would
# trade a free re-apply for a way to install nothing at all.
apply_with_release_label manifests/servicemonitor/*.yaml manifests/alerts/*.yaml

echo "==> [3/5] fake-gpu-operator"
fgo_version=()
[[ -n "$FAKE_GPU_CHART_VERSION" ]] && fgo_version=(--version "$FAKE_GPU_CHART_VERSION")
# ${arr[@]+"${arr[@]}"}: bash 3.2 (macOS default) treats an empty array as unbound
# under `set -u`; the +alternate form expands to nothing instead of aborting.
"${HELM[@]}" upgrade --install "$FAKE_GPU_RELEASE" "$FAKE_GPU_CHART" \
  ${fgo_version[@]+"${fgo_version[@]}"} \
  --namespace "$FAKE_GPU_NS" --create-namespace \
  -f helm/fake-gpu-operator/values.yaml \
  --set-string topology.nodePoolLabelKey="$NODE_POOL_LABEL_KEY" \
  --wait --timeout 15m
# There is NO mutating webhook here, despite the widespread assumption that there is.
# `kubectl get mutatingwebhookconfigurations` shows no run.ai/gpu entry at all: the fake
# nvidia-smi and topology are injected by the DEVICE PLUGIN's Allocate() response, the
# same mechanism the real NVIDIA plugin uses. kubelet applies it at container-create
# time, so the injected bits never appear in the pod spec.
#
# That makes the DaemonSets the thing to wait on, not the Deployments: waiting on
# `deploy --all` here was a no-op, because the two Deployments in this namespace
# (gpu-operator, nvidia-dcgm-exporter) are permanent 0/0 placeholders and are therefore
# trivially Available. What actually has to be up before GPU pods can schedule is the
# device plugin, so that kubelet advertises nvidia.com/gpu.
#
# Non-fatal: a GPU pod created too early is merely Pending until capacity appears, which
# self-heals. mig-faker legitimately has 0 desired (its node selector matches nothing on
# this rig), and rollout status treats that as complete.
for ds in $("${KUBECTL[@]}" -n "$FAKE_GPU_NS" get ds -o name 2>/dev/null); do
  "${KUBECTL[@]}" -n "$FAKE_GPU_NS" rollout status "$ds" --timeout=5m || true
done

echo "==> [4/5] sample GPU workloads (only after the device plugin is advertising GPUs)"
"${KUBECTL[@]}" apply -f manifests/workloads/

echo "==> [5/5] simulated LLM serving stack"
# The simulator is a normal, readable, locally-runnable Python file rather than a
# few hundred lines of indented YAML, so the ConfigMap is built from it here.
# `create --dry-run | apply` keeps this idempotent on re-install.
"${KUBECTL[@]}" apply -f manifests/llm/00-namespace.yaml
"${KUBECTL[@]}" -n "$LLM_NS" create configmap llm-sim-script \
  --from-file=llm_sim.py=scripts/llm-sim.py \
  --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
# Applied AFTER the GPU stack so llm-steady's optional nvidia.com/gpu request is
# satisfiable. If no simulated GPU is free it still runs, just unbound.
"${KUBECTL[@]}" apply -f manifests/llm/

# ⚠️ REBUILDING THE ConfigMap ABOVE IS NOT ENOUGH TO GET THE NEW SCRIPT RUNNING.
#
# A running pod keeps serving the code it started with: kubelet does eventually sync
# the projected volume, but the Python process read llm_sim.py once at exec time and
# never looks again. Nothing in the Deployment changes when only the ConfigMap's
# contents do, so `kubectl apply` reports everything "unchanged" and no rollout
# happens. The install goes green and the cluster keeps emitting the OLD metric
# surface — which is the silent-success failure this repo writes assertions against,
# and it is invisible on a FRESH install because there the first pods already have
# the current file. That is why CI never caught it: CI always builds a new cluster.
#
# It cost a real diagnosis: after the prefix-cache series landed, verify.sh's L7
# failed on an existing cluster with both counters absent while the ConfigMap plainly
# contained them.
#
# The fix is the standard checksum-annotation trick, and its important property is
# that it is a NO-OP when nothing changed: the pod template only differs when the
# hash does, so a re-install with an unmodified script rolls nothing. An
# unconditional `rollout restart` here would churn the tenants on every install and
# reset the queue the saturated profile spends minutes building — which briefly
# breaks the very checks this is meant to keep passing.
#
# Discovered by what they MOUNT rather than by name, so the opt-in llm-driven
# Deployment in manifests/llm/extras/ is covered too. Hardcoding llm-steady and
# llm-saturated would leave anyone using the extras with exactly this bug, still
# silent.
script_sha="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()[:16])' scripts/llm-sim.py)"
llm_deploys="$("${KUBECTL[@]}" -n "$LLM_NS" get deploy -o json 2>/dev/null | python3 -c '
import json, sys
try:
    items = json.load(sys.stdin).get("items", [])
except (ValueError, OSError):
    sys.exit(0)
for d in items:
    vols = d["spec"]["template"]["spec"].get("volumes") or []
    if any((v.get("configMap") or {}).get("name") == "llm-sim-script" for v in vols):
        print(d["metadata"]["name"])
' || true)"

for d in $llm_deploys; do
  "${KUBECTL[@]}" -n "$LLM_NS" patch deploy "$d" --type=merge \
    -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"k8s-ai-observability/llm-sim-sha256\":\"$script_sha\"}}}}}" \
    >/dev/null
done
[[ -n "$llm_deploys" ]] && echo "    simulator script sha256=$script_sha (pods roll only if this moved)"

# Waits on the same discovered list, so an extras tenant is not silently skipped.
# Non-fatal as before: a simulator that cannot schedule is verify.sh's to report.
for d in $llm_deploys; do
  "${KUBECTL[@]}" -n "$LLM_NS" rollout status "deploy/$d" --timeout=3m || true
done

# The admin Secret shares the Grafana Service's name, and neither is predictable
# from KPS_RELEASE. Resolved so the command printed below can be pasted; falls
# back to the constructed name only for the message, since a summary line must
# never abort a successful install.
graf_secret="$(resolve_kps svc grafana || echo "${KPS_RELEASE}-grafana")"

byo_env=""; byo_verify=""
if [[ "$SKIP_MONITORING" == "1" ]]; then
  # Echo the release back on every BYO line. The scripts read it from the
  # environment, so a user who set it for install.sh and not for grafana.sh gets a
  # port-forward to a Service that does not exist — the last mile of exactly the
  # journey this flag exists to serve.
  byo_env="KPS_RELEASE=$KPS_RELEASE "
  byo_verify=" --byo"
fi

cat <<EOF

Done. Two dashboards (Grafana stays private — the script holds a port-forward):

  ${byo_env}./scripts/grafana.sh $TARGET

  GPU:  $(grafana_dashboard_url)
  LLM:  $(grafana_llm_dashboard_url)

  Anonymous Viewer access — no login needed to view. To edit, log in as 'admin':
  kubectl --context $CTX -n $MONITORING_NS get secret ${graf_secret} \\
    -o jsonpath='{.data.admin-password}' | base64 -d; echo
  (Anonymous access and that secret are both kube-prometheus-stack conventions —
   on a monitoring stack this repo did not install, yours may differ.)

Verify the whole stack:
  ${byo_env}./scripts/verify.sh $TARGET${byo_verify}

Drive a moving LLM load curve (opt-in extras first):
  kubectl apply -f manifests/llm/extras/
  ./scripts/drive-llm-load.sh ramp
EOF
