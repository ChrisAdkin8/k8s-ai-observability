# GKE deployment

> **GKE Standard only — not Autopilot.** Autopilot blocks the hostPath mount the fake
> operator's device-plugin DaemonSet needs and constrains node labels/DaemonSets, so the
> simulated GPUs never register. The Terraform here creates a Standard cluster.

## Prerequisites

### Project and region

Everything below reads these, and so do the Phase-2 scripts — set them first:

```sh
export GCP_PROJECT=my-project GCP_REGION=europe-west1
# or, to avoid re-exporting each session: echo 'GCP_PROJECT=my-project' > .env
```

`GCP_REGION` defaults to `europe-west1` (`scripts/config.sh`); `GCP_PROJECT` has no default
and every GKE path fails loudly without it.

### Authenticate

```sh
brew install --cask gcloud-cli    # skip if gcloud is already on PATH
gcloud auth login --update-adc
gcloud config set project "$GCP_PROJECT"
gcloud auth application-default set-quota-project "$GCP_PROJECT"
gcloud services enable container.googleapis.com compute.googleapis.com
```

`--update-adc` does the work of `gcloud auth login` **and** `gcloud auth
application-default login` in one browser round trip. Both halves are needed: the CLI
login serves `gcloud`, and the Application Default Credentials serve Terraform.
`set-quota-project` silences the "active project does not match the quota project in your
local ADC file" warning that ADC otherwise emits on every call.

### `gke-gcloud-auth-plugin`

Phase-2 `kubectl` fails without it, and there is **no Homebrew cask** for it — it is a
gcloud component:

```sh
gcloud components install gke-gcloud-auth-plugin
```

On a Homebrew-installed SDK that alone is not enough. Homebrew symlinks only the core
binaries (`gcloud`, `bq`, `gsutil`, the credential helpers), so the plugin lands in the
SDK's own `bin` and never reaches your PATH. `kubectl` execs it **by name**, so it fails
as if the component were never installed:

```sh
echo "export PATH=$(brew --prefix)/share/google-cloud-sdk/bin:\$PATH" >> ~/.zshrc
export PATH="$(brew --prefix)/share/google-cloud-sdk/bin:$PATH"   # this shell too
```

Or symlink the one binary instead of putting the whole SDK `bin` on PATH — this takes
effect in every shell at once, with no profile edit and nothing to re-source:

```sh
ln -sfn "$(brew --prefix)"/share/google-cloud-sdk/bin/gke-gcloud-auth-plugin \
        "$(brew --prefix)"/bin/gke-gcloud-auth-plugin
```

The link deliberately points at the unversioned `share/google-cloud-sdk` path, not into
`Caskroom/gcloud-cli/<version>/`. `brew upgrade --cask gcloud-cli` replaces the versioned
directory and drops every component you added, so the plugin needs reinstalling after an
upgrade either way — but a link through the stable path starts resolving again by itself,
where one pinned to a version silently dangles.

Confirm with `task tools`, which checks the plugin resolves rather than merely that
`gcloud` exists — this is the miss it exists to catch.

## Phase 1 — infra (Terraform)

```sh
cp terraform/gke/terraform.tfvars.example terraform/gke/terraform.tfvars   # region / machine_type / api_allowed_cidrs
. ./scripts/config.sh                          # exports TF_VAR_project from GCP_PROJECT
terraform -chdir=terraform/gke init
terraform -chdir=terraform/gke apply
```

`project` is not a tfvars entry — `config.sh` maps `GCP_PROJECT` onto `TF_VAR_project` so
it is stated once and cannot drift from what Phase 2's `get-credentials` uses. Sourcing is
only needed for terraform by hand; `task gke:plan` / `task gke:apply` do it themselves.

Creates: a **Standard** regional GKE cluster (k8s 1.36) and a **CPU-only** node pool
(`e2-standard-2`, amd64) labelled `run.ai/simulated-gpu-node-pool=default`.
`node_count` is **per zone** — a regional cluster spans ~3 zones, so `1` ≈ 3 nodes.

## Phase 2 — apps

```sh
cd ../..
./scripts/install.sh gke     # sets kubeconfig context 'gpu-sim-gke', installs both stacks
./scripts/verify.sh gke
```

`install.sh` runs `get-credentials`, renames the context gcloud created to
**`gpu-sim-gke`**, and guards every action on it. `verify.sh`, `grafana.sh` and
`teardown.sh` each repeat that — `get-credentials` recreates its own
`gke_<project>_<region>_gpu-sim` entry and re-selects it every time it runs, so the alias
is re-applied rather than assumed to have survived.

## Access Grafana (private by default)

```sh
./scripts/grafana.sh gke     # one port-forward, opens BOTH boards:
                             #   GPU → http://localhost:3000/d/gpu-sim-dcgm
                             #   LLM → http://localhost:3000/d/llm-sim-overview
# or: task gke:grafana
```

Anonymous Viewer access, so no login to view. The equivalent by hand:

```sh
kubectl --context gpu-sim-gke -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
# admin password (only needed to EDIT):
kubectl --context gpu-sim-gke -n monitoring get secret kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

## Teardown

```sh
./scripts/teardown.sh gke            # remove stacks only
./scripts/teardown.sh gke --destroy  # + terraform destroy
```

## Cost notes

- Billable while running: GKE management fee (per cluster/hour beyond the free tier),
  ~3× `e2-standard-2`, boot disks. Regional control plane has no separate node charge but
  the nodes do.
- Use the **cheapest machine type that still meets the ~2 vCPU / 8 GB floor**.
- No GPU quota or GPU nodes are used. **Run `--destroy`** when finished.
