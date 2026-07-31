# tests

Everything here runs in **seconds, with no cluster and no network**.

| Run | Covers |
|--|--|
| `task rule-tests` (or `./scripts/rule-tests.sh`) | every recording rule and alert in `manifests/alerts/` |
| `task selftest` (or `python3 scripts/llm-sim.py --selftest`) | the simulator's Prometheus exposition — bucket monotonicity, `+Inf` consistency, `HELP`/`TYPE`, and that rendering `/metrics` observes nothing |
| `task drift-test` (or `python3 scripts/check-vllm-buckets.py --selftest`) | the matching rules of the weekly upstream drift check, against a fixture |

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
tests/fixtures/                    # committed inputs for the two Python selftests
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

## Fixtures

`tests/fixtures/` holds committed inputs for the Python selftests. They exist to pin a
case the thing being tested cannot demonstrate by running.

| File | Pins |
|--|--|
| `upstream-vllm-metric-names.txt` | a **stubbed** upstream metric set for `check-vllm-buckets.py --selftest`. Never update it to track upstream — the real file is the moving thing the drift check watches, and testing against it would make the test drift with its own input |
| `profile-no-prefix-cache.json` | a load profile with `prefix_cache_hit_rate` at `0.0`, so `llm-sim.py --selftest` can assert both prefix-cache counters are still **emitted**, with hits flat at zero. An absent series and a zero one are different things to a panel |

## promtool

It ships inside the Prometheus release archive; there is no separate download.

```sh
brew install prometheus                      # macOS
PROMTOOL=/path/to/promtool task rule-tests   # or point at one you already have
```

`task tools` reports whether it is on PATH. CI pins the version in
`.github/workflows/ci.yml` (`PROMETHEUS_VERSION`).

## ⚠️ Check a new expected value on amd64 before committing it

`histogram_quantile` does not return bit-identical values on every architecture. A test
that pins an interpolated percentile can pass on an Apple Silicon laptop and fail on CI's
`ubuntu-latest`, one ULP apart — this has already happened here, with `(1.0, 2.5]`
returning `2.4250000000000003` on arm64 and `2.425` on amd64. promtool compares exactly,
so whichever you pin, the other fails. The alert annotation moves too, flipping between
`2.42s` and `2.43s` under `%.2f`.

The mechanism is not established and the inline comment in `rules/llm-rules_test.yaml`
deliberately does not guess at one. The rule under test is unaffected — `> 2` is a
comparison, and both values are above 2. Only exact-equality assertions are.

So if you add or change an expected percentile, run it against a real linux/amd64
promtool as well. No cluster, and the emulation is the only thing that needs Docker:

```sh
mkdir -p /tmp/w && ./scripts/extract.sh rules /tmp/w && cp tests/rules/*_test.yaml /tmp/w/
docker run --rm --platform linux/amd64 -v "$PWD":/w -w /w \
  --entrypoint promtool prom/prometheus:v3.7.3 test rules /tmp/w/*_test.yaml
```

Expectations currently verified green on both: `0.09`, `0.099`, `0.0998`, `0.9875`,
`0.02425`, `4.875`, `78`.
