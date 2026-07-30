# Extra workloads (opt-in)

These are **not** applied by `scripts/install.sh` (it applies `manifests/workloads/`
non-recursively, so this subdirectory is skipped). Apply them yourself when you want a
richer / more dynamic GPU-workload mix:

```sh
kubectl apply -f manifests/workloads/extras/
```

| File | What it simulates | GPUs requested |
|------|-------------------|----------------|
| `gpu-driven.yaml` | Idle deployment that `scripts/drive-load.sh` drives through ramps/spikes | 1 |
| `gpu-multi-gpu.yaml` | Multi-GPU fan-out (2 replicas × 2 GPUs) — busier, multi-GPU-per-node view | 4 total |
| `gpu-batch-cronjob.yaml` | Recurring batch job: every 15 min a pod loads a GPU for ~5 min then completes | 1 (while running) |

## Capacity

The default set (`../gpu-workloads.yaml`: idle/steady/busy) uses 3 GPUs. These extras add
up to ~6 more. The fake topology advertises **8 GPUs per node**
(`helm/fake-gpu-operator/values.yaml`), so on a 2-node EKS (16) or 3-node GKE (24) cluster
everything fits comfortably. If you lower `gpuCount` or shrink the node pool, apply
selectively to avoid `Pending` pods.

## Driving a moving load curve

```sh
kubectl apply -f manifests/workloads/extras/gpu-driven.yaml
./scripts/drive-load.sh ramp        # staircase 0→95→0 on gpu-driven
./scripts/drive-load.sh spikes      # baseline/spike train
```

Watch it on the **GPU Simulation — DCGM Overview** dashboard, or in Prometheus:
`DCGM_FI_DEV_GPU_UTIL`.

## Trigger a batch run now (instead of waiting for the schedule)

```sh
kubectl create job --from=cronjob/gpu-batch gpu-batch-now -n default
```
