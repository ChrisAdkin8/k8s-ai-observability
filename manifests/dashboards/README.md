# Dashboards

This repo ships **two** self-contained dashboards as sidecar ConfigMaps. Neither needs
egress to grafana.com, so both work air-gapped.

| Board | File | uid | Covers |
|-------|------|-----|--------|
| GPU Simulation — DCGM Overview | `dcgm-configmap.yaml` | `gpu-sim-dcgm` | GPU util / memory / temp / power |
| LLM Simulation — vLLM Serving Overview | `llm-configmap.yaml` | `llm-sim-overview` | First-token latency, throughput, queue depth, KV cache |

`scripts/config.sh` holds both (ConfigMap name, uid) pairs and `assert_dashboard_contract`
fails the install if either drifts from the JSON — the uid is what makes `/d/<uid>` a stable
deep link, so a mismatch produces a confident link to a Grafana 404.

The LLM board is documented in [`docs/llm-simulation.md`](../../docs/llm-simulation.md).
Everything below concerns the DCGM board.

---

The DCGM overview dashboard:
[`dcgm-configmap.yaml`](dcgm-configmap.yaml) (`GPU Simulation — DCGM Overview`, uid
`gpu-sim-dcgm`). It has four panels — GPU utilisation, memory %, temperature, power —
over the `DCGM_FI_DEV_*` series.

⚠️ **Temperature and power are derived series, not exporter output.** The fake
dcgm-exporter emits only `DCGM_FI_DEV_GPU_UTIL`, `_FB_USED` and `_FB_FREE`; recording
rules in [`../alerts/gpu-prometheusrule.yaml`](../alerts/gpu-prometheusrule.yaml)
synthesise `DCGM_FI_DEV_GPU_TEMP` and `_POWER_USAGE` from utilisation under their real
names (tagged `source="derived"`). Apply the dashboard without those rules and both
panels are blank. See [`docs/observability.md`](../../docs/observability.md#metrics).

Provisioning path: the ConfigMap is labelled `grafana_dashboard=1`, so the
kube-prometheus-stack Grafana **sidecar** (`grafana.sidecar.dashboards`, `searchNamespace:
ALL`) discovers and loads it. `scripts/install.sh` applies it; `scripts/verify.sh`
asserts it exists **by name** and that its core query returns data. No egress to
grafana.com is required (works air-gapped).

In Grafana: **Dashboards → GPU Simulation — DCGM Overview**.

**Datasource:** the panels bind to a datasource template variable that resolves to the
default Prometheus datasource (which kube-prometheus-stack provisions) on load. If panels
render "datasource not found" on first open, pick the Prometheus datasource in the
dashboard's variable dropdown once, or set the variable's `current` to it in the JSON.

## Swapping in the fuller upstream dashboard 12239

The community NVIDIA DCGM dashboard (grafana.com id **12239**) is richer but bare-metal
oriented (its PromQL expects labels like `gpu`, `UUID`, `Hostname`, `modelName`); verify
label compatibility with the fake operator's metrics before relying on it. To use it as a
sidecar ConfigMap too:

```sh
curl -sL https://grafana.com/api/dashboards/12239/revisions/2/download -o dcgm-12239.json
kubectl -n monitoring create configmap dcgm-12239 \
  --from-file=dcgm-12239.json \
  --dry-run=client -o yaml \
  | kubectl label --local -f - grafana_dashboard=1 -o yaml \
  > dcgm-12239-configmap.yaml
kubectl apply -f dcgm-12239-configmap.yaml
```

If 12239's panels render blank it's a label mismatch — either adjust the panel queries or
prefer a Kubernetes-oriented DCGM board. Note that 12239 also plots series the fake
exporter never emits (SM/memory clocks, PCIe throughput, ECC counters); the recording
rules cover temperature and power, but those panels will stay empty.

`scripts/verify.sh` check #4 asserts the shipped `dcgm-gpu-dashboard` ConfigMap plus that
`DCGM_FI_DEV_GPU_UTIL` has data (the scriptable proxy for "panels are non-empty"); check
#4c does the same for the two derived series, so a dropped recording rule fails the run
instead of silently blanking two panels.
