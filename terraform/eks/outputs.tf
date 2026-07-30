output "cluster_name" {
  value = module.eks.cluster_name
}

output "region" {
  value = var.region
}

# Phase 2 uses this to point kubectl/helm at the cluster with a known context alias.
output "configure_kubectl" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --alias gpu-sim-eks"
}

# Cross-checked against K8S_VERSION by scripts/config.sh.
output "k8s_version" {
  value = module.contract.k8s_version
}

output "gpu_sim_node_label" {
  value = "${module.contract.node_pool_label_key}=${module.contract.node_pool_name}"
}
