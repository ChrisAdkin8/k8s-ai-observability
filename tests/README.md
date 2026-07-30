# tests

Everything here runs in **seconds, with no cluster and no network**.

| Run | Covers |
|--|--|
| `task rule-tests` (or `./scripts/rule-tests.sh`) | every recording rule and alert in `manifests/alerts/` |
| `task selftest` (or `python3 scripts/llm-sim.py --selftest`) | the simulator's Prometheus exposition — bucket monotonicity, `+Inf` consistency, `HELP`/`TYPE`, and that rendering `/metrics` observes nothing |

`scripts/verify.sh` is the third check and a different kind of thing: it asserts the
whole path end to end against a running cluster, and takes about six minutes because
it waits out real `for:` durations.

## Why the rules are unit-tested at all

`verify.sh` can only observe the alerts the shipped workloads happen to drive —
`GPUHighUtilization` and `LLMHighTTFT` — and only from the firing side. It can never
show that a threshold *doesn't* fire one notch below, that `LLMKVCacheSaturated` works
at all (nothing on the rig reaches 90% KV cache), or that the derived temperature
curve has the right coefficients.

That gap matters more here than it would elsewhere. This repo's premise is that you can
validate GPU and LLM alerts cheaply; alert rules it does not itself test would be an odd
thing to ship alongside that claim.

## Layout

```
tests/rules/gpu-rules_test.yaml    # promtool tests for gpu-prometheusrule.yaml
tests/rules/llm-rules_test.yaml    # promtool tests for llm-prometheusrule.yaml
```

`scripts/rule-tests.sh` extracts the `spec:` block from each PrometheusRule custom
resource into a plain Prometheus rule file, drops it in a temp directory next to a copy
of the test file, then runs `promtool check rules` and `promtool test rules`. That is
why `rule_files:` in each test names a bare file rather than a path into `manifests/` —
and why the applied manifest stays the single source of truth for the rules rather than
being generated from something else.

## Adding a test

Add a case to the relevant file; there is nothing to register. Each test block declares
`input_series` (synthetic samples), then asserts with `promql_expr_test` (a recording
rule's value) or `alert_rule_test` (which alerts fire, with what labels and rendered
annotations).

Two conventions worth keeping:

- **Assert both sides of every threshold.** A rule that fires when it should is half the
  property; most alerting bugs are the other half.
- **Pick input values whose expected output you can check by hand.** Every histogram
  test here puts all of a tenant's observations inside one bucket, so the expected
  quantile is a two-term calculation rather than a number copied out of a passing run.

Then check the test can fail: change the threshold or a coefficient in
`manifests/alerts/`, re-run, confirm it goes red, and put it back. Two limitations are
already documented inline where they apply — the `clamp_min` divide-by-zero guard and
the `rate()` window are both invisible to these tests, and the comments say why.

## promtool

It ships inside the Prometheus release archive; there is no separate download.

```sh
brew install prometheus                      # macOS
PROMTOOL=/path/to/promtool task rule-tests   # or point at one you already have
```

`task tools` reports whether it is on PATH. CI pins the version in
`.github/workflows/ci.yml` (`PROMETHEUS_VERSION`).
