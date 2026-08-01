# Dashboards

Two boards, each a plain `.json` file — **one artefact, used four ways**:

| Used by | How |
|--|--|
| Kubernetes | `scripts/install.sh` wraps each file in a ConfigMap labelled `grafana_dashboard=1`, which the kube-prometheus-stack sidecar imports |
| [The compose path](../../compose/) | Grafana provisioning mounts this directory directly |
| Any running Grafana | import the file as-is — **Dashboards → New → Import → Upload JSON** |
| grafana.com catalog | **not** as-is: `task dashboards` derives the upload into `dist/` — see [Publishing](#publishing-to-grafanacom) |

Because there is only one copy, those four cannot disagree — the catalog upload is
derived from the same file rather than maintained beside it. Nothing is ever clicked
into place, and no egress to grafana.com is needed at install time.

| Board | File | uid | grafana.com id | Covers |
|-------|------|-----|--|--------|
| GPU Simulation — DCGM Overview | `gpu-sim-dcgm.json` | `gpu-sim-dcgm` | [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) | GPU util / memory / temp / power |
| LLM Simulation — vLLM Serving Overview | `llm-sim-overview.json` | `llm-sim-overview` | [25620](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/) | First-token latency, throughput, queue depth, KV cache, prefix-cache reuse |

**The filename is the uid.** `install.sh` derives the ConfigMap name from it
(`<uid>-dashboard`), and `scripts/config.sh` builds the `/d/<uid>` deep link that
`install.sh` and `grafana.sh` advertise. `assert_dashboard_contract` fails the install if
a filename and the `uid` inside it ever disagree — a mismatch would otherwise produce a
confident link to a Grafana 404. Rename the file and the uid together, or neither.

## Things about these boards that the JSON cannot tell you

JSON has no comments, so the reasoning lives here.

**⚠️ Temperature and power are derived series, not exporter output.** The fake
dcgm-exporter emits only `DCGM_FI_DEV_GPU_UTIL`, `_FB_USED` and `_FB_FREE`; recording
rules in [`../alerts/gpu-prometheusrule.yaml`](../alerts/gpu-prometheusrule.yaml)
synthesise `DCGM_FI_DEV_GPU_TEMP` and `_POWER_USAGE` from utilisation under their real
names (tagged `source="derived"`). **Import the GPU board without those rules and both
panels stay blank.** `install.sh` applies them together; the compose path loads the same
rules. See [`docs/observability.md`](../../docs/observability.md#metrics).

**The default window is 15m, not the usual 30m.** This rig is short-lived: a freshly
installed cluster has only minutes of history, and on a 30m window that history is
squeezed into the right-hand third with the rest of the canvas blank — which reads as
"the panels never populated" when the data was there all along. 15m still comfortably
covers the 5m recording-rule windows both boards query.

**Every LLM panel breaks out `by (model_name)`.** Showing the healthy and the saturated
tenant side by side *is* the point of running two simulators; a board that sums them away
discards it.

**The `llm:tokens:*_rate5m` panels under-read for the first five minutes** after install.
`rate()` over `[5m]` on a counter that started 90 seconds ago has only 90 seconds of
increase to divide by 300. Not a fault — see
[warm-up](../../docs/observability.md#warm-up-the-first-few-minutes-after-install).

**Datasource:** the panels bind to a datasource template variable that resolves to the
default Prometheus datasource on load. If panels render "datasource not found" on first
open, pick the Prometheus datasource in the dashboard's variable dropdown once.

## Publishing to grafana.com

**⚠️ The files in this directory cannot be uploaded to the catalog as-is.** They bind
every panel to a `datasource`-type template variable, which is right in-cluster — the
sidecar provisions the board and the variable resolves to whatever Prometheus is there.
The catalog wants the opposite: the `__inputs` block Grafana 3.0 introduced, so it can
prompt the importer. Upload a file without it and you get:

> Warning: Old dashboard JSON format. Read about Importing & Sharing with Grafana 2.x or 3.0

Grafana's own **Export for sharing externally** does not fix this, and the reason is worth
knowing: that exporter rewrites a *concrete* datasource uid into a placeholder. A
datasource variable has already abstracted the uid away, so there is nothing to rewrite
and no `__inputs` is emitted. The cleaner the file, the more certainly it is rejected.

So derive the upload instead:

```sh
task dashboards          # or: python3 scripts/dashboard-publish.py
```

That writes `dist/grafana-com/*.json`, adding `__inputs` and `__requires`, repointing every
`${datasource}` reference at `${DS_PROMETHEUS}` — 22 of them on the LLM board — and
dropping the now-redundant variable. `dist/` is generated and gitignored, on the same
terms as the ConfigMaps: one source of truth, several derived forms.

It also **fails rather than emitting a broken upload** if any panel carries a hardcoded
datasource uid, which is what a board edited in the Grafana UI and pasted back will do.
That check used to be a snippet in this file; running it inside the thing that consumes
the result means it cannot be skipped.

Both boards also work against **real** hardware, which is the point: the GPU board's
temperature and power panels read `DCGM_FI_DEV_GPU_TEMP` / `_POWER_USAGE`, which a genuine
dcgm-exporter emits directly (here they are recording rules), and the LLM board's `model`
variable is `label_values(vllm:num_requests_running, model_name)`, which real vLLM answers.

### The upload

Sign in at [grafana.com](https://grafana.com) → your org → **Dashboards** → upload the file
**from `dist/grafana-com/`**. There is **no API for this** that does not require your org
credentials, so it is a manual step by nature. Each needs a name, a description and the
datasource it expects (Prometheus). Ready to paste:

**`gpu-sim-dcgm.json`** — *GPU Simulation — DCGM Overview*

> Utilisation, memory, temperature and power across simulated NVIDIA GPUs, from
> DCGM-format metrics. Works against a real `dcgm-exporter` unchanged. Temperature and
> power are recording rules here rather than exporter output — import the rules from
> https://github.com/ChrisAdkin8/k8s-ai-observability or those two panels stay blank
> against a simulated source. Prompts for your Prometheus datasource on import.

For the catalog page's long-form description, paste
[`gpu-sim-dcgm.grafana-com.md`](gpu-sim-dcgm.grafana-com.md) — the same board written for
someone arriving with their own Prometheus and no knowledge of this repo, so every link in
it is absolute and the derived-series caveat is stated in full rather than referenced.

**`llm-sim-overview.json`** — *LLM Simulation — vLLM Serving Overview*

> Time to first token, inter-token latency, throughput, queue depth, KV-cache usage and
> prefix-cache reuse for vLLM, broken out `by (model_name)` so a saturated tenant is never
> averaged into a healthy one. Uses the **V1** engine's metric names
> (`vllm:kv_cache_usage_perc`, `vllm:inter_token_latency_seconds`,
> `vllm:prefix_cache_hits_total`). Prompts for your Prometheus datasource on import.

Long-form description: [`llm-sim-overview.grafana-com.md`](llm-sim-overview.grafana-com.md).
It carries more weight than the GPU one, because this board is the one that does **not**
import-and-go: several panels read `llm:*` recording rules and two more are simulator-only,
so the catalog page has to say which, and give the rules inline.

### ⚠️ Arrived from the catalog and found the panels blank?

That is the expected first experience, and it is not a fault in the board: the `llm:*` and
derived-DCGM series are **recording rules**, and importing a dashboard does not bring them.
Three ways to get them, cheapest first:

| | |
|--|--|
| paste the rules | both catalog pages give them inline — enough for a board you just want to look at |
| [the Helm chart](../../charts/k8s-ai-observability/README.md) | installs the rules, the simulators and the workloads **without touching your monitoring stack**. `task chart && helm install rig dist/charts/k8s-ai-observability --set releaseLabel=<yours>` |
| [`scripts/install.sh`](../../scripts/install.sh) | the whole rig, including its own kube-prometheus-stack unless you pass `--skip-monitoring` |

⚠️ Whichever you choose, the `release:` label on the `PrometheusRule` has to match what
your Prometheus selects on, and a mismatch is **silent** — the rules are created and never
evaluated. The chart's `helm test` is the thing that says so; see its README.

⚠️ **When a panel changes, the catalog does not update itself.** The board in this repo and
the board the catalog serves are two artefacts, and `task dashboards` only refreshes the
first — so a merged panel reaches nobody who imported by id until it is uploaded again.

Always re-submit as a **new revision of the existing id**, never as a new dashboard: a
second upload mints a second id, and everyone who already imported the first silently
stops receiving fixes. 25620's prefix-cache revision went up on 2026-07-31; both boards
are currently in step with the repo.

A logo each, 512×512, is in [`docs/logos/`](../../docs/logos/) — `gpu-sim-dcgm.png` and
`llm-sim-overview.png`, named for the boards they belong to. They are **generated**, not
drawn: `python3 docs/dashboard-logos.py` re-renders both from
[`docs/dashboard-logos.py`](../../docs/dashboard-logos.py), so the pair cannot drift into
mismatched marks and a tweak is a diff rather than a re-export. Needs `pillow`, like the
other two image scripts in `docs/`.

You get a numeric dashboard id back — **put it in the table at the top of this file**, and
in the README, so the published board and this repo stay connected.

**If you republish after editing**, upload a new revision rather than a new dashboard, so
the id people have imported keeps working.

## Editing a board

Edit in Grafana, then **Dashboard settings → JSON Model**, copy it back over the file
here, and keep the `uid` unchanged. Re-run `./scripts/install.sh <target>` (or restart
the compose stack) to load it.

Do not edit a board only in Grafana's UI — the ConfigMap is recreated from the file on
every install, so an unexported change is silently reverted.

## Swapping in the fuller upstream DCGM board (grafana.com id 12239)

Richer, but bare-metal oriented — its PromQL expects labels like `gpu`, `UUID`,
`Hostname`, `modelName`, so verify compatibility with the fake operator's metrics first.
Drop it in beside the others and `install.sh` will pick it up automatically:

```sh
curl -sL https://grafana.com/api/dashboards/12239/revisions/2/download \
  -o manifests/dashboards/dcgm-12239.json
```

Set its `uid` to match the filename, or `assert_dashboard_contract` will reject it. Note
that 12239 also plots series the fake exporter never emits (SM/memory clocks, PCIe
throughput, ECC counters); the recording rules cover temperature and power, but those
other panels stay empty.
