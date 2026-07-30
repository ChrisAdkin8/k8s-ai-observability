# EKS — CPU-only cluster for GPU simulation. No GPU node groups, no NVIDIA drivers.
# Phase 1 only: creates infra. Helm/manifests are Phase 2 (scripts/install.sh).
#
# Nodes run the EKS-optimised AL2023 AMI, selected by ami_type. Nothing is installed
# at first boot and EKS supplies the bootstrap, so the node group is as close to the
# module's defaults as this rig allows — the only additions are the GPU-sim node
# label, a larger root volume, and SSM for debugging.

module "contract" {
  source       = "../modules/contract"
  cluster_name = var.cluster_name
  k8s_version  = var.k8s_version
}

module "vpc" {
  source = "terraform-aws-modules/vpc/aws"
  # Patch-level pin. This built the VPC, subnets and the NAT gateway the node
  # bootstrap depends on for egress; a loose "~> 5.8" silently carried it 13 minors
  # forward during an unrelated upgrade. Minor bumps should be deliberate.
  version = "~> 5.21.0"

  name = "${local.name}-vpc"
  cidr = "10.0.0.0/16"

  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true # one NAT to keep test cost down
  enable_dns_hostnames = true

  tags = local.tags
}

module "eks" {
  source = "terraform-aws-modules/eks/aws"
  # Patch-level pin (>= 21.24.0, < 21.25.0), deliberately tighter than the usual
  # "~> 21.0". The addons map below is load-bearing with no plan-time signal when it
  # is wrong, and the v21 line has already renamed inputs within a major. Bump minors
  # by hand, re-reading that map each time.
  version = "~> 21.24.0"

  # v21 renamed these from cluster_name / cluster_version /
  # cluster_endpoint_public_access* — the old names are hard errors, not deprecations.
  name               = local.name
  kubernetes_version = module.contract.k8s_version

  endpoint_public_access                   = true
  endpoint_public_access_cidrs             = var.api_allowed_cidrs
  enable_cluster_creator_admin_permissions = true # so the operator's kubectl/helm works

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # ⚠️ LOAD-BEARING, with no plan-time signal when it is wrong. Module v21 hardcodes
  # bootstrap_self_managed_addons = false and var.addons defaults to null, so without
  # this map EKS installs no VPC CNI / kube-proxy / CoreDNS and the cluster comes up
  # with zero pods: nodes register, kubelet is healthy and labelled, and each sits
  # NotReady on "cni plugin not initialized" until the node group create gives up ~33
  # min later with NodeCreationFailure. Per-addon rationale sits on each entry below.
  addons = {
    # before_compute installs the CNI before any node exists — without it a node has
    # nothing to become Ready with. The module orders before_compute addons ahead of
    # the node group and everything else behind it. vpc-cni needs no IRSA/pod-identity
    # here: it authenticates via the node role's AmazonEKS_CNI_Policy, which the module
    # attaches. Left on its defaults (no prefix delegation), so the AMI's own bootstrap
    # computes max-pods from the standard eni-max-pods formula and the two agree.
    vpc-cni    = { before_compute = true }
    kube-proxy = { before_compute = true }

    # NOT before_compute: CoreDNS is a Deployment that needs a schedulable node, so
    # running it ahead of compute only leaves it Pending and the addon reports DEGRADED.
    #
    # preserve and resolve_conflicts_on_create cover the addon's delete/recreate cycle,
    # which is not hypothetical — any failed apply that rolls the node group triggers it:
    #   * preserve = false: the module default (true) orphans the CoreDNS Deployment,
    #     Service, ConfigMap, SA and PDB when the addon is deleted. That deadlocks a node
    #     roll — the orphaned PDB (maxUnavailable 1) allows 0 disruptions while its pods
    #     are unhealthy, so the drain can never evict them and the roll dies with
    #     PodEvictionFailure, the very roll that would have healed those pods.
    #   * OVERWRITE: lets the re-created addon adopt those objects instead of failing the
    #     conflict check (the module default is NONE).
    # Trade: removing coredns from this map now DELETES cluster DNS rather than orphaning
    # it — right for an ephemeral test cluster, wrong for anything long-lived.
    coredns = {
      preserve                    = false
      resolve_conflicts_on_create = "OVERWRITE"
    }
  }

  eks_managed_node_groups = {
    cpu_sim = {
      instance_types = [var.instance_type]

      # No ami_id, so ami_type carries its normal meaning: it selects a real EKS AMI
      # and the module's matching user-data template (nodeadm, for the AL2023 line).
      # use_latest_ami_release_version and enable_bootstrap_user_data are left at
      # their module defaults on purpose — EKS injects the bootstrap into its own
      # AMIs, so there is nothing here to populate.
      ami_type = "AL2023_x86_64_STANDARD"

      min_size     = var.node_count
      max_size     = var.node_count
      desired_size = var.node_count

      # ⚠️ NOT `disk_size = 50`. The module honours disk_size only when
      # use_custom_launch_template = false, and it defaults to TRUE. Setting disk_size
      # here is accepted, silently ignored, and leaves nodes on the AMI's default
      # 20 GiB root — not enough for the monitoring stack's images plus its ephemeral
      # storage, and it surfaces as pods evicted under disk pressure rather than as
      # anything that names the volume.
      block_device_mappings = {
        root = {
          device_name = "/dev/xvda" # AL2023 x86_64 EKS-optimised root device
          ebs = {
            volume_size           = 50
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      # Private subnets and no key_name, so a node that misbehaves has no other way in.
      # Costs nothing and a node that cannot be inspected is a worse default.
      iam_role_additional_policies = {
        AmazonSSMManagedInstanceCore = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
      }

      # ⚠️ The GPU-sim node label — without this the fake operator fakes nothing.
      labels = module.contract.node_labels
      # NB: deliberately NO GPU taint, so the monitoring stack can also schedule here.
    }
  }

  tags = local.tags
}
