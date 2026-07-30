## k8s-gpu-simulation — convenience targets.
## Two-phase: `tf-apply-<cloud>` (infra) then `install-<cloud>` (apps).
##
## NOTE: Taskfile.yml now covers the same ground with less duplication (preconditions,
## a destroy prompt, `task eks:up`). This Makefile is kept for anyone without Task
## installed — both are thin wrappers over scripts/, so neither owns any logic. If you
## standardise on one, delete the other rather than maintaining both.
.PHONY: help selftest rule-tests compose compose-down up-local \
        cluster-local install-local verify-local grafana-local prom-local teardown-local destroy-local \
        tf-init-eks tf-apply-eks install-eks verify-eks grafana-eks prom-eks teardown-eks destroy-eks \
        tf-init-gke tf-apply-gke install-gke verify-gke grafana-gke prom-gke teardown-gke destroy-gke

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

selftest:     ## validate the LLM simulator locally (no cluster needed)
	python3 scripts/llm-sim.py --selftest

rule-tests:   ## unit-test the alert + recording rules with promtool (no cluster needed)
	./scripts/rule-tests.sh

compose:      ## both dashboards without Kubernetes — localhost:3000 (see compose/README.md)
	cd compose && docker compose up -d
compose-down: ## stop the no-Kubernetes stack
	cd compose && docker compose down

## --- local (kind) — no cloud account, no credentials, no spend ---
up-local:       ## ONE SHOT: kind cluster -> stacks -> acceptance checks
	./scripts/kind-up.sh && ./scripts/install.sh local && ./scripts/verify.sh local
cluster-local:  ## create the kind cluster only — Phase 1
	./scripts/kind-up.sh
install-local:  ## deploy stacks (local) — Phase 2
	./scripts/install.sh local
verify-local:   ## run acceptance checks (local)
	./scripts/verify.sh local
grafana-local:  ## open the GPU + LLM dashboards (local) — holds a port-forward
	./scripts/grafana.sh local
prom-local:     ## open the Prometheus console (local) — holds a port-forward
	./scripts/prometheus.sh local
teardown-local: ## remove stacks only (local)
	./scripts/teardown.sh local
destroy-local:  ## remove stacks + delete the kind cluster
	./scripts/teardown.sh local --destroy

## --- EKS ---
tf-init-eks:  ## terraform init (eks)
	terraform -chdir=terraform/eks init
tf-apply-eks: ## terraform apply (eks) — Phase 1
	terraform -chdir=terraform/eks apply
install-eks:  ## deploy stacks (eks) — Phase 2
	./scripts/install.sh eks
verify-eks:   ## run acceptance checks (eks)
	./scripts/verify.sh eks
grafana-eks:  ## open the GPU + LLM dashboards (eks) — holds a port-forward
	./scripts/grafana.sh eks
prom-eks:     ## open the Prometheus console (eks) — holds a port-forward
	./scripts/prometheus.sh eks
teardown-eks: ## remove stacks only (eks)
	./scripts/teardown.sh eks
destroy-eks:  ## remove stacks + terraform destroy (eks)
	./scripts/teardown.sh eks --destroy

## --- GKE (Standard) ---
tf-init-gke:  ## terraform init (gke)
	terraform -chdir=terraform/gke init
tf-apply-gke: ## terraform apply (gke) — Phase 1
	terraform -chdir=terraform/gke apply
install-gke:  ## deploy stacks (gke) — Phase 2
	./scripts/install.sh gke
verify-gke:   ## run acceptance checks (gke)
	./scripts/verify.sh gke
grafana-gke:  ## open the GPU + LLM dashboards (gke) — holds a port-forward
	./scripts/grafana.sh gke
prom-gke:     ## open the Prometheus console (gke) — holds a port-forward
	./scripts/prometheus.sh gke
teardown-gke: ## remove stacks only (gke)
	./scripts/teardown.sh gke
destroy-gke:  ## remove stacks + terraform destroy (gke)
	./scripts/teardown.sh gke --destroy
