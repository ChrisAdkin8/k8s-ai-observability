# Single source of truth for the cross-cloud "contract" constants. Both the EKS and GKE
# roots consume this module so the GPU-sim node label (and cluster/k8s naming) cannot
# drift between clouds. These MUST also match scripts/config.sh and the fake-gpu-operator
# Helm values (topology.nodePoolLabelKey / nodePools.<name>).

# nullable = false is what makes these the REAL defaults rather than decorative
# ones. The roots default their own cluster_name/k8s_version to null and pass it
# straight through; with the Terraform >= 1.1 default of nullable = true, that null
# would be preserved and these values never used. With nullable = false, null falls
# back to here — so this file is the single place either value is defined, and an
# override is a deliberate act in a root's tfvars.
# Being the single source of truth means a bad value here fails in BOTH clouds and,
# for k8s_version, halfway through a node's first boot. These validations move that
# to plan time.

# Constrained by GKE, which is stricter than EKS: lowercase alphanumeric + hyphen,
# must start with a letter. The 25-char ceiling comes from the GCP service account
# built as "<cluster_name>-node", whose account_id is capped at 30.
variable "cluster_name" {
  type     = string
  default  = "gpu-sim"
  nullable = false

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]*[a-z0-9])?$", var.cluster_name)) && length(var.cluster_name) <= 25
    error_message = "cluster_name must be lowercase alphanumeric/hyphen, start with a letter, and be <= 25 chars (GKE + GCP service-account limits)."
  }
}

# MINOR only ("1.36"), never "v1.36" or "1.36.0". Both clouds reject the other
# spellings, and on EKS it also selects the node AMI via ami_type, so a change here
# rolls the node group as well as upgrading the control plane.
#
# This value AGES OUT, so it is maintenance rather than preference. Three targets have
# to accept the same minor, and GKE is the one that fails hard:
#   * GKE, used here WITHOUT a release channel, matches min_master_version against the
#     static version list only. A minor is dropped from that list while the EXTENDED
#     channel still offers it, and the create then fails with
#     `No valid versions with the prefix "<minor>"` — after the VPC, NAT and service
#     account are already built. terraform/gke moves that to plan time; the list is
#     `gcloud container get-server-config --region <region>`.
#   * EKS keeps aged-out minors creatable under extended support (extra cost per hour),
#     so it degrades quietly where GKE breaks: `aws eks describe-cluster-versions`.
#   * kind ties node images to kind RELEASES, so `local` needs a kindest/node:v<minor>.x
#     tag published with the kind binary in use (asserted by scripts/config.sh).
# 1.36 clears all three as of 2026-07: GKE static offers 1.36.2-gke.x, it is the EKS
# default (standard support to 2027-08), and kind v0.32.0 ships v1.36.1.
variable "k8s_version" {
  type     = string
  default  = "1.36"
  nullable = false

  validation {
    condition     = can(regex("^[0-9]+[.][0-9]+$", var.k8s_version))
    error_message = "k8s_version must be MAJOR.MINOR only, e.g. \"1.36\" — not \"v1.36\" or \"1.36.0\"."
  }
}

# A Kubernetes label key: optional DNS-1123 subdomain prefix, then a name segment.
# The quantifiers are bounded rather than open ({0,251} and {0,61}, each between a
# leading and trailing character) so the one regex enforces Kubernetes' 253-char
# prefix and 63-char name limits too. Without the length half, an over-long key
# passes plan and apply and is only rejected when kubelet self-registers it — a node
# that boots and never joins.
variable "node_pool_label_key" {
  type     = string
  default  = "run.ai/simulated-gpu-node-pool"
  nullable = false

  validation {
    condition     = can(regex("^([a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$", var.node_pool_label_key))
    error_message = "node_pool_label_key must be a valid Kubernetes label key: optional <=253-char DNS prefix, then a <=63-char name, e.g. \"example.com/pool\"."
  }
}

# A Kubernetes label VALUE, and also a YAML map key in the fake-operator Helm
# values (topology.nodePools.<name>) — scripts/config.sh asserts the two match.
variable "node_pool_name" {
  type     = string
  default  = "default"
  nullable = false

  validation {
    condition     = can(regex("^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$", var.node_pool_name)) && length(var.node_pool_name) <= 63
    error_message = "node_pool_name must be a valid Kubernetes label value (<= 63 chars, alphanumeric with - _ . inside)."
  }
}
