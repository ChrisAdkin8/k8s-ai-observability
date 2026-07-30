output "cluster_name" {
  value = google_container_cluster.this.name
}

output "region" {
  value = var.region
}

# Phase 2: needs the gke-gcloud-auth-plugin binary installed locally.
output "configure_kubectl" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --region ${var.region} --project ${var.project}"
}

# Cross-checked against K8S_VERSION by scripts/config.sh.
output "k8s_version" {
  value = module.contract.k8s_version
}

output "gpu_sim_node_label" {
  value = "${module.contract.node_pool_label_key}=${module.contract.node_pool_name}"
}
