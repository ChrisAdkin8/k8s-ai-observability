# GKE STANDARD (not Autopilot) — CPU-only cluster for GPU simulation.
# Autopilot is deliberately NOT used: it blocks the hostPath mount
# (/var/lib/kubelet/device-plugins) the fake operator's device-plugin DaemonSet needs,
# and constrains node labels/DaemonSets, so the fake GPUs never register.

module "contract" {
  source       = "../modules/contract"
  cluster_name = var.cluster_name
  k8s_version  = var.k8s_version
}

# --- network ------------------------------------------------------------------
# Mirrors the EKS side: nodes have no external IPs and egress via a managed NAT.
# Both clouds therefore depend on that NAT for image pulls — losing it means pods
# cannot pull from the public registries this project uses (runai jfrog,
# prometheus-community, quay), not merely degraded connectivity.
resource "google_compute_network" "this" {
  name                    = "${module.contract.cluster_name}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "nodes" {
  name          = "${module.contract.cluster_name}-nodes"
  network       = google_compute_network.this.id
  region        = var.region
  ip_cidr_range = "10.10.0.0/16"

  # VPC-native (alias IP) needs named secondary ranges for pods and services.
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }

  # Lets private nodes reach Google APIs (Artifact Registry, logging) without
  # traversing the NAT.
  private_ip_google_access = true
}

resource "google_compute_router" "this" {
  name    = "${module.contract.cluster_name}-router"
  network = google_compute_network.this.id
  region  = var.region
}

resource "google_compute_router_nat" "this" {
  name                               = "${module.contract.cluster_name}-nat"
  router                             = google_compute_router.this.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# NB: no extra master->node firewall rule is needed. GKE auto-creates rules for
# 443 and 10250, and kube-prometheus-stack's admission webhook targets
# prometheusOperator.tls.internalPort, which defaults to 10250 — already covered.
# If a chart is ever added whose webhook listens elsewhere, that is when a rule
# from local.master_cidr becomes necessary.

# Dedicated least-privilege node identity. Without this, node_config falls back to
# the project's default Compute Engine service account, which carries roles/editor
# unless an org policy removed the automatic grant — i.e. every node, and every pod
# able to reach the metadata server, would hold Editor on the whole project.
# These five roles are the documented minimum for a functioning GKE node.
resource "google_service_account" "node" {
  account_id   = "${module.contract.cluster_name}-node"
  display_name = "GKE node identity for ${module.contract.cluster_name}"
}

resource "google_project_iam_member" "node" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])

  project = var.project
  role    = each.value
  member  = "serviceAccount:${google_service_account.node.email}"
}

# The static (no-release-channel) version list for this region, filtered to the
# contract's minor. Read purely so the cluster's precondition below can fail at PLAN
# time when that minor has aged out of the list — see the comment on min_master_version.
data "google_container_engine_versions" "static" {
  location       = var.region
  version_prefix = "${module.contract.k8s_version}."
}

resource "google_container_cluster" "this" {
  name     = module.contract.cluster_name
  location = var.region # regional cluster

  # Standard mode: enable_autopilot is intentionally omitted/false.
  remove_default_node_pool = true
  initial_node_count       = 1

  # Statically versioned (no release channel). `min_master_version` accepts a "1.36"-style
  # prefix and GKE resolves the patch, but it matches ONLY the static version list — not
  # the release channels. GKE retires a minor from that list while EXTENDED still carries
  # it, so a value that worked last quarter fails with
  #   Error 400: No valid versions with the prefix "<minor>" found
  # at cluster-create time, i.e. after the VPC, NAT, subnet and service account exist.
  # The precondition below turns that into a plan-time failure instead.
  min_master_version  = module.contract.k8s_version
  deletion_protection = false # allow `terraform destroy`

  lifecycle {
    precondition {
      condition     = length(data.google_container_engine_versions.static.valid_master_versions) > 0
      error_message = "GKE in ${var.region} offers no static (no-release-channel) version for Kubernetes ${module.contract.k8s_version}; the minor has aged out. Pick one that is listed by `gcloud container get-server-config --region ${var.region} --format='value(validMasterVersions)'` and bump k8s_version in terraform/modules/contract, keeping scripts/config.sh (K8S_VERSION) and kind/gpu-sim.yaml (node image) on the same minor."
    }
  }

  network         = google_compute_network.this.id
  subnetwork      = google_compute_subnetwork.nodes.id
  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Nodes get no external IPs. The endpoint stays public but is reachable only
  # from var.api_allowed_cidrs — making it private instead would need a bastion or
  # VPN before Phase 2's kubectl/helm could run at all.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = local.master_cidr
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.api_allowed_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "operator"
      }
    }
  }

  # Stops pods reading the node service account's token from the metadata server
  # (paired with workload_metadata_config = GKE_METADATA on the node pool). Nothing
  # in this stack — fake-gpu-operator, DCGM exporter, kube-prometheus-stack — needs
  # GCP API access, so no Workload Identity bindings are required for it to work.
  workload_identity_config {
    workload_pool = "${var.project}.svc.id.goog"
  }
}

resource "google_container_node_pool" "cpu_sim" {
  name     = "cpu-sim"
  cluster  = google_container_cluster.this.name
  location = var.region

  # Referencing the SA's email creates a dependency on the account but NOT on its
  # role bindings, so Terraform would otherwise build nodes in parallel with them.
  # On top of that GCP IAM is eventually consistent. Nodes that come up before
  # logging.logWriter/artifactregistry.reader land fail to register or pull images,
  # and it presents as a flaky apply that succeeds on retry.
  depends_on = [google_project_iam_member.node]

  node_count = var.node_count # per zone

  node_config {
    machine_type = var.machine_type # amd64
    image_type   = "COS_CONTAINERD"
    disk_size_gb = 50

    # The SA is the real access control; scopes are a legacy mechanism and Google's
    # current guidance is cloud-platform + a least-privilege SA, rather than trying
    # to narrow scopes (which breaks things and grants nothing the SA lacks).
    service_account = google_service_account.node.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    # ⚠️ The GPU-sim node label — without this the fake operator fakes nothing.
    # (Kubernetes node labels; deliberately NO GPU taint.)
    labels = module.contract.node_labels
  }

  management {
    auto_repair  = true
    auto_upgrade = false
  }
}
