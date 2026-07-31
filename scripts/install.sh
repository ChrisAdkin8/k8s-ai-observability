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
# one script and leaving the other inconsistent.
#
# UNLIKE teardown.sh, an unrecognised value is rejected. teardown.sh compares $2
# against the literal and ignores anything else, which means a typo'd flag silently
# does the non-flag thing — and here that would install a second monitoring stack over
# the top of the user's own, which is the exact outcome this flag exists to prevent.
SKIP_MONITORING=0
case "${2:-}" in
  "")                 ;;
  --skip-monitoring)  SKIP_MONITORING=1 ;;
  *) echo "ERROR: unknown argument '${2}'" >&2
     echo "usage: install.sh <eks|gke|local> [--skip-monitoring]" >&2
     exit 1 ;;
esac

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
  "${HELM[@]}" upgrade --install "$KPS_RELEASE" "$KPS_CHART" \
    --version "$KPS_CHART_VERSION" \
    --namespace "$MONITORING_NS" --create-namespace \
    "${KPS_VALUES[@]}" \
    --wait --timeout 15m
  # Readiness (not just ordering): the PrometheusRule validating webhook must be serving
  # before we apply custom rules. `helm --wait` above already blocks until the operator +
  # its webhook are Ready, so this is a redundant, NON-FATAL belt-and-braces check
  # (kept resilient to a renamed release — a wrong name must not abort a healthy install).
  "${KUBECTL[@]}" -n "$MONITORING_NS" rollout status "deploy/${KPS_RELEASE}-operator" --timeout=5m || true
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
  kubectl --context $CTX -n $MONITORING_NS get secret ${KPS_RELEASE}-grafana \\
    -o jsonpath='{.data.admin-password}' | base64 -d; echo
  (Anonymous access and that secret are both kube-prometheus-stack conventions —
   on a monitoring stack this repo did not install, yours may differ.)

Verify the whole stack:
  ${byo_env}./scripts/verify.sh $TARGET${byo_verify}

Drive a moving LLM load curve (opt-in extras first):
  kubectl apply -f manifests/llm/extras/
  ./scripts/drive-llm-load.sh ramp
EOF
