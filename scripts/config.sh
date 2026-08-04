#!/usr/bin/env bash
# config.sh — SINGLE SOURCE OF TRUTH for runtime constants + shared helpers.
# Sourced by install.sh / teardown.sh / verify.sh. Do not run directly.
#
# The "three-way naming invariant" lives here and in Terraform:
#   NODE_POOL_LABEL_KEY / NODE_POOL_NAME  (set on nodes by Terraform)
#     MUST equal
#   helm/fake-gpu-operator/values.yaml -> topology.nodePoolLabelKey + nodePools.<name>
# If these drift you get a green install with ZERO GPUs.

# ---- cluster / k8s -----------------------------------------------------------
CLUSTER_NAME="gpu-sim"
# Floor is >=1.31, but the fake operator stays on the device-plugin path either way (DRA
# not enabled). The CEILING is what moves: this must be a minor GKE still offers as a
# static version and that has a kindest/node tag for the local kind binary — the reasoning
# and how to re-check live in terraform/modules/contract/variables.tf. Verified 2026-07.
K8S_VERSION="1.36"

# ---- namespaces / releases ---------------------------------------------------
MONITORING_NS="monitoring"
FAKE_GPU_NS="gpu-operator"   # fake operator lives here to stand in for the real GPU operator
LLM_NS="llm-sim"             # simulated vLLM serving stack
FAKE_GPU_RELEASE="gpu-operator"

# ---- the monitoring stack: OURS, or someone else's ---------------------------
# OVERRIDABLE FROM THE ENVIRONMENT, and that is the whole BYO story in one line.
# All four scripts that touch the monitoring release source this file, so
#
#     KPS_RELEASE=my-monitoring ./scripts/grafana.sh local
#
# reaches install.sh, verify.sh, grafana.sh and prometheus.sh at once. Before this
# was a :- default it was a plain assignment, so the environment was silently
# overwritten at source time and every one of those scripts port-forwarded to a
# Service that does not exist on a cluster whose release is named anything else.
#
# ⚠️ THE SERVICE NAMES ARE NOT BUILT FROM IT ANY MORE, AND THE OBVIOUS
# CONSTRUCTION WORKED ONLY FOR THE ONE RELEASE NAME THAT NEVER NEEDED IT.
#
# This used to be "${KPS_RELEASE}-prometheus", described as a chart convention.
# It is half of one. Helm's fullname template collapses the prefix when the
# release name ALREADY CONTAINS the chart name:
#
#   release kube-prometheus-stack  ->  svc kube-prometheus-stack-prometheus
#   release acme-mon               ->  svc acme-mon-kube-prometheus-s-prometheus
#
# So the construction resolves for the greenfield release this repo installs
# itself, and for nothing else — including every release name KPS_RELEASE exists
# to support. Verified both ways by rendering the upstream chart under each name.
#
# ⚠️ AND IT FAILED AS A DIFFERENT BUG. Found 2026-08-04 by running the BYO path
# with release `acme-mon`: verify.sh port-forwarded a Service that does not
# exist, so every PromQL query returned nothing, and every series check reported
# itself as a SELECTOR problem — pointing at RELEASE_LABEL, which was correct.
# The suggested fix would not have helped, because the diagnosis was wrong.
#
# Grafana is NOT affected: the grafana subchart's own fullname gives
# "<release>-grafana" either way. It is resolved the same way regardless, so the
# two cannot quietly diverge again.
KPS_RELEASE="${KPS_RELEASE:-kube-prometheus-stack}"

# Ask the cluster what a kube-prometheus-stack component is called, rather than
# predicting it. Echoes a bare resource name and returns 0, or returns 1.
#
#   $1  resource kind      svc | deploy
#   $2  component          prometheus | alertmanager | operator | grafana
#
# Requires KUBECTL and MONITORING_NS, which every caller sets before use.
resolve_kps() {
  local kind="$1" component="$2" name

  # 1. The constructed name, which IS correct when the release name contains the
  #    chart name. Tried first so the greenfield path costs a single lookup and
  #    behaves exactly as it always has.
  if "${KUBECTL[@]}" -n "$MONITORING_NS" get "$kind" "${KPS_RELEASE}-${component}" \
       -o name >/dev/null 2>&1; then
    printf '%s\n' "${KPS_RELEASE}-${component}"; return 0
  fi

  # 2. kube-prometheus-stack's own labels. `app` is built from the CHART name, so
  #    it is identical under every release name — which is exactly the property
  #    the resource name lacks. Narrowed by `release` so a cluster running two
  #    monitoring stacks cannot answer for the wrong one.
  name=$("${KUBECTL[@]}" -n "$MONITORING_NS" get "$kind" \
           -l "app=kube-prometheus-stack-${component},release=${KPS_RELEASE}" \
           -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || name=""
  [ -n "$name" ] && { printf '%s\n' "$name"; return 0; }

  # 3. The SUBCHARTS use the standard Kubernetes labels instead, and carry no
  #    `release` label at all — grafana is labelled app.kubernetes.io/name=grafana
  #    with app.kubernetes.io/instance=<release>. Verified on a live cluster.
  name=$("${KUBECTL[@]}" -n "$MONITORING_NS" get "$kind" \
           -l "app.kubernetes.io/name=${component},app.kubernetes.io/instance=${KPS_RELEASE}" \
           -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || name=""
  [ -n "$name" ] && { printf '%s\n' "$name"; return 0; }

  # 4. The Prometheus Operator's OWN Service, created for every Prometheus and
  #    Alertmanager CR regardless of how they were installed — so this works even
  #    for a Prometheus that never came from this chart. Last, not first: it is
  #    headless and namespace-wide, so it answers for ALL of them and cannot tell
  #    two apart. A fallback, not a preference.
  if [ "$kind" = "svc" ]; then
    case "$component" in
      prometheus|alertmanager)
        if "${KUBECTL[@]}" -n "$MONITORING_NS" get svc "${component}-operated" \
             -o name >/dev/null 2>&1; then
          printf '%s\n' "${component}-operated"; return 0
        fi ;;
    esac
  fi
  return 1
}

# As above, but a miss is fatal and says what was looked for. A port-forward to a
# Service that does not exist fails in a way that reads as an empty Prometheus,
# which is the misdiagnosis this whole block exists to end.
resolve_kps_or_die() {
  local kind="$1" component="$2" name
  if name=$(resolve_kps "$kind" "$component"); then printf '%s\n' "$name"; return 0; fi
  {
    echo "ERROR: no '$component' $kind found in namespace '$MONITORING_NS'."
    echo "       Looked for, in order:"
    echo "         $kind/${KPS_RELEASE}-${component}"
    echo "         -l app=kube-prometheus-stack-${component},release=${KPS_RELEASE}"
    echo "         -l app.kubernetes.io/name=${component},app.kubernetes.io/instance=${KPS_RELEASE}"
    [ "$kind" = "svc" ] && echo "         $kind/${component}-operated"
    echo
    echo "       Is KPS_RELEASE ($KPS_RELEASE) the right release, and is"
    echo "       MONITORING_NS ($MONITORING_NS) the right namespace? What is there:"
    "${KUBECTL[@]}" -n "$MONITORING_NS" get "$kind" 2>&1 | sed 's/^/         /'
  } >&2
  exit 1
}

# ⚠️ THE TWO LABELS THAT FAIL SILENTLY. Get either wrong and there is NO ERROR
# ANYWHERE: the rules never evaluate, the scrapes never happen, the boards stay
# empty, and every object involved reports itself as successfully created.
#
# RELEASE_LABEL is the `release:` selector carried by the four objects install.sh
# applies — the two ServiceMonitors and the two PrometheusRules. Upstream
# kube-prometheus-stack defaults ruleSelectorNilUsesHelmValues and its two
# siblings to TRUE, which makes the selector `release=<their release name>`. This
# repo's own helm/kube-prometheus-stack/values.yaml sets all three FALSE, which is
# why the label is genuinely harmless HERE and why the ServiceMonitor comments
# used to say so without qualification. On a BYO cluster it is not harmless.
#
# Defaults to KPS_RELEASE rather than to the literal, so someone who sets only
# KPS_RELEASE=my-monitoring gets the matching label for free. A BYO user has two
# possible fixes and should know both: set this, or set those three values false
# on their side — and the second is often not theirs to change.
RELEASE_LABEL="${RELEASE_LABEL:-$KPS_RELEASE}"

# The Grafana sidecar's discovery label. `grafana_dashboard=1` is the
# kube-prometheus-stack chart's convention, NOT a universal one; a different
# Grafana deployment may watch a different key entirely.
GRAFANA_DASHBOARD_LABEL="${GRAFANA_DASHBOARD_LABEL:-grafana_dashboard}"
GRAFANA_DASHBOARD_LABEL_VALUE="${GRAFANA_DASHBOARD_LABEL_VALUE:-1}"

# Ownership, so teardown.sh (and a BYO user's own cleanup) can remove OUR boards
# without touching the several the monitoring chart ships under the same sidecar
# label. Deleting by the sidecar label alone would take the chart's with them.
# Not overridable: it is our marker, not theirs.
DASHBOARD_OWNER_LABEL="app.kubernetes.io/part-of=gpu-sim-dashboards"

# ---- GPU simulation contract (MUST match Terraform node labels) --------------
NODE_POOL_LABEL_KEY="run.ai/simulated-gpu-node-pool"
NODE_POOL_NAME="default"
GPU_COUNT="8"          # informational; source of truth is helm/fake-gpu-operator/values.yaml
GPU_PRODUCT="Tesla-T4"
GPU_MEMORY_MIB="15360"

# ---- helm charts (pinned) ----------------------------------------------------
KPS_REPO="https://prometheus-community.github.io/helm-charts"
KPS_CHART="prometheus-community/kube-prometheus-stack"
KPS_CHART_VERSION="87.17.0"          # verified 2026-07; bump after checking the changelog

FAKE_GPU_REPO="https://runai.jfrog.io/artifactory/api/helm/fake-gpu-operator-charts-prod"
FAKE_GPU_CHART="fake-gpu-operator/fake-gpu-operator"
# PINNED, and more load-bearing than the kube-prometheus-stack pin above. This repo
# hard-codes facts that are true of 0.0.59 specifically and have no plan-time check:
#   * the exporter emits exactly three series (GPU_UTIL / FB_USED / FB_FREE), which is
#     WHY manifests/alerts/ synthesises temp+power — see the header there;
#   * manifests/servicemonitor/ selects `app: nvidia-dcgm-exporter` on port `gpu-metrics`;
#   * the metric labels the dashboard legends use (Hostname, gpu) and the recording
#     rules join on (UUID).
# On `latest`, a chart release that renames a service, port or label silently produces
# an install that is green with blank panels. Bump deliberately:
#   helm search repo fake-gpu-operator/fake-gpu-operator --versions
# and re-check the three bullets above before changing this number.
FAKE_GPU_CHART_VERSION="0.0.59"       # verified 2026-07

# ---- LLM simulation ----------------------------------------------------------
# The simulator mirrors this vLLM engine's metric surface: names, types and
# histogram bucket boundaries. Bucket placement determines histogram_quantile()
# accuracy, so a dashboard tuned against the wrong version does not transfer.
#
# NAMES: V1. Two series were renamed when the V1 engine landed, and this repo
# emitted the superseded v0 spellings for its first two releases — nothing
# failed, they simply stopped matching a real deployment, which is the one thing
# this simulator exists to get right:
#     vllm:gpu_cache_usage_perc          -> vllm:kv_cache_usage_perc
#     vllm:time_per_output_token_seconds -> vllm:inter_token_latency_seconds
# METRIC_SURFACES in scripts/llm-sim.py is the single place that mapping lives;
# `--vllm-surface both` emits the v0 aliases too, for upgrade testing.
#
# One V1 change was NOT a rename, and METRIC_RESHAPES beside it holds that one:
#     vllm:gpu_prefix_cache_hit_rate (gauge of a ratio)
#       -> vllm:prefix_cache_queries_total + vllm:prefix_cache_hits_total
# The shape changed, so the repair is rate(hits)/rate(queries) and not a
# substituted name — which is why it is the best upgrade rehearsal here.
#
# BUCKETS: V1, and verified rather than asserted. TTFT_BUCKETS/TPOT_BUCKETS/
# E2E_BUCKETS in scripts/llm-sim.py are transcribed from vllm/v1/metrics/
# loggers.py, and scripts/check-vllm-buckets.py diffs them against that file
# weekly in CI — along with the metric SET, in both directions. They had ALSO
# drifted — TTFT's whole tail above 10s — which is why the check exists rather
# than a note telling you to re-check.
LLM_VLLM_VERSION="v1"

# model_name is an IDENTITY, not a label: the recording rules aggregate
# by (model_name) and verify.sh asserts the steady tenant by name. These MUST
# match the profile ConfigMaps and MUST be distinct — assert_llm_contract checks
# both. A mismatch gives a green install whose LLM checks silently measure a
# tenant that does not exist.
LLM_STEADY_MODEL="sim-llama-3-8b-steady"
LLM_SATURATED_MODEL="sim-llama-3-8b-saturated"
LLM_DRIVEN_MODEL="sim-llama-3-8b-driven"        # opt-in extras only

# ---- dashboards --------------------------------------------------------------
# The boards live in manifests/dashboards/ as plain .json — one artefact each, used
# three ways: wrapped in a sidecar ConfigMap by install.sh, mounted by Grafana
# provisioning in the compose path, and uploaded as-is to grafana.com. No egress is
# needed at install time either way.
#
# The FILENAME carries the uid (gpu-sim-dcgm.json -> uid gpu-sim-dcgm), which is what
# lets the ConfigMap name be derived rather than tracked. assert_dashboard_contract
# enforces that the filename and the .uid inside really do agree.
# To swap in the fuller upstream board 12239, see manifests/dashboards/README.md.
DASHBOARD_UID="gpu-sim-dcgm"           # MUST match the .uid in the dashboard JSON — it's what
                                       # makes /d/<uid> a stable deep link across re-installs
LLM_DASHBOARD_UID="llm-sim-overview"

# ConfigMap name for a board file. Derived, not configured: a second place to spell
# the name is a second place for it to drift, and verify.sh looks the ConfigMap up by
# calling this same function.
dashboard_configmap_name() { echo "$(basename "$1" .json)-dashboard"; }
DASHBOARD_CM="$(dashboard_configmap_name "$DASHBOARD_UID")"
LLM_DASHBOARD_CM="$(dashboard_configmap_name "$LLM_DASHBOARD_UID")"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"   # local port used by scripts/grafana.sh

# Deep links straight to each board (Grafana redirects /d/<uid> to the slug URL).
grafana_dashboard_url()     { echo "http://localhost:${GRAFANA_PORT}/d/${DASHBOARD_UID}"; }
grafana_llm_dashboard_url() { echo "http://localhost:${GRAFANA_PORT}/d/${LLM_DASHBOARD_UID}"; }

# ---- Prometheus console ------------------------------------------------------
# Prometheus is ClusterIP for the same reason Grafana is, so its web console needs
# the same port-forward treatment — scripts/prometheus.sh. Kept separate from
# GRAFANA_PORT so both consoles can be held open at once.
PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
prometheus_url()         { echo "http://localhost:${PROMETHEUS_PORT}"; }
prometheus_query_url()   { echo "http://localhost:${PROMETHEUS_PORT}/query"; }
prometheus_targets_url() { echo "http://localhost:${PROMETHEUS_PORT}/targets"; }
prometheus_alerts_url()  { echo "http://localhost:${PROMETHEUS_PORT}/alerts"; }
prometheus_rules_url()   { echo "http://localhost:${PROMETHEUS_PORT}/rules"; }

# ---- cloud (override via env) ------------------------------------------------
AWS_REGION="${AWS_REGION:-eu-west-1}"
GCP_PROJECT="${GCP_PROJECT:-}"
GCP_REGION="${GCP_REGION:-europe-west1}"

# Feed GCP_PROJECT to Terraform rather than duplicating it in terraform/gke/terraform.tfvars.
# TF_VAR_<name> is how Terraform reads a variable from the environment, so var.project
# resolves from the same value get-credentials uses (line ~175) and the two cannot drift.
# Only exported when non-empty: an exported-but-empty TF_VAR_project SATISFIES the required
# variable with "", which then fails deep in the GCP API instead of at "No value for
# required variable". A tfvars entry still wins — Terraform ranks it above the environment.
[[ -n "$GCP_PROJECT" ]] && export TF_VAR_project="$GCP_PROJECT"

# ---- local (kind) ------------------------------------------------------------
# The no-cloud target: no account, no credentials, no terraform.tfvars, no spend.
# Everything this repo demonstrates is simulated — the fake operator fabricates
# nvidia.com/gpu through the device plugin's Allocate() response, and the LLM
# simulator is a Python file — so none of it needs a real cloud or a real GPU.
#
# kind rather than k3d/minikube for one repo-specific reason: the fixes this stack
# needs (control-plane bind addresses, the GPU node label, the version pin) all fit in
# kind/gpu-sim.yaml, leaving helm/kube-prometheus-stack/values.yaml unforked. See the
# header of KIND_CONFIG for the full argument.
KIND_CONFIG="kind/gpu-sim.yaml"

# LITE=1 trims the monitoring stack so `local` fits a small container runtime —
# helm/kube-prometheus-stack/values-lite.yaml documents exactly what is given up.
# Read here rather than in install.sh because kind-up.sh needs it too: the sizing
# floor below and the Helm values must agree, and they are two different scripts.
LITE="${LITE:-0}"
[[ "$LITE" == "1" || "$LITE" == "true" ]] && LITE=1 || LITE=0

# The Helm values stack, in -f order. Lite is an OVERLAY on the base file, never a
# replacement: everything load-bearing stays stated once in values.yaml.
KPS_VALUES=(-f helm/kube-prometheus-stack/values.yaml)
[[ "$LITE" == "1" ]] && KPS_VALUES+=(-f helm/kube-prometheus-stack/values-lite.yaml)

# Container-runtime floor. On the default profile Prometheus alone requests 1Gi and
# limits 2Gi, and the kind control plane wants roughly another 1Gi, so colima's
# 2 CPU / 2 GiB default cannot run this: Prometheus simply sits Pending, which reads
# as a broken install rather than an under-provisioned VM. scripts/kind-up.sh checks
# these before creating anything.
#
# LITE=1 lowers the floor because it lowers the demand — Prometheus drops to
# 256Mi/512Mi and Alertmanager, kube-state-metrics and node-exporter go away. The
# floor still is not 2 GiB: the kind node, the control plane, the operator and two
# Helm releases have a cost that trimming values cannot remove.
# ⚠️ THESE ARE REPORTED GiB, NOT ALLOCATED GiB, and the difference is a whole unit.
# kind-up.sh reads the runtime's own MemTotal and floors it with integer division, so a
# colima VM asked for 3 GiB reports 2.83 and reads as 2 — under this floor, refused
# before anything starts. Allocating 4 reports 3.81 and passes. Measured 2026-08-04 on
# colima/aarch64; the README's LITE block gives the command, and
# docs/troubleshooting.md carries the user-facing version of this paragraph.
if [[ "$LITE" == "1" ]]; then
  KIND_MIN_MEMORY_GIB="${KIND_MIN_MEMORY_GIB:-3}"
  KIND_WANT_MEMORY_GIB="${KIND_WANT_MEMORY_GIB:-4}"
else
  KIND_MIN_MEMORY_GIB="${KIND_MIN_MEMORY_GIB:-5}"
  KIND_WANT_MEMORY_GIB="${KIND_WANT_MEMORY_GIB:-8}"
fi
KIND_MIN_CPUS="${KIND_MIN_CPUS:-2}"
KIND_WANT_CPUS="${KIND_WANT_CPUS:-4}"

# kind talks to podman only when told to. Set here, at source time, rather than in
# kind-up.sh alone: teardown.sh also shells out to kind, and a `kind delete cluster` that
# silently looks for a Docker socket would leave a podman user's cluster running with no
# obvious reason why. An explicit value always wins — and a podman user who ALSO has the
# docker binary installed needs to set it, since the heuristic below cannot tell which
# runtime is actually serving.
if [[ -z "${KIND_EXPERIMENTAL_PROVIDER:-}" ]] \
   && ! command -v docker >/dev/null 2>&1 \
   && command -v podman >/dev/null 2>&1; then
  export KIND_EXPERIMENTAL_PROVIDER=podman
fi

# ---- context aliases ---------------------------------------------------------
context_for() {
  case "$1" in
    eks)   echo "gpu-sim-eks" ;;
    gke)   echo "gpu-sim-gke" ;;
    local) echo "gpu-sim-local" ;;
    *)     echo "ERROR: target must be 'eks', 'gke' or 'local'" >&2; return 1 ;;
  esac
}

# Alias whatever context the cloud CLI just made current as $1.
#
# `aws eks update-kubeconfig` takes --alias and is idempotent. gcloud and kind have no
# equivalent: both invent their own context name, both make it current, and neither will
# name it for us. So the alias guard_context checks has to be applied afterwards — and
# both halves of how you do that are traps.
#
#   * A plain `rename-context <generated> <alias> || true` LOOKS idempotent and is not.
#     rename-context REFUSES when the target name already exists, and on the second and
#     later calls in a run it always does: install.sh created the alias, then verify.sh
#     calls get-credentials again, which recreates the generated context and re-selects
#     it. The rename fails, `|| true` hides the failure, and current-context is left on
#     the generated name. guard_context then reports "expected gpu-sim-gke" about a
#     cluster that is in fact the right one — a green install that cannot be verified.
#     This is the bug this function exists to remove; don't reintroduce the one-liner.
#   * Renaming whatever happens to be current is the opposite mistake. If the CLI call
#     failed and left some unrelated context selected, that would relabel a stranger's
#     cluster as ours — exactly the wrong-cluster accident guard_context is for. Hence
#     $2: a glob the current context MUST match before we touch it.
#
# Deleting the stale alias first is safe. It is only ever a pointer this function created,
# it is about to be recreated pointing at the cluster we just authenticated to, and
# delete-context leaves the cluster/user entries it referenced alone.
alias_current_context() {
  local alias="$1" expected="$2" current
  current="$(kubectl config current-context 2>/dev/null || echo '')"

  # Already aliased — nothing to rename. Not an error: it's the steady state on a re-run
  # where the CLI left our own alias selected.
  [[ "$current" == "$alias" ]] && return 0

  # shellcheck disable=SC2053  # $expected is deliberately a glob, so it must stay unquoted
  if [[ -z "$current" || "$current" != $expected ]]; then
    echo "ERROR: after obtaining credentials the current kubecontext should match" >&2
    echo "       '$expected', but it is '${current:-<none>}'." >&2
    echo "       Refusing to rename it — that would alias an unrelated cluster as" >&2
    echo "       '$alias' and defeat the wrong-context guard." >&2
    exit 1
  fi

  kubectl config delete-context "$alias" >/dev/null 2>&1 || true
  if ! kubectl config rename-context "$current" "$alias" >/dev/null; then
    echo "ERROR: could not rename kubecontext '$current' to '$alias'." >&2
    echo "       Inspect the kubeconfig (\$KUBECONFIG=${KUBECONFIG:-<unset>}):" >&2
    echo "         kubectl config get-contexts" >&2
    exit 1
  fi
  # rename-context carries current-context across, but say so explicitly: it costs
  # nothing and makes the postcondition this function promises impossible to misread.
  kubectl config use-context "$alias" >/dev/null
}

# Base list is kubectl + helm ONLY. terraform is passed explicitly by the eks/gke
# callers rather than required here, because the local target has no Terraform at all
# and demanding it would reintroduce exactly the prerequisite the no-cloud path exists
# to remove.
require_tools() {
  local missing=0
  for t in kubectl helm "$@"; do
    command -v "$t" >/dev/null 2>&1 || { echo "ERROR: '$t' not found on PATH" >&2; missing=1; }
  done
  [[ "$missing" -eq 0 ]] || exit 1
}

# Set up kubeconfig with a KNOWN context alias so we never operate on the wrong cluster.
configure_kubeconfig() {
  local target="$1"
  case "$target" in
    # WHY THESE ARE EXPLICITLY FATAL.
    # guard_context below only checks the context NAME, and a gpu-sim-<target> alias from
    # an earlier session outlives the credentials that created it. So when the CLI call
    # here fails — expired token, wrong region, cluster not built yet — the stale alias
    # still satisfies the guard and the install proceeds to talk to a cluster it cannot
    # authenticate to, surfacing as an auth error deep inside `helm upgrade` several
    # minutes later. Worse on macOS: bash 3.2 does not propagate `set -e` into the
    # command substitution that ensure_context is called from, so a bare failing command
    # here is not fatal by itself. It has to say so.
    eks)
      if ! aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" --alias "gpu-sim-eks" >/dev/null; then
        echo "ERROR: 'aws eks update-kubeconfig' failed (see the AWS error above)." >&2
        echo "       Expired credentials, wrong region ($AWS_REGION), or the cluster does" >&2
        echo "       not exist yet (run terraform apply / task eks:apply first)." >&2
        exit 1
      fi
      ;;
    gke)
      [[ -n "$GCP_PROJECT" ]] || { echo "ERROR: set GCP_PROJECT" >&2; exit 1; }
      # needs the gke-gcloud-auth-plugin binary installed locally
      if ! gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$GCP_REGION" --project "$GCP_PROJECT" >/dev/null; then
        echo "ERROR: 'gcloud container clusters get-credentials' failed (see above)." >&2
        echo "       Expired credentials, wrong project ($GCP_PROJECT) or region" >&2
        echo "       ($GCP_REGION), or the cluster does not exist yet (task gke:apply)." >&2
        exit 1
      fi
      # get-credentials names the context gke_<project>_<location>_<cluster> and selects
      # it. Alias from what it selected rather than rebuilding that name from our own
      # variables: the project component is the RESOLVED project id, which need not be the
      # string in $GCP_PROJECT (a project number, or a gcloud default, resolves to a
      # different one). Glob-matched on the cluster name so a mismatch is caught, not
      # silently renamed.
      alias_current_context "gpu-sim-gke" "gke_*_${CLUSTER_NAME}"
      ;;
    local)
      # Deliberately does NOT create the cluster. Phase 1 is a separate, explicit step on
      # every target — `terraform apply` on the clouds, scripts/kind-up.sh here — so that
      # verify.sh / grafana.sh / teardown.sh can never silently build infrastructure as a
      # side effect of being asked to look at it.
      if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
        echo "ERROR: no kind cluster named '$CLUSTER_NAME'." >&2
        echo "       Create it first:  ./scripts/kind-up.sh    (or: task local:up)" >&2
        exit 1
      fi
      # Rebuild the kubeconfig entry from kind rather than assuming it survives — a
      # pruned or regenerated kubeconfig would otherwise fail at the guard below with a
      # context error that says nothing about the cluster still being alive and fine.
      kind export kubeconfig --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
      # Check kind's own entry is really there before aliasing it. Skipping this check
      # does not make it unsafe — guard_context below still refuses to act on the wrong
      # cluster — but it makes it unreadable: you get a wrong-context error naming a
      # context that was never created, which reads like two unrelated faults instead of
      # one missing kubeconfig entry.
      if ! kubectl config get-contexts -o name 2>/dev/null | grep -qx "kind-${CLUSTER_NAME}"; then
        echo "ERROR: kind cluster '$CLUSTER_NAME' exists but no 'kind-${CLUSTER_NAME}'" >&2
        echo "       kubecontext could be created from it. The kubeconfig may be pointing" >&2
        echo "       elsewhere (\$KUBECONFIG=${KUBECONFIG:-<unset>})." >&2
        echo "       Recreate the entry:  kind export kubeconfig --name $CLUSTER_NAME" >&2
        echo "       Or rebuild from scratch: ./scripts/teardown.sh local --destroy && ./scripts/kind-up.sh" >&2
        exit 1
      fi
      # Select kind's entry explicitly. `kind export kubeconfig` normally leaves it
      # current, but it is allowed to fail above (the entry may simply have survived from
      # an earlier run), and alias_current_context renames the CURRENT context — so this
      # is what makes the thing it renames deterministic rather than "whatever was last
      # selected", which on this machine is as likely to be gpu-sim-gke.
      kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null
      # kind names its context kind-<cluster>. Alias to the gpu-sim-<target> shape the
      # other two targets use, so guard_context and the Taskfile's CTX var stay uniform.
      alias_current_context "gpu-sim-local" "kind-${CLUSTER_NAME}"
      ;;
  esac
}

# Wrong-context guard: refuse to act unless the active context is the expected one.
guard_context() {
  local expected="$1" current
  current="$(kubectl config current-context 2>/dev/null || echo '')"
  if [[ "$current" != "$expected" ]]; then
    echo "ERROR: current-context is '${current:-<none>}', expected '$expected'." >&2
    echo "       Refusing to continue so we don't touch the wrong cluster." >&2
    exit 1
  fi
}

# The static YAML in manifests/ HARDCODES these namespaces (kubectl apply, not templated).
# If config.sh drifts from them, turn the silent mismatch into a loud, actionable error
# rather than applying resources into a namespace Prometheus isn't watching.
assert_manifest_namespaces() {
  if [[ "$MONITORING_NS" != "monitoring" || "$FAKE_GPU_NS" != "gpu-operator" || "$LLM_NS" != "llm-sim" ]]; then
    echo "ERROR: the static manifests in manifests/ assume namespaces" >&2
    echo "       monitoring (observability), gpu-operator (fake operator)" >&2
    echo "       and llm-sim (LLM simulation), but config.sh has" >&2
    echo "       MONITORING_NS=$MONITORING_NS FAKE_GPU_NS=$FAKE_GPU_NS LLM_NS=$LLM_NS." >&2
    echo "       Update manifests/**/*.yaml to match, or revert config.sh." >&2
    exit 1
  fi
}

# Dashboard identity drift. install.sh and grafana.sh print /d/$DASHBOARD_UID as the
# access URL, so a UID that no longer matches the JSON yields a confident link to a
# Grafana 404 — the dashboard is fine, the advertised way in isn't.
# Checks a LIST of (manifest file, ConfigMap name, dashboard uid) triples, not a
# single hardcoded one — all three parts of each triple can drift independently,
# and validating only the name and uid would leave a second board's JSON
# unchecked. Pipe-delimited strings because bash 3.2 (macOS) has no dict type.
assert_dashboard_contract() {
  local uid file
  for uid in "$DASHBOARD_UID" "$LLM_DASHBOARD_UID"; do
    file="manifests/dashboards/${uid}.json"

    if [[ ! -f "$file" ]]; then
      echo "ERROR: $file not found (run from the repo root)." >&2
      echo "       config.sh names uid '$uid', and the board file is named after its" >&2
      echo "       uid — install.sh derives the ConfigMap name from that filename." >&2
      exit 1
    fi

    # The filename says one uid; the JSON must agree. They are read independently —
    # Grafana serves /d/<uid> from the JSON, while install.sh and grafana.sh build
    # that link from config.sh — so a mismatch is a confident link to a Grafana 404.
    if ! grep -qE "\"uid\"[[:space:]]*:[[:space:]]*\"${uid}\"" "$file"; then
      echo "ERROR: $file does not declare \"uid\": \"${uid}\"." >&2
      echo "       The filename and the uid inside the board must match: the file is" >&2
      echo "       what Grafana loads, and config.sh is what advertises the URL." >&2
      exit 1
    fi
  done

  # Every board in the directory is applied by install.sh, so any file that is not
  # valid JSON breaks the install — catch it here rather than mid-apply.
  local board
  for board in manifests/dashboards/*.json; do
    if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$board" 2>/dev/null; then
      echo "ERROR: $board is not valid JSON. install.sh wraps every .json in this" >&2
      echo "       directory into a dashboard ConfigMap; a malformed one fails the apply." >&2
      exit 1
    fi
  done
}

# THE LLM NAMING INVARIANT — same class of trap as assert_gpu_contract.
#
# model_name flows through four artefacts: the profile ConfigMaps, the
# by(model_name) recording rules, verify.sh's scoped p95 assertion, and the
# dashboard's panel queries. A typo in any one gives a GREEN install whose LLM
# latency check silently scopes to a tenant that does not exist — the same shape
# of failure as "a green install with ZERO GPUs".
#
# Distinctness matters just as much: two Deployments sharing a model_name merge
# in the recording rules, so a saturated tenant would drag the healthy one's p95
# over the alert threshold and verify.sh would fail for a reason nothing states.
assert_llm_contract() {
  local steady="manifests/llm/10-profiles.yaml"
  local driven="manifests/llm/extras/llm-driven.yaml"
  [[ -f "$steady" ]] || { echo "ERROR: $steady not found (run from the repo root)" >&2; exit 1; }

  local m
  for m in "$LLM_STEADY_MODEL" "$LLM_SATURATED_MODEL"; do
    if ! grep -qF "\"model_name\": \"${m}\"" "$steady"; then
      echo "ERROR: $steady has no profile with model_name \"${m}\"." >&2
      echo "       config.sh and the profile ConfigMaps must agree — verify.sh" >&2
      echo "       asserts p95 latency scoped to LLM_STEADY_MODEL by name, so a" >&2
      echo "       mismatch measures a tenant that does not exist (green, meaningless)." >&2
      exit 1
    fi
  done

  # The opt-in extras are allowed to be absent, but must not collide if present.
  if [[ -f "$driven" ]] && ! grep -qF "\"model_name\": \"${LLM_DRIVEN_MODEL}\"" "$driven"; then
    echo "ERROR: $driven has no profile with model_name \"${LLM_DRIVEN_MODEL}\"." >&2
    exit 1
  fi

  if [[ "$LLM_STEADY_MODEL" == "$LLM_SATURATED_MODEL" || \
        "$LLM_STEADY_MODEL" == "$LLM_DRIVEN_MODEL" || \
        "$LLM_SATURATED_MODEL" == "$LLM_DRIVEN_MODEL" ]]; then
    echo "ERROR: LLM model names must be DISTINCT; config.sh has" >&2
    echo "       steady=$LLM_STEADY_MODEL saturated=$LLM_SATURATED_MODEL driven=$LLM_DRIVEN_MODEL" >&2
    echo "       Duplicates merge in the by(model_name) recording rules." >&2
    exit 1
  fi
}

# THREE-WAY NAMING INVARIANT — the half install.sh cannot inject.
# install.sh passes --set-string topology.nodePoolLabelKey, so the label KEY is
# enforced at install time. The nodePool NAME cannot be: it is a map KEY in
# values.yaml, and --set would ADD a second pool rather than rename the existing
# one. So assert it here instead. A mismatch means the fake operator watches a pool
# no node is labelled for — a green install with ZERO GPUs, which is exactly the
# failure mode the comments in values.yaml and Terraform warn about.
assert_gpu_contract() {
  local vals="helm/fake-gpu-operator/values.yaml"
  [[ -f "$vals" ]] || { echo "ERROR: $vals not found (run from the repo root)" >&2; exit 1; }

  if ! grep -qE "^[[:space:]]+nodePoolLabelKey:[[:space:]]+${NODE_POOL_LABEL_KEY}[[:space:]]*$" "$vals"; then
    echo "ERROR: $vals topology.nodePoolLabelKey does not match config.sh." >&2
    echo "       config.sh NODE_POOL_LABEL_KEY=$NODE_POOL_LABEL_KEY" >&2
    echo "       install.sh overrides the key at install time, so the two disagreeing" >&2
    echo "       means values.yaml is stale and misleading. Align them." >&2
    exit 1
  fi

  if ! grep -qE "^[[:space:]]{4}${NODE_POOL_NAME}:[[:space:]]*(#.*)?$" "$vals"; then
    echo "ERROR: $vals has no topology.nodePools.${NODE_POOL_NAME} entry." >&2
    echo "       config.sh NODE_POOL_NAME=$NODE_POOL_NAME must equal a nodePools key," >&2
    echo "       and that key must equal the node-label VALUE Terraform sets" >&2
    echo "       (modules/contract -> node_pool_name)." >&2
    echo "       Mismatch = green install with ZERO GPUs." >&2
    exit 1
  fi
}

# THE LOCAL HALF OF THE THREE-WAY NAMING INVARIANT.
#
# On EKS/GKE, Terraform applies the GPU node label and assert_terraform_contract cross-
# checks it. The local target has no Terraform, so kind/gpu-sim.yaml is what labels the
# node — and this is its cross-check. Identical failure mode if it drifts: the fake
# operator watches a pool no node is labelled for, and you get a green install with ZERO
# GPUs.
#
# Also pins the node image to K8S_VERSION, so `local` cannot quietly run a different
# Kubernetes minor from the one this file advertises as the source of truth — the same
# assertion assert_terraform_contract makes against the clouds' k8s_version output.
assert_kind_contract() {
  local cfg="$KIND_CONFIG"
  [[ -f "$cfg" ]] || { echo "ERROR: $cfg not found (run from the repo root)" >&2; exit 1; }

  if ! grep -qE "^name:[[:space:]]+${CLUSTER_NAME}[[:space:]]*$" "$cfg"; then
    echo "ERROR: $cfg does not declare 'name: ${CLUSTER_NAME}'." >&2
    echo "       config.sh CLUSTER_NAME drives kind get/delete and the kubecontext" >&2
    echo "       rename in configure_kubeconfig. A mismatch orphans the cluster:" >&2
    echo "       created under one name, looked for under another." >&2
    exit 1
  fi

  if ! grep -qE "^[[:space:]]+${NODE_POOL_LABEL_KEY}:[[:space:]]+${NODE_POOL_NAME}[[:space:]]*$" "$cfg"; then
    echo "ERROR: $cfg does not label the node ${NODE_POOL_LABEL_KEY}: ${NODE_POOL_NAME}." >&2
    echo "       That label is what the fake operator selects on (topology.nodePoolLabelKey" >&2
    echo "       + nodePools.<name> in helm/fake-gpu-operator/values.yaml)." >&2
    echo "       Mismatch = green install with ZERO GPUs." >&2
    exit 1
  fi

  if ! grep -qE "^[[:space:]]+image:[[:space:]]+kindest/node:v${K8S_VERSION}\." "$cfg"; then
    echo "ERROR: $cfg node image is not on the v${K8S_VERSION} line." >&2
    echo "       config.sh K8S_VERSION=$K8S_VERSION, so local would run a different" >&2
    echo "       Kubernetes minor from the one this repo claims to target." >&2
    echo "       Pick the kindest/node:v${K8S_VERSION}.x tag published with YOUR kind" >&2
    echo "       release (kind --version), or change K8S_VERSION deliberately." >&2
    exit 1
  fi
}

# Cross-check this file against what Terraform ACTUALLY built. Terraform owns the
# contract (modules/contract); config.sh keeps its own copies because shell cannot
# import HCL, so this closes the last drift path between the two.
#
# Non-fatal when there is no state to read (not applied yet, remote backend not
# configured, cluster built outside this repo) — the check is a guard against
# drift, not a requirement that Terraform be the only way in. A MISMATCH is always
# fatal, because that is drift by definition.
#
# The `local` target lands in that non-fatal path by construction: there is no
# terraform/local directory, so this returns early and assert_kind_contract above is
# what guards the same invariants instead.
assert_terraform_contract() {
  local target="$1" dir="terraform/$1" tf_name tf_label want_label
  command -v terraform >/dev/null 2>&1 || return 0
  [[ -d "$dir" ]] || return 0

  tf_name="$(terraform -chdir="$dir" output -raw cluster_name 2>/dev/null || true)"
  if [[ -z "$tf_name" ]]; then
    echo "NOTE: no readable terraform state in $dir; skipping the Terraform cross-check." >&2
    return 0
  fi

  if [[ "$tf_name" != "$CLUSTER_NAME" ]]; then
    echo "ERROR: cluster name drift between Terraform and config.sh." >&2
    echo "       terraform -chdir=$dir output cluster_name => $tf_name" >&2
    echo "       config.sh CLUSTER_NAME                    => $CLUSTER_NAME" >&2
    echo "       ensure_context would target the wrong cluster. Align them." >&2
    exit 1
  fi

  # gpu_sim_node_label is emitted as key=value by both roots, so this validates the
  # whole GPU contract against the labels actually applied to the nodes.
  tf_label="$(terraform -chdir="$dir" output -raw gpu_sim_node_label 2>/dev/null || true)"
  want_label="${NODE_POOL_LABEL_KEY}=${NODE_POOL_NAME}"
  if [[ -n "$tf_label" && "$tf_label" != "$want_label" ]]; then
    echo "ERROR: GPU node-label drift between Terraform and config.sh." >&2
    echo "       terraform -chdir=$dir output gpu_sim_node_label => $tf_label" >&2
    echo "       config.sh NODE_POOL_LABEL_KEY=NODE_POOL_NAME    => $want_label" >&2
    echo "       The fake operator would watch a pool no node is labelled for" >&2
    echo "       (green install, ZERO GPUs). Align them." >&2
    exit 1
  fi

  # K8S_VERSION is currently documentation only — nothing else in scripts/ reads it.
  # Checked anyway, because this file calls itself a source of truth and a stale
  # value here is what makes someone act on the wrong number later.
  local tf_k8s
  tf_k8s="$(terraform -chdir="$dir" output -raw k8s_version 2>/dev/null || true)"
  if [[ -n "$tf_k8s" && "$tf_k8s" != "$K8S_VERSION" ]]; then
    echo "ERROR: k8s version drift between Terraform and config.sh." >&2
    echo "       terraform -chdir=$dir output k8s_version => $tf_k8s" >&2
    echo "       config.sh K8S_VERSION                    => $K8S_VERSION" >&2
    exit 1
  fi

  # Region is the one copy that can silently target the WRONG CLUSTER rather than
  # merely failing: configure_kubeconfig passes it to update-kubeconfig, so with a
  # stale region a same-named cluster in another region gets aliased to gpu-sim-eks.
  # guard_context then passes — the alias is right, the cluster behind it is not.
  local want_region tf_region
  case "$target" in
    eks) want_region="$AWS_REGION" ;;
    gke) want_region="$GCP_REGION" ;;
    *) want_region="" ;; # unknown target (e.g. a test stub): nothing to compare
  esac
  tf_region="$(terraform -chdir="$dir" output -raw region 2>/dev/null || true)"
  if [[ -n "$tf_region" && -n "$want_region" && "$tf_region" != "$want_region" ]]; then
    echo "ERROR: region drift between Terraform and config.sh." >&2
    echo "       terraform -chdir=$dir output region => $tf_region" >&2
    echo "       config.sh region for '$target'      => $want_region" >&2
    echo "       update-kubeconfig would look in the wrong region — and if a" >&2
    echo "       same-named cluster exists there, alias IT as the target." >&2
    exit 1
  fi
}

# THE BYO PRECONDITION — the CRDs must already exist.
#
# Under --skip-monitoring nothing installs kube-prometheus-stack, so nothing creates
# the ServiceMonitor and PrometheusRule CRDs. Applying manifests/servicemonitor/ and
# manifests/alerts/ against a cluster that lacks them is precisely the
# green-install-with-nothing-working failure the other assertions exist to prevent —
# except here it is worse, because `kubectl apply` DOES error and install.sh would
# still have created the namespace, the ConfigMaps and the dashboards first.
#
# Unlike the assert_* family this one needs a live cluster, so install.sh calls it
# after ensure_context rather than beside the others. It still runs before anything
# is created, which is the property that matters: a refusal leaves the cluster
# untouched.
#
# ⚠️ Names the fix. "CRD not found" sends people to the wrong place — they go
# looking for a broken manifest rather than a missing operator.
assert_monitoring_crds() {
  local ctx="$1" missing=() crd

  # The namespace too. The static manifests hardcode `namespace: monitoring`
  # (assert_manifest_namespaces pins that), and under --skip-monitoring nothing
  # creates it — so a monitoring stack living somewhere else fails at the first
  # apply with a bare "namespaces not found" that says nothing about why.
  if ! kubectl --context "$ctx" get namespace "$MONITORING_NS" >/dev/null 2>&1; then
    echo "ERROR: namespace '$MONITORING_NS' does not exist." >&2
    echo "       This repo's ServiceMonitors, rules and dashboard ConfigMaps are" >&2
    echo "       applied there — the names are static in manifests/, which is why" >&2
    echo "       assert_manifest_namespaces refuses to let config.sh drift from them." >&2
    echo "       Install your monitoring stack into '$MONITORING_NS', or create the" >&2
    echo "       namespace and make sure its Prometheus and Grafana watch it." >&2
    exit 1
  fi

  for crd in servicemonitors.monitoring.coreos.com prometheusrules.monitoring.coreos.com; do
    kubectl --context "$ctx" get crd "$crd" >/dev/null 2>&1 || missing+=("$crd")
  done
  # bash 3.2 treats an empty array as unbound under set -u; the +alternate form
  # expands to nothing rather than aborting (same trick install.sh uses for helm args).
  [[ ${#missing[@]} -eq 0 ]] && return 0

  echo "ERROR: --skip-monitoring was passed, but the Prometheus Operator CRDs this" >&2
  echo "       repo's ServiceMonitors and PrometheusRules need are not installed:" >&2
  printf '         %s\n' "${missing[@]+"${missing[@]}"}" >&2
  echo "" >&2
  echo "       Nothing has been created. Install a monitoring stack that provides" >&2
  echo "       them first, then re-run:" >&2
  echo "" >&2
  echo "         helm repo add prometheus-community $KPS_REPO" >&2
  echo "         helm install <release> prometheus-community/kube-prometheus-stack \\" >&2
  echo "           -n $MONITORING_NS --create-namespace --wait" >&2
  echo "" >&2
  echo "       If your release is NOT named '$KPS_RELEASE', pass its name through" >&2
  echo "       so the port-forwards and the selector labels find it:" >&2
  echo "" >&2
  echo "         KPS_RELEASE=<release> ./scripts/install.sh <target> --skip-monitoring" >&2
  echo "" >&2
  echo "       Or drop --skip-monitoring and let this repo install the stack." >&2
  exit 1
}

# Configure + guard in one call.
ensure_context() {
  local target="$1" ctx
  ctx="$(context_for "$target")" || exit 1
  configure_kubeconfig "$target"
  guard_context "$ctx"
  echo "$ctx"
}
