#!/bin/sh
# extract.sh <rules|profiles> <out-dir> — pull the payloads out of the Kubernetes
# manifests so they can be used outside Kubernetes.
#
# WHY THIS EXISTS. Two things in manifests/ are really plain files that happen to be
# wrapped in a custom resource or a ConfigMap: the Prometheus rule groups, and the LLM
# load profiles. promtool wants the first as a rule file; the compose stack wants both
# as files on disk. Copying them into a second location would create exactly the drift
# this repo asserts against everywhere else, so they are unwrapped on demand instead and
# the applied manifest stays the single source of truth.
#
# POSIX sh, not bash, and no tools beyond awk. It runs in two places: on the host from
# scripts/rule-tests.sh, and inside a busybox container from compose/compose.yaml. That
# rules out bashisms and rules out yq — which would also be a dependency this repo does
# not otherwise have.
#
# The transformation is a DE-INDENT, not a YAML parse. That is only safe because the
# inputs are two files we own and whose shape is fixed; every extraction below is
# asserted non-empty by its caller, because silently producing nothing is the one
# failure mode that would make downstream checks pass vacuously.
set -eu

usage() { echo "usage: extract.sh <rules|profiles> <out-dir>" >&2; exit 2; }
[ $# -eq 2 ] || usage
what="$1"; out="$2"
cd "$(dirname "$0")/.."
mkdir -p "$out"

case "$what" in
  # PrometheusRule custom resource -> plain Prometheus rule file.
  # Everything under `spec:` is indented by exactly two spaces, so dropping the `spec:`
  # line and removing two leading spaces yields `groups:` at column 0. Relative
  # indentation survives, which is what keeps the `expr: >-` block scalars valid.
  # The print rule runs before the flag is set, so the `spec:` line is consumed.
  rules)
    for src in manifests/alerts/*.yaml; do
      base=$(basename "$src" .yaml); base=${base%-prometheusrule}
      awk 'f { sub(/^  /, ""); print } /^spec:[[:space:]]*$/ { f = 1 }' "$src" \
        > "$out/${base}-rules.yaml"
      echo "$out/${base}-rules.yaml"
    done
    ;;

  # ConfigMap -> the profile JSON each simulator polls. Named from the ConfigMap
  # (llm-profile-steady -> steady.json) rather than from the model_name inside, so the
  # compose file can reference a stable filename.
  profiles)
    awk -v out="$out" '
      /^  name: llm-profile-/ { cur = $2; sub(/^llm-profile-/, "", cur); next }
      /^  profile\.json: \|[[:space:]]*$/ { blk = 1; next }
      blk {
        if ($0 ~ /^    /)            { l = $0; sub(/^    /, "", l); print l > (out "/" cur ".json") }
        else if ($0 ~ /[^[:space:]]/) { blk = 0 }
      }
    ' manifests/llm/10-profiles.yaml
    for f in "$out"/*.json; do echo "$f"; done
    ;;

  *) usage ;;
esac
