# Dashboards

Two boards, each a plain `.json` file — **one artefact, used three ways**:

| Used by | How |
|--|--|
| Kubernetes | `scripts/install.sh` wraps each file in a ConfigMap labelled `grafana_dashboard=1`, which the kube-prometheus-stack sidecar imports |
| [The compose path](../../compose/) | Grafana provisioning mounts this directory directly |
| grafana.com / any Grafana | import the file as-is — **Dashboards → New → Import → Upload JSON** |

Because there is only one copy, those three cannot disagree. Nothing is ever clicked
into place, and no egress to grafana.com is needed at install time.

| Board | File | uid | Covers |
|-------|------|-----|--------|
| GPU Simulation — DCGM Overview | `gpu-sim-dcgm.json` | `gpu-sim-dcgm` | GPU util / memory / temp / power |
| LLM Simulation — vLLM Serving Overview | `llm-sim-overview.json` | `llm-sim-overview` | First-token latency, throughput, queue depth, KV cache |

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
