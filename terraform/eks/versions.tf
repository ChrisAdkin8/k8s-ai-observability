terraform {
  required_version = ">= 1.6"
  required_providers {
    # Floor at the version this config was actually validated against, so a fresh
    # init on another machine cannot silently resolve something older or skip ahead
    # a minor while .terraform.lock.hcl is untracked.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.55"
    }
  }
  # State: local by default (fine for an ephemeral test cluster). For a shared/remote
  # backend, uncomment and configure — and remember state holds sensitive data
  # (kubeconfig material). Example:
  # backend "s3" {
  #   bucket         = "my-tf-state"
  #   key            = "gpu-sim/eks.tfstate"
  #   region         = "eu-west-1"
  #   dynamodb_table = "my-tf-locks"
  # }
}

provider "aws" {
  region = var.region
}
