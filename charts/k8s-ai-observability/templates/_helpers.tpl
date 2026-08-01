{{/*
Shared naming and labelling.

The `part-of` labels are NOT decoration. teardown.sh selects dashboard
ConfigMaps on `app.kubernetes.io/part-of=gpu-sim-dashboards` rather than on the
sidecar's `grafana_dashboard=1`, precisely so it removes OUR boards and not the
several kube-prometheus-stack ships under the same sidecar label. `helm uninstall`
is scoped by ownership rather than by label, so it does not have that trap — but
the labels are carried anyway, so a user's own cleanup can make the same
distinction, and so the two install paths produce identically-labelled objects.
*/}}

{{- define "k8s-ai-observability.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "k8s-ai-observability.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "k8s-ai-observability.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "k8s-ai-observability.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/*
The simulator image, with the tag defaulting to the chart's appVersion.

⚠️ THE DEFAULT IS THE COUPLING. appVersion tracks the repo's release tag, so
leaving llm.image.tag empty keeps the chart and the published image in step
automatically. Pinning it by hand is allowed and is exactly how it goes stale:
the chart installs cleanly and runs an OLD simulator, green with something
silently wrong. `task chart` cross-checks both against the release tag.
*/}}
{{- define "k8s-ai-observability.llmImage" -}}
{{- $tag := default .Chart.AppVersion .Values.llm.image.tag -}}
{{- printf "%s:%s" .Values.llm.image.repository $tag -}}
{{- end -}}

{{/*
One tenant's profile.json, built from values.

Templated rather than shipped as a file so the numbers in values.yaml are
genuinely reachable — a profile ConfigMap copied verbatim would freeze every one
of them at its default, which is the failure W-C3 calls out.

⚠️ JSON, not YAML: the simulator is standard-library-only and Python has no YAML
parser in stdlib. Built with toJson from a dict rather than by string
concatenation, so a value containing a quote cannot produce a file the simulator
rejects at startup — which it would survive (the last good profile is kept) and
report only as llmsim_profile_reload_errors_total.
*/}}
{{- define "k8s-ai-observability.profileJson" -}}
{{- $p := .profile -}}
{{- $t := .tenant -}}
{{- dict
      "model_name" $t.modelName
      "arrival_rate_rps" $t.arrivalRateRps
      "max_concurrency" (int $p.maxConcurrency)
      "max_in_flight" (int $p.maxInFlight)
      "prompt_tokens" (dict "mean" (int $p.promptTokens.mean) "stddev" (int $p.promptTokens.stddev))
      "generation_tokens" (dict "mean" (int $p.generationTokens.mean) "stddev" (int $p.generationTokens.stddev))
      "base_ttft_seconds" $p.baseTtftSeconds
      "base_itl_seconds" $p.baseItlSeconds
      "kv_cache_tokens_capacity" (int $p.kvCacheTokensCapacity)
      "prefix_cache_hit_rate" $t.prefixCacheHitRate
      "kv_block_tokens" (int $p.kvBlockTokens)
      "finish_reasons" (dict "stop" 0.90 "length" 0.09 "abort" 0.01)
      "seed" nil
    | toPrettyJson -}}
{{- end -}}
