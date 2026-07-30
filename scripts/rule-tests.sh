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
# but changes what `kubectl apply -f manifests/alerts/` means; a 3-line extraction
# keeps the applied manifest the single source of truth.
#
# The extraction is a de-indent, not a YAML parse, because this repo has no YAML
# dependency at all (no pip, no yq) and adding one for a test harness would be a worse
# trade than a transformation whose input is two files we own. It is asserted below
# rather than assumed: an extraction that silently produced nothing would make every
# test vacuously pass, which is the one failure mode a test harness must not have.
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

# PrometheusRule -> plain Prometheus rule file.
#
# Everything under `spec:` is indented by exactly two spaces, so dropping the `spec:`
# line and removing two leading spaces from every line after it yields `groups:` at
# column 0. Relative indentation is preserved, which is what keeps the `expr: >-`
# block scalars valid.
#
# The awk rules are ordered deliberately: the print rule runs before the one that sets
# the flag, so the `spec:` line itself is consumed rather than emitted.
extract() {
  awk 'f { sub(/^  /, ""); print } /^spec:[[:space:]]*$/ { f = 1 }' "$1"
}

echo "==> extracting rule files from the PrometheusRule manifests"
for src in manifests/alerts/*.yaml; do
  base="$(basename "$src" .yaml)"
  base="${base%-prometheusrule}"          # gpu-prometheusrule.yaml -> gpu
  out="$WORK/${base}-rules.yaml"
  extract "$src" > "$out"

  # Assert the extraction actually produced rules. A restructured manifest (a `spec:`
  # that stops being top-level, a switch to multi-document YAML) would otherwise
  # yield an empty file that promtool happily reports as passing zero tests.
  if ! grep -q '^groups:' "$out"; then
    echo "ERROR: $src produced no 'groups:' — is 'spec:' still top-level?" >&2
    exit 1
  fi
  if ! grep -qE '^\s+- (record|alert):' "$out"; then
    echo "ERROR: $src produced a rule file with no rules in it." >&2
    exit 1
  fi
  printf '    %-40s -> %s (%s rules)\n' "$src" "$(basename "$out")" \
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
