#!/usr/bin/env bash
# install.sh <eks|gke|local> — Phase 2: deploy observability + fake GPU stacks.
# Assumes Phase 1 has created the cluster with the GPU-sim node label — `terraform apply`
# on the clouds, ./scripts/kind-up.sh on local. Phase 2 itself is cloud-agnostic: the only
# thing that differs per target is how the kubecontext is obtained.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/config.sh

TARGET="${1:?usage: install.sh <eks|gke|local>}"
case "$TARGET" in
  eks)   require_tools terraform aws ;;
  gke)   require_tools terraform gcloud ;;
  local) require_tools kind ;;
  *)     echo "target must be eks|gke|local" >&2; exit 1 ;;
esac

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

echo "==> [1/5] Helm repos"
"${HELM[@]}" repo add prometheus-community "$KPS_REPO" >/dev/null 2>&1 || true
"${HELM[@]}" repo add fake-gpu-operator "$FAKE_GPU_REPO" >/dev/null 2>&1 || true
"${HELM[@]}" repo update >/dev/null

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
        grafana_dashboard=1 app.kubernetes.io/part-of=gpu-sim-dashboards -o yaml \
    | "${KUBECTL[@]}" apply -f -
done
"${KUBECTL[@]}" apply -f manifests/servicemonitor/
"${KUBECTL[@]}" apply -f manifests/alerts/

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
"${KUBECTL[@]}" -n "$LLM_NS" rollout status deploy/llm-steady --timeout=3m || true
"${KUBECTL[@]}" -n "$LLM_NS" rollout status deploy/llm-saturated --timeout=3m || true

cat <<EOF

Done. Two dashboards (Grafana stays private — the script holds a port-forward):

  ./scripts/grafana.sh $TARGET

  GPU:  $(grafana_dashboard_url)
  LLM:  $(grafana_llm_dashboard_url)

  Anonymous Viewer access — no login needed to view. To edit, log in as 'admin':
  kubectl --context $CTX -n $MONITORING_NS get secret ${KPS_RELEASE}-grafana \\
    -o jsonpath='{.data.admin-password}' | base64 -d; echo

Verify the whole stack:
  ./scripts/verify.sh $TARGET

Drive a moving LLM load curve (opt-in extras first):
  kubectl apply -f manifests/llm/extras/
  ./scripts/drive-llm-load.sh ramp
EOF
