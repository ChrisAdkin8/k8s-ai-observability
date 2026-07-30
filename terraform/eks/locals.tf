locals {
  name = module.contract.cluster_name
  tags = { Project = "gpu-sim", ManagedBy = "terraform" }
}
