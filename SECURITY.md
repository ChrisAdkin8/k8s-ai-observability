# Security policy

## ⚠️ This is a test rig, not production infrastructure

Read this before the reporting section, because most of what looks like a vulnerability
here is a deliberate demo choice — and the ones that are not are worth knowing about.

**Grafana runs with anonymous access.** The compose stack and the kind path both grant an
unauthenticated viewer role, because the point is that `docker compose up -d` gets you a
working board in a minute. `scripts/verify.sh` asserts it, and `--byo` relaxes that check
precisely because a real deployment will not have it.

**The simulator has no authentication and serves anything that asks.** It exposes a
Prometheus endpoint on 9401 and nothing else; it holds no data, reads no secrets, and its
entire state is a load profile.

**The Terraform creates real, chargeable cloud resources.** `terraform/eks` and
`terraform/gke` stand up real clusters. They are ephemeral by design — `teardown.sh`
exists for that reason — and they are not hardened for anything but a demo.

**None of it is intended to run in a production cluster**, alongside production workloads,
or on a network you do not control. If you install it into a shared cluster, the
`--skip-monitoring` path exists so it does not install a second Prometheus over yours, but
that is an operational courtesy rather than an isolation boundary.

## What is genuinely in scope

- The published container image `ghcr.io/chrisadkin8/vllm-metrics-sim` — it is built from
  `scripts/llm-sim.py` on a `python:3.12-slim` base, standard library only, no dependencies
  to audit beyond the base image itself.
- Anything in `scripts/` or `terraform/` that could damage or expose a cluster the user
  already has — for example the install path writing outside its own namespaces, or a
  credential reaching a log.
- A supply-chain issue in the workflows: they pin actions by full commit SHA, and
  Dependabot keeps those current.

## Reporting

Please **do not open a public issue** for a security report.

Use [private vulnerability reporting](https://github.com/ChrisAdkin8/k8s-ai-observability/security/advisories/new),
which is visible only to the maintainer. Include what you did, what happened, and which
path you were on (compose, kind, EKS, GKE, or the Helm chart) — the paths differ enough
that it matters.

This is a personal project maintained in spare time, so expect an acknowledgement within a
week rather than within hours. Fixes ship in the next release, and the changelog entry says
what changed and why.

## Supported versions

The latest release only. This is a rig on a fast-moving upstream — vLLM's metric surface
has already been resynced once — and back-porting to an older tag would mean maintaining a
claim about a vLLM that no longer exists.
