# EKS deployment

## Prerequisites

- AWS credentials with permission to create VPC/EKS/IAM (`aws sts get-caller-identity` works).
- `terraform`, `kubectl`, `helm`, `aws` CLI.
- Keep `region` (Terraform) in sync with `AWS_REGION` in `scripts/config.sh`.

## Phase 1 — infra (Terraform)

```sh
cd terraform/eks
cp terraform.tfvars.example terraform.tfvars   # set api_allowed_cidrs, region
terraform init
terraform apply
```

`api_allowed_cidrs` is the only value you must set — it has no default, so an unset value
fails the plan rather than leaving the API endpoint open. Use a VPN or office range; a
dynamic home IP will eventually lock you out.

Creates: a 2-AZ VPC (single NAT), an EKS control plane (k8s 1.36), and a **CPU-only**
managed node group (`t3.large` ×2, amd64) on the EKS-optimised AL2023 AMI, labelled
`run.ai/simulated-gpu-node-pool=default`. The cluster creator gets admin access so
Phase 2 `kubectl`/`helm` works.

Expect the first apply to take **~20 min**, of which the node group is the long pole.

Nodes are node-ready as they boot — EKS supplies the bootstrap, so there is no custom
user data to go wrong and nothing to install over the NAT gateway. `k8s_version` pins
the control plane only.

> **⚠️ There is no surge capacity.** `min = max = desired = node_count`, so anything
> that rolls the node group replaces one node at a time, with the monitoring stack
> below its sizing floor for the duration. That is fine for an ephemeral test cluster
> and worth knowing before you change `instance_type` or `k8s_version` on a live one.

This page is the *operator* view — run it, reach it, tear it down. For the module itself,
[`terraform/eks/README.md`](../terraform/eks/README.md) documents every input and output,
which files own what, and the load-bearing details to read before editing the Terraform.

## Phase 2 — apps

```sh
cd ../..
./scripts/install.sh eks     # sets kubeconfig context 'gpu-sim-eks', installs both stacks
./scripts/verify.sh eks
```

`install.sh` sets up the kubeconfig with the alias **`gpu-sim-eks`** and guards every
action on that context, so it can't touch another cluster.

## Access Grafana (private by default)

```sh
./scripts/grafana.sh eks     # one port-forward, opens BOTH boards:
                             #   GPU → http://localhost:3000/d/gpu-sim-dcgm
                             #   LLM → http://localhost:3000/d/llm-sim-overview
# or: task eks:grafana
```

Anonymous Viewer access, so no login to view. The equivalent by hand:

```sh
kubectl --context gpu-sim-eks -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
# admin password (only needed to EDIT):
kubectl --context gpu-sim-eks -n monitoring get secret kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

## Teardown

```sh
./scripts/teardown.sh eks            # remove stacks only
./scripts/teardown.sh eks --destroy  # + terraform destroy (VPC/EKS/IAM)
```

## Cost notes

- Billable while running: EKS control plane (hourly), 2× `t3.large`, 1× NAT gateway,
  EBS volumes. Use the **cheapest nodes that still meet the ~2 vCPU / 8 GB floor** — do
  not drop to `*.small` or Prometheus won't schedule.
- No GPU instances or GPU quota are used.
- **Always run `--destroy`** when finished; the NAT gateway and control plane bill even
  when idle.
