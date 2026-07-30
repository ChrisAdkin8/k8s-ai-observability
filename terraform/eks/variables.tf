variable "region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region. Keep in sync with AWS_REGION in scripts/config.sh."
}

variable "instance_type" {
  type        = string
  default     = "t3.large" # amd64, 2 vCPU / 8 GB — meets the sizing floor for the monitoring stack
  description = "amd64 only. Do NOT use Graviton (arm64) unless the fake-operator/DCGM images are multi-arch."
}

variable "node_count" {
  type    = number
  default = 2
}

# Deliberately has no default: an unset value fails the plan rather than silently
# leaving the API endpoint open to the internet. Prefer a VPN or office range —
# pinning a dynamic home IP will eventually lock you out of your own cluster.
# Kept identical to the GKE variable of the same name so both clouds behave alike.
variable "api_allowed_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach the Kubernetes API. Must be set explicitly."

  validation {
    condition     = length(var.api_allowed_cidrs) > 0 && !contains(var.api_allowed_cidrs, "0.0.0.0/0")
    error_message = "api_allowed_cidrs must be non-empty and must not be 0.0.0.0/0."
  }
}

# Contract overrides. Both default to null, which modules/contract resolves to ITS
# default — so the value lives in exactly one file for both clouds, and setting one
# here is a deliberate, per-root divergence rather than a second copy that silently
# drifts. Do not restore literal defaults here.
variable "cluster_name" {
  type    = string
  default = null
}

# Drives the control plane version, and through ami_type the EKS-optimised AMI the
# nodes boot — so bumping it rolls the node group.
variable "k8s_version" {
  type    = string
  default = null
}
