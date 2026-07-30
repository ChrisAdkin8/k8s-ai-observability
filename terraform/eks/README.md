# terraform/eks — Phase 1 EKS infrastructure

Creates the CPU-only EKS cluster the GPU-simulation stack runs on: a 2-AZ VPC, an EKS
control plane, and one managed node group on the EKS-optimised AL2023 AMI. No GPU
hardware, quota, or drivers are involved.

**To run it** (init → apply → Phase 2 install → verify → teardown), follow
[`docs/eks.md`](../../docs/eks.md) or the `*-eks` Makefile targets. This README is the
reference for editing *this Terraform root*: what the files are, the inputs and outputs,
and the handful of non-obvious settings that will silently break the cluster if changed
without care.

## Files

| File | Holds |
|------|-------|
| `versions.tf` | Terraform / provider version floors and pins; backend (local by default) |
| `variables.tf` | Inputs (see table below) |
| `data.tf` | AZ lookup |
| `locals.tf` | `name` and `tags` |
| `main.tf` | The `contract`, `vpc`, and `eks` modules — the whole cluster |
| `outputs.tf` | Outputs (see table below) |
| `terraform.tfvars` | Local overrides — **gitignored**; copy from `.example` |

Cross-cloud constants (cluster name, k8s version, the GPU-sim node label) live in
`../modules/contract`, not here, so EKS and GKE cannot drift. Don't restate them in
`terraform.tfvars`; override only to diverge on purpose.

## Inputs

| Variable | Default | Notes |
|----------|---------|-------|
| `api_allowed_cidrs` | **none — required** | CIDRs allowed to reach the API. Plan fails if unset; rejects `0.0.0.0/0`. Use a VPN/office range — a dynamic home IP will eventually lock you out. |
| `region` | `eu-west-1` | Keep in sync with `AWS_REGION` in `scripts/config.sh`. |
| `instance_type` | `t3.large` | **amd64 only** (fake-operator/DCGM images aren't multi-arch). Meets the ~2 vCPU / 8 GB monitoring floor. |
| `node_count` | `2` | Drives `min = max = desired`; there is no surge capacity during a roll. |
| `cluster_name` | `null` → contract (`gpu-sim`) | Override only to diverge from the contract. |
| `k8s_version` | `null` → contract (`1.36`) | Pins the control plane. |

## Outputs

| Output | Use |
|--------|-----|
| `cluster_name` | Cluster name. |
| `region` | Region. |
| `configure_kubectl` | Ready-made `aws eks update-kubeconfig …` command (alias `gpu-sim-eks`). |
| `k8s_version` | Cross-checked against `scripts/config.sh`. |
| `gpu_sim_node_label` | The `key=value` the fake operator selects on. |

## Load-bearing details (read before editing)

Both of these fail with **healthy-looking nodes and no plan-time signal**. Each is
documented inline at the setting it concerns, flagged `⚠️`.

- **`addons` map (`main.tf`)** — module v21 installs no self-managed addons, so without
  this map the cluster has *zero* pods and the node group fails after ~33 min. `vpc-cni`
  and `kube-proxy` are `before_compute`; `coredns` uses `preserve = false` +
  `OVERWRITE` so a failed apply can't orphan its objects and deadlock the next node roll.
- **`block_device_mappings` (`main.tf`)** — *not* `disk_size`. The module honours
  `disk_size` only when `use_custom_launch_template = false`, and it defaults to **true**.
  `disk_size` is accepted, silently ignored, and leaves nodes on the AMI's 20 GiB root —
  which surfaces later as pods evicted under disk pressure, naming neither the volume nor
  the setting.

## Maintainer notes

- **Bump module minors by hand.** The `eks` (`~> 21.24.0`) and `vpc` (`~> 5.21.0`) pins
  are deliberately patch-tight: the VPC module built the NAT gateway the cluster depends
  on for egress, and a loose constraint once carried it 13 minors forward during an
  unrelated upgrade.
- **Provider/module pins are authoritative in-file** (`versions.tf`, `main.tf`), not in
  the root README's version table — check the files if the two disagree.
