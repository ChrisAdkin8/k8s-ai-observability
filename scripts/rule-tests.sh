#!/usr/bin/env bash
# rule-tests.sh — unit-test the PromQL in manifests/alerts/ with promtool.
#
# NO CLUSTER, NO NETWORK, SECONDS. This is the check that matches what the repo
# actually claims: if the point is "validate GPU and LLM dashboards, alerts and SLOs
# cheaply", then the alert rules themselves have to be testable without standing
# anything up. scripts/verify.sh proves the rules fire against a live cluster, which
# takes ~6 minutes of polling and can only ever exercise the two alerts the shipped
# workloads happen to drive. This exercises every rule, including the thresholds
# nothing in the rig reaches, and the recording-rule arithmetic that a live check can
# only observe indirectly.
#
# WHY THE EXTRACTION STEP. manifests/alerts/*.yaml are PrometheusRule CUSTOM RESOURCES
# — apiVersion/kind/metadata wrapping a `spec:` — and promtool only reads plain
# Prometheus rule files (`groups:` at the top level). The alternative would be to keep
# rule files in the repo and generate the CRs from them, which is the cleaner design
# but changes what `kubectl apply -f manifests/alerts/` means; unwrapping on demand
# keeps the applied manifest the single source of truth.
#
# scripts/extract.sh does the unwrapping, and is shared with the compose stack rather
# than reimplemented here — two copies of the same transformation is how the rules
# promtool tests and the rules Prometheus actually loads start to differ. What is
# asserted below is that the extraction produced something: silently yielding nothing
# would make every test pass vacuously, the one failure mode a test harness must not have.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROMTOOL="${PROMTOOL:-promtool}"
if ! command -v "$PROMTOOL" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: 'promtool' not found on PATH.

promtool ships inside the Prometheus release archive — there is no separate
download, and nothing else in this repo needs it:

  macOS:  brew install prometheus
  Linux:  curl -fsSL https://github.com/prometheus/prometheus/releases/download/v3.7.3/prometheus-3.7.3.linux-amd64.tar.gz \
            | tar -xz --strip-components=1 -C ~/.local/bin prometheus-3.7.3.linux-amd64/promtool

Or point at one you already have:  PROMTOOL=/path/to/promtool ./scripts/rule-tests.sh
EOF
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> extracting rule files from the PrometheusRule manifests"
./scripts/extract.sh rules "$WORK" >/dev/null

shopt -s nullglob
produced=("$WORK"/*-rules.yaml)
if [[ ${#produced[@]} -eq 0 ]]; then
  echo "ERROR: scripts/extract.sh produced no rule files from manifests/alerts/." >&2
  exit 1
fi

for out in "${produced[@]}"; do
  # Assert the extraction actually produced rules. A restructured manifest (a `spec:`
  # that stops being top-level, a switch to multi-document YAML) would otherwise
  # yield an empty file that promtool happily reports as passing zero tests.
  if ! grep -q '^groups:' "$out"; then
    echo "ERROR: $(basename "$out") has no 'groups:' — is 'spec:' still top-level?" >&2
    exit 1
  fi
  if ! grep -qE '^\s+- (record|alert):' "$out"; then
    echo "ERROR: $(basename "$out") has no rules in it." >&2
    exit 1
  fi
  printf '    %-24s %s rules\n' "$(basename "$out")" \
    "$(grep -cE '^\s+- (record|alert):' "$out")"
done

# Lint before testing. `check rules` catches a malformed expression or a bad label
# template on its own, and its message points at the rule; the same fault seen first
# through `test rules` surfaces as a pile of unmet expectations instead.
echo "==> promtool check rules"
"$PROMTOOL" check rules "$WORK"/*-rules.yaml

# The test files reference their rule file by bare name (rule_files: [gpu-rules.yaml]),
# resolved relative to the test file — so both have to sit in the same directory.
cp tests/rules/*_test.yaml "$WORK/"

echo "==> promtool test rules"
"$PROMTOOL" test rules "$WORK"/*_test.yaml
