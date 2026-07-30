# Running it without Task

[Task](https://taskfile.dev/docs/installation) is a thin front door over `scripts/`, not a
replacement for it — every task shells out to the same script. This page is the script-level
equivalent of [Quick start](../README.md#quick-start), for when you would rather not install
Task, or want to see exactly what each phase runs.


Task only wraps them, and needs no extra tooling to skip. `scripts/` stays the source of truth
for install ordering, for guarding against the wrong kubecontext, and for the drift assertions
that abort the install when `config.sh` and the static manifests disagree.

Phase 2 onwards is identical on all three targets — substitute `local`, `eks` or `gke`:

```sh
./scripts/install.sh  <target>
./scripts/verify.sh   <target>
./scripts/grafana.sh  <target>             # one port-forward, both boards, localhost:3000
./scripts/teardown.sh <target> --destroy   # omit --destroy to keep the cluster
```

`--destroy` removes the cluster too: `kind delete` on local, `terraform destroy` on the clouds.

Only Phase 1 differs between targets:

```sh
# ---- local (kind) ---- preflights the container runtime, then creates the cluster
./scripts/kind-up.sh

# ---- EKS ----
cd terraform/eks && cp terraform.tfvars.example terraform.tfvars   # edit region
terraform init && terraform apply && cd ../..

# ---- GKE ---- (Standard, not Autopilot)
export GCP_PROJECT=my-project GCP_REGION=europe-west1
cp terraform/gke/terraform.tfvars.example terraform/gke/terraform.tfvars   # edit api_allowed_cidrs
. ./scripts/config.sh          # exports TF_VAR_project from GCP_PROJECT — see docs/gke.md
terraform -chdir=terraform/gke init && terraform -chdir=terraform/gke apply
```

GKE has first-time `gcloud` setup — login, ADC, the two APIs, and the
`gke-gcloud-auth-plugin` PATH trap — covered in [gke.md](gke.md#prerequisites).
