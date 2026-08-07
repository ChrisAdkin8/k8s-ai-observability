# Prompt: GPU Simulation Test & Observability Stack for EKS and GKE (Terraform)

> ## ⚠️ THIS BRIEF IS HISTORICAL — read this box before acting on anything below
>
> This is the **original** brief for the GPU simulation rig. The rig is built, and the repo
> has moved past this document in several places. **Where this file and the code disagree,
> the code wins.** Reconciled on **2026-07-30**; the annotations below are marked
> **SUPERSEDED** or **REVERSED** in place rather than deleted, because the reasoning is
> what stops a decision being re-made badly.
>
> **What changed since this was written:**
>
> | | Then (below) | Now (shipped) |
> |---|---|---|
> | Targets | EKS + GKE | **Three** — plus `local` (kind), which is the advertised one-shot path and needs no cloud account |
> | Provisioning | "Terraform is the only path" | True for the clouds; `local`'s Phase 1 is `scripts/kind-up.sh` |
> | Stacks | two (GPU sim, observability) | **three** — a simulated vLLM serving stack was added; see `prompt-llm-sim.md`, which is the authority on it |
> | Dashboard | start from Grafana ID 12239 | **Reversed** — ships a hand-written self-contained board; 12239 is an opt-in swap documented in `manifests/dashboards/README.md` |
> | Front door | "a Makefile is a nice-to-have" | `Taskfile.yml` + one thrice-included `taskfiles/target.yml` |
> | EKS nodes | EKS-optimised AMI (implied) | hardened **hc-base Ubuntu 24.04** with a first-boot bootstrap template — see "Decisions made after this brief" at the end |
> | Grafana access | port-forward + admin password | port-forward + **anonymous Viewer**, so viewing needs no password |
> | `verify.sh` checks | 1–7 here | `1, 2, 3, 4, 4b, 4c, 4d, 5` + `L1`–`L6` from `prompt-llm-sim.md`. **Do not renumber** — the numbers are referenced across the repo |
>
> Requirements 2, 4, 5, 6, 7 and 8 below (two-phase ownership, ephemeral storage, namespace
> coordination, install ordering + readiness, teardown ordering, pinned k8s version) all
> **still hold** and are still enforced.

## Role & Objective

You are a Kubernetes platform engineer. Produce the **documentation and artefacts**
required to deploy two stacks onto **both Amazon EKS and Google GKE**, using
**Terraform** for all cloud provisioning:

1. **Test stack** — a *simulated* NVIDIA GPU environment that requires **no real GPU
   hardware** (CPU-only node pools), so we can exercise GPU-aware workloads and
   observability cheaply.
2. **Observability stack** — Prometheus + Grafana consuming DCGM-format GPU metrics,
   with an NVIDIA GPU dashboard and a set of GPU alert rules.

End state: an engineer clones this repo and, per cloud, runs a two-phase flow
(`terraform apply` for infra, then an install step for the stacks) that yields a
working cluster where GPU dashboards populate and at least one GPU alert can be fired —
without provisioning a single physical GPU.

## Background / Technical Facts (use these; do not re-derive)

- **GPU simulation:** Use **`run-ai/fake-gpu-operator`**
  (`ghcr.io/run-ai/fake-gpu-operator`; now maintained under NVIDIA after the Run:ai
  acquisition). It advertises `nvidia.com/gpu`, injects a fake `nvidia-smi` into GPU
  pods, and emits **DCGM-format Prometheus metrics**, so the standard NVIDIA
  observability stack works unchanged. Repo: https://github.com/run-ai/fake-gpu-operator
- **⚠️ Node labeling is mandatory:** The operator only fakes GPUs on nodes carrying its
  selector label (e.g. `run.ai/simulated-gpu-node-pool=<pool>`). Verify the exact label
  key/value against the installed chart version, then set it **on the Terraform node
  group/pool** (EKS `labels`, GKE `node_config.labels`). Without this, **no fake GPUs
  appear and nothing else works.** This is the most common failure — make it a
  first-class, tested step.
- **⚠️ Three-way naming invariant (same trap, one layer deeper):** the label **value**
  on the nodes, the fake-operator **topology config's node-pool name**, and the Helm
  **node selector** must all agree. A mismatch (e.g. topology pool `default` vs nodes
  labeled `…=cpu-sim`) yields a **green install with zero GPUs**. State this invariant
  in one place and assert it.
- **Deployment mode:** these are real managed CPU nodes, so the operator runs its
  **device-plugin DaemonSet** (which needs a `hostPath` mount of
  `/var/lib/kubelet/device-plugins`) — **not** the KWOK virtual-node path. This has two
  consequences pinned below: GKE must be **Standard** (Autopilot blocks that hostPath),
  and nodes must be **amd64** (image arch).
- **Driving load:** Simulated utilisation is controlled with a pod annotation, e.g.
  `run.ai/simulated-gpu-utilization: "10-30"` (oscillates util 10–30%). Utilisation is
  the **only** controllable quantity — design alert tests only around it. Memory is
  **allocation-driven, not load-driven**: a GPU with a pod on it reports
  `FB_USED=<all>, FB_FREE=0`, an unallocated GPU the reverse.
- **Metric names** are the real DCGM ones, but the fake exporter emits **only three
  series** (verified against chart 0.0.59, image `status-exporter`):
  `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE`. There is **no
  chart knob to add more** — no container args, no metrics-config env var, nothing in
  `helm show values`. Consequently:
  - `DCGM_FI_DEV_GPU_TEMP` and `DCGM_FI_DEV_POWER_USAGE` are **synthesised** by recording
    rules in `manifests/alerts/gpu-prometheusrule.yaml`, derived from utilisation and
    recorded under the real DCGM names with a `source="derived"` label.
  - `DCGM_FI_DEV_SM_CLOCK` is **not available at all** — do not build panels or alerts
    on it.

  Re-check with:
  ```sh
  kubectl -n gpu-operator run m --rm -i --restart=Never --image=curlimages/curl:8.10.1 \
    -- -s http://nvidia-dcgm-exporter:9400/metrics | grep '^# HELP DCGM'
  ```
- **Observability stack:** `kube-prometheus-stack` (Prometheus + Grafana + Operator).
- **Dashboard: REVERSED.** The repo ships a **hand-written, self-contained** four-panel DCGM
  board (`manifests/dashboards/dcgm-configmap.yaml`, uid `gpu-sim-dcgm`) instead of
  importing 12239. The reason is the caveat below turning out to matter: 12239 is
  bare-metal-oriented, and pulling it also means egress to grafana.com, which breaks the
  air-gapped property everything else here has. 12239 remains available as a documented
  opt-in swap in `manifests/dashboards/README.md`. The advice below is retained because it
  is what led to that decision — and its instinct was right:
- **Dashboard (original):** Start with the NVIDIA DCGM dashboard, Grafana ID `12239`
  (https://grafana.com/grafana/dashboards/12239). **Do not assume it works as-is:**
  12239 is bare-metal-oriented and its PromQL expects labels like `gpu`, `UUID`,
  `Hostname`, `modelName`. **Verify the fake-operator's metric labels match**; if pod-
  level attribution or labels don't line up, switch to a Kubernetes-oriented DCGM
  dashboard. Also check whether the fake-operator project ships its own recommended
  dashboard before defaulting to 12239. Provision the chosen dashboard as a labelled
  ConfigMap (`grafana_dashboard: "1"`) in a namespace the Grafana sidecar watches — no
  manual import.

## Hard Requirements & Decisions (already made — honour them)

1. **Terraform is the only provisioning path.** Do **not** produce `eksctl`/`gcloud`
   cluster-creation alternatives (a one-line "manual alternative exists" note is the
   most you should add).
   **SUPERSEDED — a third target, `local` (kind), was added.** It is not a "manual
   alternative" to Terraform; it is a first-class target with no cloud account, no
   credentials and no spend, and `task local:up` is the advertised way to see the rig work
   at all. Its Phase 1 is `scripts/kind-up.sh` + `kind/gpu-sim.yaml`, which carries the GPU
   node label Terraform applies on the clouds; `assert_kind_contract` cross-checks it the
   way `assert_terraform_contract` does for EKS/GKE. **Phase 2 is byte-identical on all
   three** — the only difference is how the kubecontext is obtained, which is why the
   `local` target could be added without forking any script. The original rule still holds
   *for the clouds*: no `eksctl`, no `gcloud container clusters create`.
2. **Two-phase ownership, to avoid the Terraform provider chicken-and-egg problem:**
   - **Phase 1 — infra (Terraform):** cluster, CPU-only node pools **with the GPU
     simulation label**, IAM, and any required add-ons. Terraform does **not** manage
     Helm releases.
   - **Phase 2 — apps (install step):** Helm releases + manifests, driven by
     `scripts/install.sh` (or a separate, independently-applied root module with its
     provider configured from Phase 1 outputs). Keep the two phases separable so the
     Kubernetes/Helm providers are never configured against a cluster that does not yet
     exist.
3. **CPU-only, but sized and shaped for the stack:**
   - **No** GPU node pools, GPU quota, or NVIDIA driver install.
   - **amd64 node pools only** — the fake-operator/DCGM images may be amd64-only; an
     arm64 (Graviton / GKE Arm) pool yields `exec format error` crash-loops. Only choose
     Arm if you have verified multi-arch images.
   - **Minimum node size ~2 vCPU / 8 GB, at least 2 nodes** (e.g. `t3.large` /
     `e2-standard-2`). "Smallest viable" must still fit Prometheus + Alertmanager +
     Grafana + kube-state-metrics + node-exporter + the fake-operator (~3–4 GB
     schedulable RAM). Undersized nodes (`*.small`) leave Prometheus `Pending` or OOM.
     **AMENDED — the node *count* is not uniform, so never hardcode it or anything derived
     from it.** EKS ships `node_count = 2` (`t3.large`); GKE's `node_count` is **per zone**
     on a regional cluster, so the default `1` yields ~3 nodes (`e2-standard-2`); and
     `local` is a **single** kind node. The sizing floor survives on `local` as a
     *container-runtime* floor instead — `KIND_MIN_MEMORY_GIB` / `KIND_MIN_CPUS` in
     `config.sh`, checked by `kind-up.sh` before anything is created, because Docker
     Desktop's or colima's 2 GiB default reproduces exactly the `Pending` Prometheus this
     bullet warns about, just one layer down where it looks like a broken install rather
     than an under-provisioned VM. Note the GPU series count follows node count
     (8 per node), which is why the fake-GPU facts in `prompt-llm-sim.md` are stated
     per-target.
   - **Do not taint the sim nodes** (or provide matching tolerations). Everything runs on
     this one CPU pool; a production-style GPU taint would stop the monitoring stack
     scheduling.
   - **GKE Standard only — not Autopilot.** Autopilot blocks the `hostPath` mount the
     device-plugin DaemonSet needs and constrains node labels/DaemonSets, so the fake
     operator cannot register GPUs. Pin Standard explicitly.
4. **Ephemeral storage for the observability stack** (Prometheus/Grafana on `emptyDir`,
   no PVCs). This keeps EKS and GKE **symmetric** and deliberately avoids the EKS
   **EBS CSI driver + IRSA/Pod-Identity** requirement that a persistent Prometheus would
   otherwise force. Call this out as an intentional trade-off (metrics don't survive pod
   restarts — fine for a test rig). If persistence is ever wanted, document that EKS
   then needs the EBS CSI add-on + IAM role; GKE works with its default PD class.
5. **Namespace & Prometheus coordination (critical):** Pin namespaces for both stacks
   and make them agree. The fake-operator defaults its Prometheus reference to
   `http://prometheus-operated.<ns>:9090` (historically `runai`); set it to the actual
   `kube-prometheus-stack` service/namespace you deploy. Document the chosen namespaces
   in one place.
6. **Install ordering AND readiness waits (critical):** Install `kube-prometheus-stack`
   **first** so the `ServiceMonitor`/`PrometheusRule` CRDs exist before the fake-operator's
   ServiceMonitor and our alert rules are applied. Encode this order in `install.sh` — but
   order alone is not enough; **wait for readiness between stages** (`helm install --wait`
   plus `kubectl wait`):
   - The kube-prometheus-stack **`PrometheusRule` validating webhook** must be serving
     before you apply `manifests/alerts/`, or the apply is **rejected even though the CRD
     exists**.
   - ~~The fake-operator injects the fake `nvidia-smi`/topology via a **mutating admission
     webhook**; deploy sample GPU workloads only **after** the operator and its webhook
     are Ready, otherwise injection silently doesn't happen (or, with `failurePolicy:
     Fail`, pod creation is blocked).~~
     **CORRECTION (verified against a running cluster, chart 0.0.59): this is wrong.**
     There is **no** mutating webhook — `kubectl get mutatingwebhookconfigurations` shows
     no run.ai/gpu entry. Injection happens in the **device plugin's `Allocate()`
     response**, so it is invisible in the pod spec and there is no webhook to wait on.
     Wait for the operator's **DaemonSets** instead, so the device plugin is advertising
     `nvidia.com/gpu` before GPU pods schedule. See `docs/architecture.md`.
7. **Teardown ordering (critical):** `teardown.sh` must **uninstall Helm releases and
   wait for cloud-created resources (LoadBalancers, any PVCs) to drain before
   `terraform destroy`** — otherwise dangling ELBs/target-groups/disks hang or fail the
   destroy and strand billable resources.
8. **Pin a target Kubernetes control-plane version** for both clouds so the fake-operator
   takes a deterministic path (legacy device plugin vs DRA on k8s ≥ 1.31). State it. If
   the pinned fake-operator chart proves incompatible with the chosen version, fall back
   to a minimal fake device plugin that advertises `nvidia.com/gpu` and emits DCGM
   metrics — the rest of the stack is unaffected.
9. **Grafana private by default** (port-forward); ingress/LoadBalancer is opt-in.
   Admin password not hardcoded in git — generate or reference a secret and document
   retrieval.
   **AMENDED — anonymous **Viewer** auth is enabled on top of this.** Privacy is provided by
   the network boundary (ClusterIP + port-forward), so requiring a password *behind* that
   boundary bought nothing and cost every reader a credential lookup before they could see
   a panel. Viewing needs no login; editing still requires `admin`, whose
   chart-generated password is retrieved from the `kube-prometheus-stack-grafana` secret.
   The trade-off is stated in `helm/kube-prometheus-stack/values.yaml`, and `verify.sh`
   asserts it by fetching each board's uid over an **unauthenticated** request — so if
   anonymous access regresses, a check fails rather than a doc going quietly stale.
   Prometheus gets the same treatment for the same reason: ClusterIP, reached via
   `scripts/prometheus.sh`, on a separate port from Grafana so both consoles can be held
   open at once.

## Deliverables

### A. Terraform (infra — Phase 1)

- `terraform/modules/` — shared module(s) for anything common across clouds.
- `terraform/eks/` — uses `terraform-aws-modules/eks/aws`; small CPU-only managed node
  group **carrying the GPU-sim node label**; cluster access wired so `kubectl`/Helm work
  (access entries / aws-auth as appropriate).
- `terraform/gke/` — uses the Google provider / `terraform-google-modules`; small
  CPU-only node pool **carrying the GPU-sim node label**.
- Each root module: pinned provider & module versions; variables for
  region/project/cluster name/k8s version; a `terraform.tfvars.example`; useful
  `outputs` (cluster name, endpoint, and whatever Phase 2 needs).
- **State:** document the backend choice. Local state is acceptable for these ephemeral
  test clusters; note the remote-backend option (S3+DynamoDB / GCS / HCP Terraform).
  Flag that **state contains sensitive data** (kubeconfig material, Grafana password)
  and should be handled accordingly.
- **Provider prerequisites:** AWS credentials/profile/region for EKS; GCP
  `project`/`region` and enabled APIs (container, compute) for GKE. Phase-2 `kubectl`
  against GKE also needs the **`gke-gcloud-auth-plugin`** binary installed locally (a
  common silent prerequisite).

### B. Kubernetes apps (Phase 2)

- **Helm values**
  - `fake-gpu-operator` values: topology (number of simulated nodes/GPUs, GPU model
    string, memory), the correct **node selector label**, and the **Prometheus URL /
    namespace** matching the observability stack. Ensure its metrics Service is
    scrapeable (see ServiceMonitor below).
  - `kube-prometheus-stack` values: Grafana enabled, `emptyDir` storage for Prometheus
    and Grafana (this is the Prometheus-Operator **default** when no `storageSpec` is
    set — a near-zero-effort choice, not a custom volume), sidecar dashboard discovery
    enabled.
- **ServiceMonitor contract (make it concrete):** first check whether the fake-operator
  chart **already ships a ServiceMonitor** (via its status-exporter) — if so, enable/
  configure it through Helm values rather than hand-writing a second one, which would
  create duplicate/conflicting scrape configs. Only if the chart provides none, author a
  `ServiceMonitor` yourself: specify the metrics **Service name, port name, and
  namespace**, and a `selector`/`namespaceSelector` that match it. Either way it must be
  adoptable by this Prometheus — carry the `release: <kps-release>` label, **or** set
  `serviceMonitorSelectorNilUsesHelmValues: false` in kube-prometheus-stack values to
  select all ServiceMonitors.
- **Dashboard ConfigMap:** chosen NVIDIA DCGM dashboard, labelled `grafana_dashboard: "1"`.
- **Sample workloads:** 2–3 Deployments/Jobs requesting `nvidia.com/gpu` with different
  `run.ai/simulated-gpu-utilization` ranges (idle / steady / spiky) so panels and alerts
  have signal. **The annotation must be on the pod template**
  (`spec.template.metadata.annotations`), **not** the Deployment/Job top-level
  `metadata` — misplacing it yields scheduled pods with flat/zero metrics (another quiet
  "looks fine, no signal" failure).
- **`PrometheusRule`:** GPU alerts for the **controllable** metrics (utilisation, and
  memory if controllable). Include a "GPU metrics absent" (target-down) rule but note it
  is not exercised by default — the fake exporter is always up; document scaling the
  exporter to zero as the way to test it.

### C. Scripts (idempotent; `set -euo pipefail`; pinned chart versions)

- **Wrong-context guard (safety):** every script sets up the kubeconfig with a known
  context alias per target (`gpu-sim-eks` / `gpu-sim-gke`), passes that context
  explicitly to all `kubectl`/`helm` calls, and **asserts `kubectl config
  current-context` matches the expected cluster before any apply/destroy** — aborting
  otherwise. A `teardown.sh` fired against the wrong current-context must not be able to
  tear down an unintended cluster.

- `scripts/install.sh <eks|gke>` — resolve kubecontext from Phase-1 outputs, add Helm
  repos, install `kube-prometheus-stack` **then** `fake-gpu-operator`, apply manifests,
  print how to reach Grafana and how to fetch its password.
- `scripts/teardown.sh <eks|gke>` — uninstall Helm releases, wait for LBs/PVCs to drain,
  then optionally `terraform destroy`.
- `scripts/verify.sh <eks|gke>` — automated acceptance checks (see below).
- A `Makefile` wrapping the common targets is a nice-to-have.

### D. Documentation

- `README.md` — what this is; **Mermaid** architecture diagram
  (fake-gpu-operator → DCGM metrics → Prometheus → Grafana/alerts); prerequisites
  (local tool versions: terraform, kubectl, helm, aws/gcloud); the two-phase quick
  start; and an explicit **fidelity caveat** (synthetic metrics — validates the
  observability pipeline and Kubernetes behaviour, **not** real GPU silicon behaviour).
- `docs/eks.md` / `docs/gke.md` — per-cloud prerequisites, Phase-1 apply, Phase-2
  install, verification, teardown, and **cost notes** (cheapest nodes that still meet the
  sizing floor in requirement 3 — do **not** drop below ~2 vCPU / 8 GB; what incurs
  charges; reminder to run teardown).
- `docs/observability.md` — available metrics, the dashboard, alert rules, how to drive
  load with the annotation, example PromQL, and a worked example of pushing a workload
  until an alert flips to `firing`.
- `docs/architecture.md` — component responsibilities and end-to-end data flow, plus the
  list of every EKS-vs-GKE difference (there should be few by design).

## Conventions

- **Maximise shared config**; only cloud-specific concerns (provisioning, cluster
  access, LB/ingress annotations) may differ — enumerate every difference in
  `docs/architecture.md`.
- **Pin all versions** — Terraform providers/modules, Helm charts, container images,
  dashboard revision, k8s version — in one referenced place.
- **Idempotent & reversible**; re-running install must not break; teardown leaves no
  billable resources.
- **Least-privilege** IAM where practical; note any broad grants.

## Repository Layout

**SUPERSEDED — this is the layout as shipped**, not a suggestion. The repo is
`k8s-ai-observability` (renamed from `k8s-gpu-simulation` once LLM serving joined GPUs as a
simulated domain), and it grew a local target, a second stack and a Task front door:

```
k8s-ai-observability/
├── README.md  LICENSE  Makefile
├── Taskfile.yml                     # the front door: task local:up / eks:up / gke:up
├── taskfiles/target.yml             # included 3× with CLOUD=eks|gke|local — one definition
├── prompt.md                        # this file (historical)
├── prompt-llm-sim.md                # the LLM serving simulation brief — authority on it
├── docs/
│   ├── architecture.md  eks.md  gke.md
│   ├── observability.md             # GPU metrics, dashboard, alerts, driving load
│   ├── llm-simulation.md            # the vLLM simulator, profiles, alerts
│   └── troubleshooting.md           # repo-wide "empty panel" triage
├── kind/gpu-sim.yaml                # local target's Phase 1: node label + k8s version pin
├── terraform/
│   ├── modules/contract/            # variables + outputs ONLY — the shared invariant,
│   │                                #   cross-checked by assert_terraform_contract
│   ├── eks/                         # + templates/ubuntu_eks_user_data.sh.tpl
│   └── gke/
├── helm/
│   ├── fake-gpu-operator/values.yaml
│   └── kube-prometheus-stack/values.yaml
├── manifests/
│   ├── alerts/                      # gpu- + llm-prometheusrule.yaml
│   ├── dashboards/                  # dcgm- + llm-configmap.yaml, + README.md
│   ├── servicemonitor/              # fake-gpu- + llm-sim-servicemonitor.yaml
│   ├── workloads/                   # gpu-workloads.yaml (idle/steady/busy)
│   │   └── extras/                  # opt-in: gpu-driven, multi-gpu, batch cronjob
│   └── llm/                         # 00-namespace, 10-profiles, 20-simulators
│       └── extras/llm-driven.yaml   # opt-in, driven by drive-llm-load.sh
└── scripts/
    ├── config.sh                    # single source of truth + all drift assertions
    ├── kind-up.sh                   # local Phase 1 (checks the runtime memory floor)
    ├── install.sh  teardown.sh  verify.sh
    ├── grafana.sh  prometheus.sh    # port-forwarded consoles, held in the foreground
    ├── drive-load.sh  drive-llm-load.sh
    └── llm-sim.py                   # the simulator; install.sh builds its ConfigMap from
                                     #   this file, which is why `task selftest` can run
                                     #   with no cluster at all
```

⚠️ Both `extras/` directories rely on `kubectl apply -f <dir>` being **non-recursive** —
that is what makes them opt-in. Don't "fix" it with `-R`.

## Acceptance Criteria (make `verify.sh` assert these)

> **AMENDED — `verify.sh` now numbers these `1, 2, 3, 4, 4b, 4c, 4d, 5`, followed by
> `L1`–`L6` from `prompt-llm-sim.md`. Do not renumber any of them** — the numbers are cited
> in `manifests/dashboards/README.md` and in inline comments across the repo. The three
> `4x` checks were added because the criteria as written below could all pass on a rig
> producing nothing useful; each is noted under criterion 4.
>
> One principle learned the hard way and now binding on every check here: **assert that
> values MOVE, not merely that series exist**, and **select alerts by exact name, never by
> a wildcard regex**. This file's own criterion 5 was once implemented as
> `alertname=~".*GPU.*"`, which was silently satisfied by the always-firing
> `GPUHighMemoryUsage` — so it passed with utilisation stuck at zero, the one thing it
> existed to prove.

1. At least one node reports `nvidia.com/gpu` **allocatable > 0** despite no physical GPU
   (proves node labeling + operator wiring).
2. A sample workload requesting `nvidia.com/gpu` schedules and runs.
3. The fake-operator's Prometheus **scrape target is `up`**, and a known DCGM series
   (e.g. `DCGM_FI_DEV_GPU_UTIL`) returns data.
4. The chosen DCGM dashboard's ConfigMap is loaded by the Grafana sidecar **and** the
   dashboard's key PromQL expressions return data (this is the scriptable proxy for
   "panels are non-empty" — asserting Grafana panel rendering directly is not practical;
   run the dashboard's own queries against Prometheus and confirm non-empty results,
   which also confirms label compatibility). Look the ConfigMap up **by name** — the chart
   ships several `grafana_dashboard` ConfigMaps of its own, so "at least one exists" proves
   nothing. Three checks were added around this one:
   - **4b — the advertised access path itself.** Fetch the board by **uid** over an
     **unauthenticated** request. One check covers both halves: the sidecar really imported
     it under that uid (so the `/d/<uid>` link `install.sh` prints resolves rather than
     404s) and anonymous Viewer access is live (so that link needs no password).
   - **4c — the derived-series coupling.** The temperature and power panels query series the
     fake exporter does not emit; they exist only because of recording rules in
     `manifests/alerts/`. That dependency is invisible from the dashboard JSON, so assert
     it — otherwise a dropped or renamed rule surfaces as two quietly blank panels nobody
     notices for a week. Poll it: a recording rule only materialises on its next evaluation.
   - **4d — utilisation is actually being DRIVEN, not merely present.** Every check above is
     satisfied by a series that exists and reads `0`, because the exporter emits
     `DCGM_FI_DEV_GPU_UTIL` for every simulated GPU whether or not any pod annotation is
     taking effect. So the misplaced-annotation failure warned about above (annotation on
     the Deployment instead of the pod template) would leave the whole suite green with
     flat-zero panels. Assert the busy workload's band really reaches the metric
     (`max(DCGM_FI_DEV_GPU_UTIL) > 80`).
5. Adjusting a workload's `run.ai/simulated-gpu-utilization` drives a GPU alert from
   `pending` to `firing` — `verify.sh` must **poll and wait out the rule's `for:`
   duration** rather than checking once (otherwise it flakes).
6. `teardown.sh` removes all Helm releases and cloud resources, and `terraform destroy`
   completes cleanly with nothing stranded.
7. Every version is pinned; every EKS-vs-GKE difference is documented.

## Decisions made after this brief (ADDED — no requirement above covers them)

These were forced by reality rather than chosen from the brief, and each has a failure mode
worth knowing before touching the relevant file.

1. **EKS nodes run the hardened hc-base Ubuntu 24.04 image, not the EKS-optimised AMI.**
   hc-base ships no Kubernetes components, so `terraform/eks/templates/ubuntu_eks_user_data.sh.tpl`
   installs the entire node stack — containerd, kubelet, the ECR credential provider,
   aws-iam-authenticator — at first boot. The trade, stated in `terraform/eks/main.tf`:
   ~2–4 min extra per node boot (paid again on every scale-up); the boot needs `apt`,
   `pkgs.k8s.io`, `artifacts.k8s.io` and `github.com` reachable through the NAT gateway, and
   **any one of them unreachable means the node never joins, silently**; and `var.k8s_version`
   now pins the kubelet package as well as the control plane. The durable fix is a
   Packer-baked EKS-ready hc-base; this is the no-new-pipeline version of that.
   - **This makes two module pins load-bearing.** `terraform-aws-modules/eks` is pinned at
     patch level (`~> 21.24.0`) because the node group sets `ami_type = "AL2_x86_64"` purely
     so the module's `ami_type → user-data-template` map resolves and `user_data_template_path`
     wins via `coalesce`. `AL2_x86_64` is EOL upstream, so a routine minor bump could drop
     that map entry and produce nodes that boot and never join — **with no plan-time signal
     at all**. `terraform-aws-modules/vpc` is pinned the same way (`~> 5.21.0`) because it
     owns the NAT gateway that bootstrap egress depends on. Bump either by hand, re-checking
     that map.
   - Note module v21 renamed `cluster_name` / `cluster_version` / `cluster_endpoint_public_access*`
     to `name` / `kubernetes_version` / `endpoint_public_access*`. The old names are **hard
     errors, not deprecations**.

2. **`api_allowed_cidrs` is a required variable on both clouds**, with a validation that
   rejects an empty list **and** `0.0.0.0/0`. There is deliberately no default: the API
   endpoint is public (making it private would need a bastion or a VPN for Phase 2 to reach
   it), so the only thing standing between it and the internet is this list. A missing value
   must **fail the plan**, not fall back to something permissive. Requirement 9 above covers
   Grafana; this covers the control plane, which matters more.

3. **`terraform/modules/contract/` holds variables and outputs only — no resources.** It is
   the shared *invariant* (cluster name, k8s version, the GPU node label as `key=value`), not
   shared infrastructure, because EKS and GKE have almost nothing provisionable in common.
   Both roots consume it and re-export it, and `assert_terraform_contract` in `config.sh`
   reads those outputs back to close the last drift path between HCL and shell — shell cannot
   import HCL, so `config.sh` keeps its own copies and this is what stops them diverging.
   The region check inside it earns its place: a stale region is the one copy that can
   silently target the **wrong cluster** rather than merely failing, because
   `update-kubeconfig` would alias a same-named cluster in another region to `gpu-sim-eks`
   and the wrong-context guard would pass.

4. **`Taskfile.yml` is the front door; the Makefile is vestigial.** Requirement C called a
   Makefile a nice-to-have; what shipped is `Taskfile.yml` including `taskfiles/target.yml`
   three times with `CLOUD=eks|gke|local`. One definition for three targets is the point —
   three copies of that task list is how the targets start disagreeing about what "install"
   means. It **wraps** `scripts/` and must never reimplement the install ordering, the
   wrong-context guard or the drift assertions; the scripts stay runnable on their own.

5. **Opt-in extra workloads** (`manifests/workloads/extras/`): `gpu-driven` plus
   `scripts/drive-load.sh` to walk utilisation through a curve, a multi-GPU pod, and a batch
   CronJob. Not applied by `install.sh` — they exist so the dashboard can be made to *move*
   on demand without disturbing the steady-state workloads `verify.sh` depends on.

6. **A whole second simulated domain was added: LLM serving.** `prompt-llm-sim.md` is the
   authority — a stdlib-Python simulator emitting the real `vllm:*` metric surface, its own
   dashboard, rules and `L1`–`L6` checks. It also adds `docs/llm-simulation.md` and
   `docs/troubleshooting.md` to the documentation set in Deliverable D above.

## References

- The LLM serving simulation brief (supersedes this file where they overlap): `prompt-llm-sim.md`
- fake-gpu-operator: https://github.com/run-ai/fake-gpu-operator
- NVIDIA DCGM Exporter Dashboard (12239): https://grafana.com/grafana/dashboards/12239-nvidia-dcgm-exporter-dashboard/
- NVIDIA dcgm-exporter (metric names / real-hardware path): https://github.com/NVIDIA/dcgm-exporter
- kube-prometheus-stack: https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack
- terraform-aws-modules/eks: https://github.com/terraform-aws-modules/terraform-aws-eks
- terraform-google-modules (GKE): https://github.com/terraform-google-modules/terraform-google-kubernetes-engine
