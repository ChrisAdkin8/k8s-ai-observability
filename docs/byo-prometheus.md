# Bring your own Prometheus

**You already run Prometheus, and you do not want a second monitoring stack installed over
the top of it.** That is the single most common way people arrive here: they imported
[25618](https://grafana.com/grafana/dashboards/25618) or
[25620](https://grafana.com/grafana/dashboards/25620) from the Grafana catalog, found the
panels blank for want of the `llm:*` recording rules, and followed the link back.

There are two routes, and they differ in what they ask of you:

| | |
|--|--|
| **this page** — `install.sh --skip-monitoring` | the script path. Applies the rules, boards, simulators and workloads with `kubectl`, and never touches your monitoring stack |
| [the Helm chart](../charts/k8s-ai-observability/README.md) | the same objects as a chart, with the two labels below as `values.yaml` entries and a `helm test` that checks them against your live cluster |

Both hit the same two silent failures, described below. The chart's `helm test` is the only
thing in this repo that can *verify* them against your Prometheus rather than just document
them, so prefer it if you are choosing fresh.

The chart is published to `ghcr.io` and needs no clone — `helm install rig
oci://ghcr.io/chrisadkin8/charts/k8s-ai-observability --version <v> --set releaseLabel=…`.
Building it locally with `task chart` is the *contributor* path, for testing a template
change. (⚠️ The published copy lands with the next release tag; until then the local build
is the only one that exists. See the chart README.)

⚠️ **Whichever route you take, pass your release name to every script, not just one.**
`KPS_RELEASE` reaches `install.sh`, `verify.sh`, `grafana.sh` and `prometheus.sh` through
`config.sh`, and setting it for one of them leaves the others labelling and looking up the
wrong thing — `install.sh` will still exit 0 and print follow-up commands naming a release
your cluster does not have.

## The script path

The default `install.sh` installs `kube-prometheus-stack`. `--skip-monitoring` skips that
step and nothing else, so you get only the simulators, rules, dashboards and workloads:

```sh
./scripts/install.sh local --skip-monitoring
task local:install -- --skip-monitoring        # same thing through the front door
./scripts/verify.sh local --byo
```

**If your Helm release is not named `kube-prometheus-stack`, say so** — one variable
covers every script, because they all read it from `scripts/config.sh`:

```sh
export KPS_RELEASE=my-monitoring
./scripts/install.sh local --skip-monitoring
./scripts/verify.sh   local --byo
./scripts/grafana.sh  local                    # port-forwards svc/$KPS_RELEASE-grafana
```

## ⚠️ The two labels that fail with no error at all

Get either wrong and there is no scrape, no rule evaluation, empty boards — and every
object reports itself as successfully created:

| | Default | Set it with | If it is wrong |
|--|--|--|--|
| the `release:` selector on the two ServiceMonitors and two PrometheusRules | follows `KPS_RELEASE` | `RELEASE_LABEL` | your Prometheus never adopts them |
| the Grafana sidecar's discovery label | `grafana_dashboard=1` | `GRAFANA_DASHBOARD_LABEL` and `GRAFANA_DASHBOARD_LABEL_VALUE` | the boards are never imported |

The selector default is the upstream chart's: `ruleSelectorNilUsesHelmValues` and its two
siblings default to `true`, making the selector `release=<your release name>`. So you have
two possible fixes — set `RELEASE_LABEL` here, or set those three values `false` on your
side. The second is often not yours to change.

`verify.sh --byo` is what tells you. It still asserts everything about the simulators,
scrapes, rules and dashboards — those are exactly what a wrong label breaks — and relaxes
only the anonymous-Grafana claim, which follows from this repo's own Helm values rather
than from anything it installed. A board Grafana has never heard of stays a hard failure.

## One precondition, checked before anything is created

The monitoring stack must live in the `monitoring` namespace, and its Grafana sidecar must
watch that namespace. `install.sh` refuses up front, naming the fix, if the namespace or
the Prometheus Operator CRDs are missing — a refusal leaves the cluster untouched, which is
the property that matters when the alternative is a half-applied install.

## See also

- [docs/troubleshooting.md](troubleshooting.md) — the symptom table, including what an
  empty board means on each install path
- [docs/usage.md](usage.md) — driving the phases as scripts rather than through Task
- [charts/k8s-ai-observability/](../charts/k8s-ai-observability/README.md) — the chart, and
  where each invariant is caught
