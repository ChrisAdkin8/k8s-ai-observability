terraform {
  required_version = ">= 1.6"
  required_providers {
    # Floor at the version this config was actually validated against, so a fresh
    # init on another machine cannot silently resolve something older or skip ahead
    # a minor while .terraform.lock.hcl is untracked.
    google = {
      source  = "hashicorp/google"
      version = "~> 7.40"
    }
  }
  # Local state by default (fine for an ephemeral test cluster). For remote state use a
  # gcs backend; remember state holds sensitive data. Example:
  # backend "gcs" {
  #   bucket = "my-tf-state"
  #   prefix = "gpu-sim/gke"
  # }
}

provider "google" {
  project = var.project
  region  = var.region
}
