variable "project" {
  type        = string
  description = "GCP project ID. Also export GCP_PROJECT for scripts/config.sh."
}

variable "region" {
  type        = string
  default     = "europe-west1"
  description = "GKE region (regional cluster). Keep in sync with GCP_REGION in scripts/config.sh."
}

# Deliberately has no default: an unset value fails the plan rather than silently
# leaving the control plane open to the internet. Prefer a VPN or office range —
# pinning a dynamic home IP will eventually lock you out of your own cluster.
variable "api_allowed_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach the Kubernetes API. Must be set explicitly."

  validation {
    condition     = length(var.api_allowed_cidrs) > 0 && !contains(var.api_allowed_cidrs, "0.0.0.0/0")
    error_message = "api_allowed_cidrs must be non-empty and must not be 0.0.0.0/0."
  }
}

variable "machine_type" {
  type        = string
  default     = "e2-standard-2" # amd64, 2 vCPU / 8 GB — meets the sizing floor
  description = "amd64 only. Avoid Arm (t2a) machine types unless images are multi-arch."
}

variable "node_count" {
  type        = number
  default     = 1
  description = "Nodes PER ZONE. A regional cluster spans ~3 zones, so 1 => ~3 nodes total."
}

# Contract overrides. Both default to null, which modules/contract resolves to ITS
# default — so the value lives in exactly one file for both clouds, and setting one
# here is a deliberate, per-root divergence rather than a second copy that silently
# drifts. Do not restore literal defaults here.
variable "cluster_name" {
  type    = string
  default = null
}

variable "k8s_version" {
  type    = string
  default = null
}
