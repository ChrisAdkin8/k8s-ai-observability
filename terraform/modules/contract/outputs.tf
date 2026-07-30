output "cluster_name" { value = var.cluster_name }
output "k8s_version" { value = var.k8s_version }
output "node_pool_label_key" { value = var.node_pool_label_key }
output "node_pool_name" { value = var.node_pool_name }

# The k8s node label map to stamp onto each cloud's node pool.
output "node_labels" {
  value = { (var.node_pool_label_key) = var.node_pool_name }
}
