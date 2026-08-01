# Running it without Task

[Task](https://taskfile.dev/docs/installation) is a thin front door over `scripts/`, not a
replacement for it — every task shells out to the same script. This page is the script-level
equivalent of the README's [Install](../README.md#install) section, for when you would
rather not install Task, or want to see exactly what each phase runs.


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

Against a cluster that **already runs Prometheus**, skip the monitoring install and tell
the scripts what your release is called:

```sh
export KPS_RELEASE=my-monitoring            # covers all four scripts — they share config.sh
./scripts/install.sh <target> --skip-monitoring
./scripts/verify.sh  <target> --byo
./scripts/grafana.sh <target>
```

Both flags are positional and validated: an unrecognised second argument is rejected
rather than ignored, because a typo'd `--skip-monitoring` falling through would install a
second monitoring stack over the top of yours. `install.sh` refuses up front, naming the
fix, if the `monitoring` namespace or the Prometheus Operator CRDs are absent — and
creates nothing when it does. The two labels that decide whether a BYO install actually
works, and fail silently when wrong, are in
[docs/byo-prometheus.md](byo-prometheus.md).

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

## The cluster-agnostic escape hatch

`task load` and the other bare, unprefixed forms act on **whatever kubecontext is current**,
with none of the guards the prefixed tasks apply:

```sh
task load -- ramp        # drives the CURRENT context, whatever that is
```

Prefer `task local:load` / `eks:load` / `gke:load`. Those resolve the context for their
target first, so they cannot quietly drive a cluster you had forgotten you were pointed at.
The bare form exists for when you already know, and it is the only task in the repo that
takes your word for it.
