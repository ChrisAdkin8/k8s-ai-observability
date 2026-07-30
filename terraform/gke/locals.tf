locals {
  # Must not overlap the subnet or its secondary ranges in main.tf.
  master_cidr = "172.16.0.0/28"
}
