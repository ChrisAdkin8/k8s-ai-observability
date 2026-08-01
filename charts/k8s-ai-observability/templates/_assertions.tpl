{{/*
═══════════════════════════════════════════════════════════════════════════════
THE SAFETY NET, PORTED.

scripts/install.sh runs five assertions BEFORE it creates anything —
assert_manifest_namespaces, assert_gpu_contract, assert_dashboard_contract,
assert_llm_contract, assert_terraform_contract. Every one of them exists because
breaking the invariant it guards produces A GREEN INSTALL WITH SOMETHING SILENTLY
WRONG: a dashboard whose filename and uid disagree, two LLM tenants sharing a
model_name, a node-pool label that matches nothing.

A `helm install` runs none of them. Reproducing that net is a first-class
requirement of this chart, not a nicety — a chart that installs cleanly and
produces an empty dashboard is worse than no chart, because the failure arrives
later and looks like the user's fault.

THE SPLIT, and why it falls where it does:

  RENDER-TIME (`fail`, below)   — anything knowable from the chart's own inputs
                                  and files. Caught by `helm template` and by
                                  `--dry-run`, before the cluster is touched.
  helm test (templates/tests/)  — anything that needs a live cluster: CRDs
                                  present, a node advertising nvidia.com/gpu,
                                  the selectors having actually matched.

CONTRIBUTING.md's invariants table is the list these are derived from; the chart
README maps each row to which half covers it, so the two cannot drift silently.

⚠️ `fail` aborts the whole render on the FIRST failure, so these are ordered
cheapest-and-most-fundamental first. Every message names the value to change,
not just the condition that broke — "assertion failed" sends people to the
template rather than to their `--set`.
═══════════════════════════════════════════════════════════════════════════════
*/}}
{{- define "k8s-ai-observability.assertions" -}}

{{/* ---- the LLM naming invariant (assert_llm_contract) ------------------- */}}
{{- if .Values.llm.enabled -}}
{{- $steady := .Values.llm.steady.modelName -}}
{{- $sat := .Values.llm.saturated.modelName -}}
{{- if not $steady -}}
{{- fail "llm.steady.modelName is empty. model_name is an IDENTITY here: the recording rules aggregate by (model_name) and every LLM panel breaks out on it." -}}
{{- end -}}
{{- if not $sat -}}
{{- fail "llm.saturated.modelName is empty. See llm.steady.modelName." -}}
{{- end -}}
{{- if eq $steady $sat -}}
{{- fail (printf "llm.steady.modelName and llm.saturated.modelName are both %q, and they MUST be distinct.\n\nThe recording rules aggregate by (model_name), so two tenants sharing a name merge into one series that describes neither — and the saturated tenant then drags the healthy one over the LLMHighTTFT threshold. The install would be green and the dashboard would show one meaningless line where it should show a healthy tenant beside a degraded one.\n\nSet --set llm.saturated.modelName=<something else>." $steady) -}}
{{- end -}}

{{/* The capacity arithmetic has to stay self-consistent, or the two tenants
     stop straddling the 2s alert threshold and the whole demonstration
     collapses into "both are fine" or "both are broken". This is the one
     assertion install.sh does NOT have — it cannot, because the profile
     ConfigMaps there are static files rather than computed from values. Making
     them templatable created the hazard, so it is checked here. */}}
{{- $p := .Values.llm.profile -}}
{{- if le (float64 $p.baseItlSeconds) 0.0 -}}
{{- fail "llm.profile.baseItlSeconds must be > 0." -}}
{{- end -}}
{{- $itlFull := mulf $p.baseItlSeconds 1.5 -}}
{{- $service := addf $p.baseTtftSeconds (mulf (float64 $p.generationTokens.mean) $itlFull) -}}
{{- $capacity := divf (float64 $p.maxConcurrency) $service -}}
{{- if ge (float64 .Values.llm.steady.arrivalRateRps) $capacity -}}
{{- fail (printf "llm.steady.arrivalRateRps is %v but the profile's modelled capacity is only %.2f rps.\n\nThe steady tenant must sit BELOW capacity or its queue runs away and it becomes indistinguishable from the saturated one — which is exactly what happened once before, when capacity was computed from the uncongested base_itl and read 4.08 rps instead of 2.74.\n\ncapacity = maxConcurrency / (baseTtftSeconds + generationTokens.mean x baseItlSeconds x 1.5)\n         = %v / (%v + %v x %v x 1.5)\n\nLower llm.steady.arrivalRateRps, or raise llm.profile.maxConcurrency." .Values.llm.steady.arrivalRateRps $capacity $p.maxConcurrency $p.baseTtftSeconds $p.generationTokens.mean $p.baseItlSeconds) -}}
{{- end -}}
{{- if le (float64 .Values.llm.saturated.arrivalRateRps) $capacity -}}
{{- fail (printf "llm.saturated.arrivalRateRps is %v but the profile's modelled capacity is %.2f rps, so this tenant is NOT saturated.\n\nIt would sit under the 2s LLMHighTTFT threshold alongside the steady tenant, the alert would never fire, and verify.sh L6 — which polls five minutes for it — would fail with nothing to point at.\n\nRaise llm.saturated.arrivalRateRps above %.2f." .Values.llm.saturated.arrivalRateRps $capacity $capacity) -}}
{{- end -}}

{{- if lt (int $p.maxInFlight) (int $p.maxConcurrency) -}}
{{- fail (printf "llm.profile.maxInFlight (%v) must be >= llm.profile.maxConcurrency (%v) — the simulator rejects the profile otherwise and keeps its built-in defaults, so your values would silently not apply." $p.maxInFlight $p.maxConcurrency) -}}
{{- end -}}

{{- range $name, $tenant := dict "steady" .Values.llm.steady "saturated" .Values.llm.saturated -}}
{{- $r := float64 $tenant.prefixCacheHitRate -}}
{{- if or (lt $r 0.0) (gt $r 1.0) -}}
{{- fail (printf "llm.%s.prefixCacheHitRate is %v; it is a RATE in 0.0-1.0, not a percentage. The simulator rejects the profile and keeps the previous one, so this fails as llmsim_profile_reload_errors_total rather than as anything visible." $name $tenant.prefixCacheHitRate) -}}
{{- end -}}
{{- end -}}

{{- if not .Values.llm.image.repository -}}
{{- fail "llm.image.repository is empty. The chart runs the simulator from a published image; there is no in-cluster build." -}}
{{- end -}}
{{- end -}}

{{/* ---- the dashboard contract (assert_dashboard_contract) --------------- */}}
{{/* Two independent things, both of which produce a confident link to a
     Grafana 404 rather than an error:
       * the board must PARSE as JSON at all — an unparseable one breaks the
         install mid-apply, which is why install.sh checks it too;
       * the FILENAME must equal the `uid` inside it, because /d/<uid> is built
         from one and served from the other. */}}
{{- if .Values.dashboards.enabled -}}
{{- $boards := .Files.Glob "files/dashboards/*.json" -}}
{{- if not $boards -}}
{{- fail "No dashboards found under files/dashboards/.\n\nThis chart is ASSEMBLED rather than installed from the repo directly: the dashboards and rules live in manifests/ and are copied in by `task chart`, so that no second copy of them is ever committed. Install the BUILT chart:\n\n    task chart\n    helm install rig dist/charts/k8s-ai-observability\n\nSee the chart README's note on the single-source-of-truth constraint. Set dashboards.enabled=false if you genuinely want none." -}}
{{- end -}}
{{- range $path, $_ := $boards -}}
{{- $uid := base $path | trimSuffix ".json" -}}
{{- $doc := $.Files.Get $path | fromJson -}}
{{/* ⚠️ Two failure modes, and they need DIFFERENT messages. `fromJson` does not
     raise on malformed input — it yields something with no keys — so a bare
     `ne $doc.uid $uid` reports an unparseable board as a *uid mismatch* against
     "<nil>", which sends the reader to rename a file when the real fault is a
     truncated download or a bad hand-edit. Checked as parse-then-compare. */}}
{{- if or (not (kindIs "map" $doc)) (not (hasKey $doc "uid")) -}}
{{- fail (printf "%s is not usable as a dashboard: it either does not parse as JSON, or has no top-level \"uid\".\n\nEvery .json under files/dashboards/ is wrapped into a dashboard ConfigMap, so a malformed one fails the apply PARTWAY THROUGH — after the namespace and some of the objects already exist. That is why it is caught at render time rather than by kubectl.\n\nThis is the same check assert_dashboard_contract makes for the script install path." $path) -}}
{{- end -}}
{{- if ne (toString $doc.uid) $uid -}}
{{- fail (printf "%s declares \"uid\": %q, but its FILENAME says %q.\n\nThe two are read independently: Grafana serves /d/<uid> from the JSON, while every link to the board is built from the filename. A mismatch is a confident link to a Grafana 404 — the dashboard is fine, the advertised way in is not.\n\nRename the file, or change the uid inside it. Not neither." $path (toString $doc.uid) $uid) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* ---- the rules are present and non-empty ----------------------------- */}}
{{/* Silently producing nothing is the one failure a build step must not have:
     the chart would install, every object would report success, and not a
     single recording rule would exist. */}}
{{- if .Values.rules.enabled -}}
{{- $rules := .Files.Glob "files/rules/*-rules.yaml" -}}
{{- if not $rules -}}
{{- fail "No rule files found under files/rules/. Run `task chart` and install the built chart from dist/ — see the chart README." -}}
{{- end -}}
{{- range $path, $_ := $rules -}}
{{- if not (regexMatch "(?m)^groups:" ($.Files.Get $path)) -}}
{{- fail (printf "%s has no top-level `groups:`. The extraction from the PrometheusRule custom resource produced something unusable — is `spec:` still top-level in manifests/alerts/?" $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* ---- the three-way naming invariant (assert_gpu_contract) ------------- */}}
{{/* Helm cannot template a subchart's values from the parent's, so the label
     key exists in two places by necessity. The alternative to checking it is a
     green install with ZERO GPUs — the fake operator watching a pool no node is
     labelled for — which is the exact failure docs/architecture.md and
     terraform/modules/contract both warn about. */}}
{{- if .Values.fakeGpuOperator.enabled -}}
{{- $sub := index .Values "fake-gpu-operator" -}}
{{- if $sub -}}
{{- if $sub.topology -}}
{{- if ne $sub.topology.nodePoolLabelKey .Values.nodePoolLabelKey -}}
{{- fail (printf "THE THREE-WAY NAMING INVARIANT IS BROKEN.\n\n  nodePoolLabelKey                        = %q\n  fake-gpu-operator.topology.nodePoolLabelKey = %q\n\nThey must be identical. The fake operator would watch a label key no node carries, so nothing advertises nvidia.com/gpu: a GREEN INSTALL WITH ZERO GPUS, every sample workload Pending, and the GPU board blank.\n\nSee docs/architecture.md#the-naming-invariant-read-before-editing." .Values.nodePoolLabelKey $sub.topology.nodePoolLabelKey) -}}
{{- end -}}
{{- if not (hasKey $sub.topology.nodePools .Values.nodePoolName) -}}
{{- fail (printf "THE THREE-WAY NAMING INVARIANT IS BROKEN.\n\n  nodePoolName = %q\n  fake-gpu-operator.topology.nodePools has keys: %v\n\nnodePoolName must equal one of those keys, AND equal the label VALUE on your nodes. Same failure as above: a green install with ZERO GPUs.\n\nA chart cannot label nodes, so this half is a PREREQUISITE:\n\n    kubectl label node <node> %s=%s" .Values.nodePoolName (keys $sub.topology.nodePools) .Values.nodePoolLabelKey .Values.nodePoolName) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* ---- the two silent selectors ---------------------------------------- */}}
{{/* These cannot be VALIDATED at render time — whether `releaseLabel` matches
     is a fact about someone else's Prometheus. What can be caught is the
     obviously-broken case, an empty value, which produces a label the chart
     cannot even express. Everything else is helm test's job, and the chart
     README says so rather than implying the render covers it. */}}
{{- if not .Values.releaseLabel -}}
{{- fail "releaseLabel is empty. It is the `release:` selector on the PrometheusRules and ServiceMonitors; with no value your Prometheus adopts none of them, the rules never evaluate and every derived panel is blank — with no error anywhere. Set it to your monitoring release's name." -}}
{{- end -}}
{{- if not .Values.grafana.dashboardLabel -}}
{{- fail "grafana.dashboardLabel is empty. It is the key the Grafana sidecar watches for dashboard ConfigMaps; with no value the boards are created and never imported, and /d/<uid> 404s — with no error anywhere." -}}
{{- end -}}

{{/* ---- namespaces ------------------------------------------------------ */}}
{{- range $k, $v := .Values.namespaces -}}
{{- if not $v -}}
{{- fail (printf "namespaces.%s is empty." $k) -}}
{{- end -}}
{{- end -}}

{{- end -}}
