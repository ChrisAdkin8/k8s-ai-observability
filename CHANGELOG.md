# Changelog

Notable changes to this repo, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**What a version means here.** This is a rig, not a library — nothing imports it, so
"breaking" is about a cluster you already have rather than an API:

| | Breaks | Examples |
|--|--|--|
| **MAJOR** | an existing install, or a re-apply against it | the three-way naming invariant, Terraform input names or defaults, namespaces, dashboard `uid`s, metric or recording-rule names |
| **MINOR** | nothing | a new dashboard, alert, load profile, target or check |
| **PATCH** | nothing | fixes, docs, and pinned-version bumps that change none of the above |

While the version is `0.x` a breaking change bumps the **MINOR**, per semver's
initial-development rule — the table above applies as written from `1.0.0`. Either way
the migration steps are in the entry.

Pinned upstream versions live in [docs/versions.md](docs/versions.md); a bump there is
worth an entry below whenever it changes behaviour rather than just a number.

Comparison links are at the foot of this file, one per released version.

## [Unreleased]

## [0.4.0] — 2026-07-31

**This release exists because both dashboards got published.** Once 25618 and 25620 were
in the grafana.com catalog, people arrived with a Prometheus of their own, imported a
board, and found panels blank for want of the `llm:*` recording rules — with no route
forward that did not involve installing a second monitoring stack over their existing one.
`--skip-monitoring` and `verify --byo` are that route. Everything else here is the
supporting cast: the metric families the boards now claim, the drift checks that keep
those claims honest, and the CI legs that stop the alternative paths rotting.

### Added

- **A supported installation mode: `./scripts/install.sh <target> --skip-monitoring`,
  for a cluster that already runs Prometheus.**

  ⚠️ **This changes what a cluster looks like after install** — on that path nothing
  installs `kube-prometheus-stack`, and the simulators, rules, dashboards and workloads
  land beside whatever monitoring stack is already there.

  Both boards are published, so people arrive from the catalog with their own Prometheus,
  import 25620, find four panels blank for want of the `llm:*` recording rules — and their
  only route was a script that installs a second monitoring stack over the top of theirs.
  Nobody with a production stack will do that, which made it the single biggest structural
  blocker to using any of this.

  Three variables carry the mode, and **all three fail silently when wrong**: no scrape, no
  rule evaluation, empty boards, every object reporting itself as successfully created.

  | | Default | If it is wrong |
  |--|--|--|
  | `KPS_RELEASE` | `kube-prometheus-stack` | `grafana.sh` / `prometheus.sh` port-forward to a Service that does not exist |
  | `RELEASE_LABEL` | follows `KPS_RELEASE` | your Prometheus never adopts the two ServiceMonitors or the two PrometheusRules |
  | `GRAFANA_DASHBOARD_LABEL` / `_VALUE` | `grafana_dashboard=1` | the sidecar never imports either board |

  `KPS_RELEASE` was a plain assignment in `config.sh`, so the environment was silently
  overwritten at source time — setting it did nothing at all. It is a `:-` default now,
  which is the whole story in one line: all four scripts source that file.

  The `release:` label is rewritten at apply time with `kubectl label --local` rather than
  templated, so the manifests stay the single source of truth. The comments on both
  ServiceMonitors used to call that label "harmless; picked up regardless" — true of *this
  repo's* `values.yaml`, which sets the three `SelectorNilUsesHelmValues` flags `false`.
  Upstream defaults them `true`, where the selector is `release=<their release>`. Both
  comments now say which half they were relying on, and a BYO user is told both available
  fixes, since setting those values `false` is often not theirs to change.

  `install.sh` refuses up front if the `monitoring` namespace or the Prometheus Operator
  CRDs are absent, **creating nothing**, and the message names the exact `helm install`
  rather than leaving `kubectl` to report "CRD not found" and send people looking for a
  broken manifest. `LITE=1` warns that it is ignored on this path — it is an overlay on
  the values of the install being skipped, and a flag that silently does nothing is what
  this repo writes assertions against.

- **`./scripts/verify.sh <target> --byo`**, and `task <prefix>:install -- --skip-monitoring`
  / `:verify -- --byo` now that both tasks pass `CLI_ARGS` through. Without the
  passthrough the flag was accepted from the front door and **silently dropped**.

  The mode relaxes exactly one claim: anonymous Grafana access, which follows from this
  repo's Helm values rather than from anything it installed, so `401`/`403` becomes a SKIP.
  Everything about the simulators, scrapes, rules and dashboards is still asserted —
  skipping those would make `--byo` prove nothing on the install that most needs proof,
  since they are precisely what a wrong selector label breaks. **A `404` on a board stays
  fatal**, and says so in BYO terms: Grafana having never heard of it means the sidecar
  never imported the ConfigMap, which is the most likely way a BYO install appears broken.

- **A DCGM surface contract, asserted from both producers**
  (`tests/contracts/dcgm-surface.json`).

  The compose path has a second implementation of the fake exporter's surface and nothing
  compared it against the first — the parity was prose in `gpu-metrics-sim.py`'s header.
  A chart bump renaming a series or a label would fail the kind path loudly in CI and let
  the compose path drift in silence, which is backwards: `docker compose up -d` is the
  first command in the README.

  | Side | Assertion | Why |
  |--|--|--|
  | `compose/gpu-metrics-sim.py --selftest` | **exact** equality | nothing sits between the producer and the check |
  | `verify.sh` check 3b | **subset** | scraped series carry `job`, `instance`, `namespace`, `pod`, `endpoint`, `service` from the target, and the exporter's own pod labels arrive as `exported_*` — an exact match would fail on day one |

  Both semantics are in the contract file's header so the asymmetry reads as a decision.
  The selftest parses the **rendered exposition** rather than the module's constants:
  asserting the constants would only prove the file is self-consistent, and the thing that
  has to be right is what a Prometheus scrapes, label spelling included.

  ⚠️ The negative case is a permanent test, not an experiment. `--selftest` feeds
  `tests/fixtures/dcgm-surface-wrong.json` — wrong in three independent ways, one per
  direction the checker must detect — and asserts each fault is named in the rejection. A
  checker that only ever runs against the truth is one nobody has watched fail, and one
  that accepts everything also accepts the truth. Proving it by editing the real contract
  and reverting does not count: nothing enforces the revert.

  `render()` in the compose producer now takes the clock as a parameter, defaulting to wall
  time, for the same reason `llm-sim.py`'s does — it lets the selftest demand byte-identical
  output across two renders instead of comparing sine samples with a tolerance, which is
  also how it asserts that a scrape moves nothing.

- **CI covers the compose path**, in a `compose` job that needs docker rather than kind and
  takes about a minute. It is not a cheaper duplicate of the kind job: it exercises
  Prometheus loading the rules `scripts/extract.sh` unwraps out of `manifests/alerts/`,
  Grafana provisioning both boards off disk instead of through the sidecar, and the
  compose-only GPU producer. A break in any of those showed up nowhere before.

  It asserts through Prometheus rather than by curling the simulators — the simulator
  containers publish no ports, and querying proves the scrape works, which raw exposition
  would not. An **empty** target list fails too, since "nothing is down" is otherwise
  satisfied by nothing being scraped.

- **`python3 -m py_compile` over every `*.py` in the repo**, in the `fast` job. Until now
  the only Python CI executed was `llm-sim.py` via its selftest, so `dashboard-publish.py`,
  `check-vllm-buckets.py`, `gpu-metrics-sim.py` and the `docs/` scripts could ship a
  `SyntaxError` green — and two of those are release tooling, where the first person to
  find out is whoever is trying to cut a release.

### Fixed

- **The inter-token-latency caveat was wrong by roughly 2x, on a published board.** It said
  a full batch's 22.5 ms ITL falls in the `(25ms, 50ms]` bucket and reports ~43 ms. It
  falls in `(10ms, 25ms]` — `TPOT_BUCKETS` starts `[0.01, 0.025, 0.05, …]` — so the
  interpolation spans a 15 ms gap and reports ~24 ms, which is what `scripts/llm-sim.py`
  and the promtool expectation have said since the V1 bucket sync. The catalog page was
  written afterwards and copied the pre-correction numbers, and so did the panel
  `description` inside `llm-sim-overview.json` — the tooltip a grafana.com visitor reads,
  on a board whose entire point is that the caveat transfers to real hardware.

- **Three more vLLM series: `vllm:prefix_cache_queries_total`,
  `vllm:prefix_cache_hits_total` and `vllm:request_queue_time_seconds`.**

  ⚠️ **This changes what a cluster exposes after install.** Nothing existing moved and no
  panel or alert changes meaning, but the simulator pods emit more than they did, so a
  re-apply is what makes the new series appear. `install.sh` rebuilds the `llm-sim-script`
  ConfigMap; a *running* pod keeps the old mount, so restart the simulator Deployments
  (or let `install.sh` roll them) if the new series do not turn up.

  This closes the gap that most limited the repo's central claim. A real vLLM operator's
  dashboard has a prefix-cache hit-rate panel, and there was nothing here to build one
  against — the simulator emitted 10 of upstream's ~38 V1 metrics and none of them said
  anything about cache reuse or queue wait.

  Notes worth having before you use them:

  - **Counted in tokens, not requests.** A per-request counter gives a ratio that does not
    respond to prompt length, so a panel built here would behave differently against a
    real deployment. Hits are quantised to whole KV blocks (`kv_block_tokens`, default 16)
    the way upstream quantises them — a partial trailing block is never a hit.
  - **⚠️ A cache hit shortens NO latency here, by construction.** Prefill in this
    simulator is flat, not token-proportional, so there is no per-token work a cached
    block could remove and any speedup would be invented. `--selftest` asserts the TTFT
    histogram is *identical* across hit rates. Changing that is a real modelling change
    and re-derives the service time, the 2.74 rps capacity figure, both profiles, the 2s
    threshold, `verify.sh`'s L3b bound and every promtool expectation.
  - **Queue time is extracted, not re-derived.** The simulator already built TTFT as
    `queue_wait + prefill`; the histogram observes that first term at the same point TTFT
    is observed, so `ttft == queue_time + prefill` is an identity and the selftest asserts
    it on every completed request rather than comparing a p95 to a p95 with a tolerance.
  - **No new bucket constant.** Upstream declares one `request_latency_buckets` list and
    passes it to both `e2e_request_latency_seconds` and `request_queue_time_seconds`, and
    `E2E_BUCKETS` already *is* that list — so the existing drift-check entry watches these
    boundaries on behalf of both.

- **`prefix_cache_hit_rate` on the shipped profiles: 0.35 steady, 0.15 saturated.**
  ⚠️ Unlike `capacity_rps`, these are **derived from nothing**. They are chosen so the
  panel draws two distinguishable lines, the lower one on the saturated tenant because a
  server under eviction pressure reuses less — and `manifests/llm/10-profiles.yaml` says
  exactly that where it sets them, because an invented number presented as a modelled one
  is the failure those profile comments exist to prevent. Shipping non-zero is safe only
  because the hit rate is decoupled from latency: no existing series changes.

- **`--vllm-surface` now covers a metric RESHAPE, not just renames.** v0 exposed prefix
  caching as a gauge of a ratio (`vllm:gpu_prefix_cache_hit_rate`); V1 replaced it with
  two counters. A panel bound to the old name cannot be repaired by substituting a name —
  the replacement is `rate(hits)/rate(queries)`. Neither of the two renames the repo
  already shipped makes that point, and it is the sharpest upgrade-rehearsal case here.
  The 1:1 `METRIC_SURFACES` map cannot express one-gauge-to-two-counters, so there is now
  a `METRIC_RESHAPES` table beside it with the same positional `(v0, v1)` shape; the drift
  check reads both.

- **A prefix-cache hit-ratio panel on the vLLM board, and the recording rule behind it**,
  `llm:prefix_cache:hit_ratio5m` = `rate(hits) / rate(queries)` by tenant. Recorded once
  rather than repeated per panel, so a dashboard and an alert can never disagree about what
  the ratio means — the same reasoning as the quantile rules beside it.

  The denominator is clamped like `GPUHighMemoryUsage`'s, but at an **epsilon rather than
  at 1**. `clamp_min(x, 1)` suits a byte count and not a rate: a low-traffic real
  deployment can genuinely sit below one queried token per second, and flooring there would
  quietly under-report the tenants least likely to notice. An idle tenant reads `0/1e-9`,
  which is a flat line at zero rather than a `NaN`.

  promtool covers both sides of the ratio and the no-traffic case. Its expected values are
  **exact by construction rather than by luck** — the counters sit in 2:1 and 4:1 ratios,
  so the division is exact on any architecture, which sidesteps the amd64/arm64 percentile
  trap documented in `tests/`.

  ⚠️ **25620 needs re-submitting as a new revision.** The board is published, so the panel
  does not reach anyone who imported it until it is uploaded again — and it must go up as a
  revision of 25620, never as a new dashboard, or a second id is minted and everyone on the
  first stops receiving fixes.

- **A `verify.sh` L7.** The queue-time histogram is receiving observations and both
  prefix-cache counters are present, on a real cluster. Everything else covering these
  families is a selftest or a promtool assertion, and neither leaves the repo: the
  simulator proving it emits a series and Prometheus proving it receives one are different
  claims.

- **The weekly drift check now watches the metric SET, not just the bucket boundaries**
  (`scripts/check-vllm-buckets.py`). It ast-walks both files for string literals beginning
  `vllm:` and compares the sets, in the same structure-blind way the bucket check already
  worked.

  Buckets were only ever the narrower half of the risk that created that file. Two vLLM
  metrics were *renamed* by the V1 engine and this repo shipped the old spellings for two
  releases with every test green — a name we emit that upstream has dropped is a panel
  that goes blank against a real deployment, which is the same silent failure one level
  up. The check now covers it.

  The two directions are deliberately not treated alike:

  | | Means | Exit |
  |--|--|--|
  | We emit a name upstream no longer declares | drift — the case that cost two releases | **1** |
  | Upstream declares a name we do not emit | a gap, printed in full | **0** |

  Upstream declares ~38 `vllm:` metrics and this simulator emits 10, so the gap list is
  long by design and printing it is the point: it makes the distance **visible instead of
  silent**, which is what stops it growing back. Reddening a scheduled run every time vLLM
  adds a metric would just train everyone to ignore the job.

  ⚠️ The `_total` rule is the subtle part and it is now unit-tested rather than trusted.
  Upstream declares counters *without* the suffix and the Prometheus client appends it at
  exposition time, so our `vllm:prompt_tokens_total` is upstream's `vllm:prompt_tokens` —
  but `vllm:iteration_tokens_total` is declared *with* it, as a histogram. A blanket strip
  reports "in sync" on a name with no upstream counterpart at all, and the real run cannot
  reveal that because it prints a plausible answer either way. So the matching runs against
  a committed fixture (`tests/fixtures/upstream-vllm-metric-names.txt`) in the `fast` CI
  job, on every push, with no network:

  ```sh
  task drift-test    # python3 scripts/check-vllm-buckets.py --selftest
  ```

  The file keeps its name. It is cited by `Taskfile.yml`, `ci.yml`, `CONTRIBUTING.md`,
  `docs/versions.md` and `docs/llm-simulation.md`, and renaming it to touch five files
  buys a better noun and nothing else — the docstring is the specification. The CI job is
  renamed, since a job name costs nothing to move.

- **`CONTRIBUTING.md`.** The repo's invariants were already documented — scattered across
  `config.sh`, `manifests/dashboards/README.md`, `architecture.md` and several file
  headers. A contributor had to find them first. This collects the ones that, if broken,
  produce **a green install with something silently wrong** into one table: the
  filename-is-the-uid contract, the three-way naming invariant, what to re-verify before
  bumping either pinned chart, and what to re-derive after touching a bucket list. Also
  the traps that cost real debugging time here, including the architecture-dependent
  `histogram_quantile` result, and what is deliberately out of scope.

- **A `lite` leg in CI.** The `stack` job is now a matrix over `full` and `lite`, so the
  trimmed profile is exercised on every push rather than verified once by hand and left
  to rot at the next chart bump. `fail-fast: false` — if lite breaks while full passes,
  that difference is the signal.

  ⚠️ It proves the trimmed stack **functions**, not that it fits 3 GiB: the runner has
  ~16 GB and nothing there is under memory pressure. The sizing claim still needs a
  constrained local run and is still marked unverified.

- **`.github/dependabot.yml`** for the SHA-pinned actions. Pinning by SHA is right, but a
  SHA pin never moves on its own — and this repo has a live instance, with
  `actions/upload-artifact` warning on every run that it targets Node 20. It cannot cover
  the two Helm chart pins, which live in shell variables and stay a deliberate manual
  decision; the file says so and says why.

- **Both dashboards are published to the grafana.com catalog**: the GPU board is
  [25618](https://grafana.com/grafana/dashboards/25618) and the vLLM board is
  [25620](https://grafana.com/grafana/dashboards/25620). They can now be imported by id
  into any Grafana, without cloning this repo.

  The ids are recorded in three places on purpose, and all three are the ones the repo
  already told you to update: the table at the top of `manifests/dashboards/README.md`,
  `docs/versions.md`, and the README. A published board that cannot be traced back to the
  file it came from is how a catalog entry goes stale without anyone noticing.

  ⚠️ **Republish as a new revision, not a new dashboard.** A second upload would mint a
  second id, and the one people have already imported would quietly stop receiving fixes.

- **Publishing metadata for both dashboards**, ready to paste, in
  `manifests/dashboards/README.md`.

- **A catalog page for each board** — `manifests/dashboards/gpu-sim-dcgm.grafana-com.md`
  and `llm-sim-overview.grafana-com.md` — the long-form description to paste into the
  grafana.com listing, beside the one-paragraph blurbs above.

  Separate files rather than a section of the dashboards README, because the reader is
  different in a way that changes the writing: someone arriving from the catalog has their
  own Prometheus, no clone, and no reason to care what this rig is. So every link in them
  is **absolute**, and every caveat is stated in full rather than cross-referenced — the
  derived temperature/power rules are inlined on the GPU page rather than pointed at.

  ⚠️ The LLM page carries the heavier load, and that asymmetry is the reason both exist.
  The GPU board is import-and-go against a real `dcgm-exporter`. The LLM board is not:
  **four panels read `llm:*` recording rules and two more read simulator-only `llmsim_*`
  series**, so a bare import gives a mostly-empty board and no clue why. The rules are on
  the page ready to apply, with the two traps that come with them — `llm:tpot:p95_5m` must
  not be pointed at `vllm:request_time_per_output_token_seconds` (a per-request mean, not a
  per-token histogram), and `vllm:kv_cache_usage_perc` is a fraction, so `> 90` can never
  fire. Both fail by staying silent, which is why they are worth a stranger's screen space.

- **A logo for each board**, `docs/logos/*.png` at 512×512, generated by
  `docs/dashboard-logos.py` (needs `pillow`, like the other two image scripts in `docs/`).

  Generated rather than drawn for the reason the boards are files rather than clicks: it
  re-runs, it diffs, and the two marks cannot drift into a mismatched pair. Coordinates are
  fractions of the canvas, so changing one constant re-renders the same mark at a different
  size rather than a differently-proportioned one.

  The binding constraint is legibility at listing size, not detail at 512, and it decided
  the design: nothing thinner than 0.03 of the canvas, three utilisation bars rather than
  four (a fourth drops each under ~4px and they merge into a block), and no text, which
  turns to mud small and duplicates the title the catalog already prints. The tile is
  **solid dark rather than transparent** because grafana.com renders in both themes and a
  transparent mark disappears on one of them. Drawing is supersampled 4× and downsampled,
  since PIL has no antialiasing and a stepped diagonal is what a logo cannot afford.

  ⚠️ **Unverified: grafana.com's own logo requirements.** 512×512 square is an assumption,
  chosen because it downsamples cleanly; the upload form has not been checked for a stated
  dimension or file-size limit. If it wants something else, `SIZE` in the script is the
  whole edit.

- **`scripts/dashboard-publish.py` and `task dashboards`**, deriving the grafana.com
  upload into a gitignored `dist/`.

  The boards were documented as uploadable as-is. They are not: the catalog rejects them
  with *"Old dashboard JSON format. Read about Importing & Sharing with Grafana 2.x or
  3.0."* The cause is the design working correctly. Every panel binds to a
  `datasource`-type template variable, which is right in-cluster — the sidecar provisions
  the board and the variable resolves to whatever Prometheus is there. The catalog wants
  the opposite: the `__inputs` block Grafana 3.0 introduced, so it can prompt the importer.

  Grafana's own **Export for sharing externally** does not bridge this, and the reason is
  worth recording because it inverts the obvious intuition: that exporter works by
  rewriting a *concrete* datasource uid into a placeholder. A datasource variable has
  already abstracted the uid away, so it finds nothing to rewrite and emits no `__inputs`
  at all. **The cleaner the repo file, the more certainly the catalog rejects it.**

  The script adds `__inputs` and `__requires`, repoints every `${datasource}` reference at
  `${DS_PROMETHEUS}` — 22 of them on the LLM board, several inside query-variable
  definitions that are easy to miss by hand — and drops the now-redundant variable.
  `__requires` collects panel plugins from the file rather than hardcoding them, so a new
  panel type cannot be silently omitted.

  `dist/` is gitignored on the same terms as `compose/.generated/`: one source of truth,
  several derived forms, none committed.

  It also absorbs the portability check that was a copy-paste snippet in
  `manifests/dashboards/README.md`. Rather than reporting the three properties, it **fails
  and emits nothing** when a panel carries a hardcoded datasource uid — what editing in
  the Grafana UI and pasting the JSON Model back produces. Running it inside the thing
  that consumes the result means it cannot be skipped.

### Changed

- **Repository settings** (not in the tree, recorded here because nothing else would show
  it): Dependabot vulnerability alerts and automated security fixes enabled; GitHub
  Releases backfilled for `v0.1.0` and `v0.2.0`, which existed only as bare tags; and a
  ruleset on `main` blocking force-pushes and deletion and requiring both CI jobs. The
  ruleset carries an **admin bypass**, so direct pushes by the owner still work — it gates
  contributions, not the maintainer.

### Fixed

- **`verify.sh` checks 3 and 4c could fail on a slow runner**, and did — on a commit that
  changed only two markdown files, which is what made it obviously a timing fault rather
  than a real one. Check 3 gave up waiting for the first DCGM scrape at 60s;
  `DCGM_FI_DEV_GPU_TEMP` — a recording rule that *cannot exist* without the metric check 3
  had just declared missing — passed 13 seconds later. The run took 8m34s against a
  typical 5m.

  Both now poll for 120s, and share one `DCGM_POLL_ATTEMPTS` constant rather than two
  matching literals and a comment asking future editors to keep them aligned. That
  coupling is not cosmetic: 4c asserts something *derived* from what 3 asserts, so if 3
  has the shorter window a slow runner reports "the input is missing but the value
  computed from it is present" — which reads as a ServiceMonitor selector fault when it
  is nothing of the kind.

  Still bounded, deliberately: a genuine selector mismatch never resolves, so these must
  fail rather than hang. 120s is ~8 scrape intervals at the 15s the ServiceMonitors set.
  Check 4d keeps its own 60s and says why — by the time it runs, the metric is known to
  exist, so it is budgeting annotation propagation, not a first scrape.

## [0.3.0] — 2026-07-31

**The simulator had stopped matching real vLLM, and nothing here could tell.** Two metric
names and all three histogram bucket layouts had been superseded by the V1 engine while
every check in this repo stayed green — because every check reads the simulator, and the
simulator was perfectly consistent with itself. It was consistent with the wrong thing.

That is the failure this rig exists to prevent, so this release does three things rather
than one: resyncs the surface, re-derives every number that depended on it, and adds a
check that points *upstream* — the only kind that could have caught it. Also a trimmed
monitoring profile, for the laptops that cannot spare 8 GiB.

⚠️ **Breaking, by this file's own table** — it changes metric names, which under semver's
initial-development rule bumps the MINOR while the version is `0.x`. Re-run
`./scripts/install.sh <target>`, and see Fixed below for the two names to change in any
dashboard or alert of your own. `--vllm-surface both` is the quickest way to find them.

### Fixed

- ⚠️ **The simulated vLLM metric surface was two releases out of date, and failing
  silently.** `0.1.0` and `0.2.0` emitted the pre-V1 spellings of two series. Nothing
  broke, which is the whole problem: a renamed metric does not error, it just stops
  matching, so every test here stayed green while the repo's central claim — that what
  you build against the simulator transfers unchanged to a real vLLM deployment — had
  quietly stopped being true for these two.

  | Was | Now | Why upstream moved it |
  |--|--|--|
  | `vllm:gpu_cache_usage_perc` | `vllm:kv_cache_usage_perc` | V1 dropped CPU KV-cache offload, so `gpu_` distinguished nothing |
  | `vllm:time_per_output_token_seconds` | `vllm:inter_token_latency_seconds` | same measurement, clearer name |

  Every other series this rig emits — TTFT, e2e latency, the token counters,
  `request_success_total`, the running/waiting gauges — kept its name and is unchanged.

  Updated together, so none of them can disagree: the simulator, the
  `LLMKVCacheSaturated` alert, the `llm:tpot:p95_5m` recording rule, the LLM dashboard,
  the promtool tests and the docs. **The recorded rule names are deliberately NOT
  renamed** — `llm:tpot:p95_5m` stays, because `tpot` is what the measurement is called
  and renaming it would break every dashboard built on it for no gain.

  ⚠️ **Upgrading an existing install:** re-run `./scripts/install.sh <target>`. Any
  dashboard or alert of your own that references the two old names needs the same edit —
  `--vllm-surface both` below is the quickest way to find them.

- ⚠️ **The histogram bucket boundaries had drifted too — and that was the more dangerous
  half.** A wrong metric name fails loudly: the panel is blank and you go looking. A wrong
  bucket boundary fails quietly, because `histogram_quantile()` still returns a confident,
  plausible number that simply will not match real hardware.

  All three lists were the v0.6.x layout. `TTFT_BUCKETS` was the one that actually broke:

  ```
  both:  0.001 0.005 0.01 0.02 0.04 0.06 0.08 0.1 0.25 0.5 0.75 1.0 2.5 5.0 7.5 10.0
  v0.6:  │ 15   20   30   45   60   90  120
  V1:    │ 20   40   80  160  640 2560
  ```

  Sixteen identical boundaries, then nothing in common — and the saturated tenant sits at
  ~58s, inside the tail that had diverged. So the single number this rig exists to teach
  you to read was the number it got wrong. **The reported p95 moves 59.25 → 78 with the
  simulated latency completely unchanged**; only the resolution it is measured at moved.

  `TPOT_BUCKETS` was a strict *prefix* of V1's, so it was never wrong at the operating
  point — V1 only extended the tail. `E2E_BUCKETS` gained sub-second resolution
  (`0.3/0.5/0.8`) that the old list, starting at `1.0`, did not have at all.

  All three are now transcribed from `vllm/v1/metrics/loggers.py`. Knock-on changes, all
  re-derived rather than adjusted until green:

  - `tests/rules/llm-rules_test.yaml` fed `le="45.0"`/`le="60.0"`, boundaries V1 does not
    have. The saturated case now places its mass in `(40, 80]` and expects `78`. The
    steady tenant's boundaries (`0.08`, `0.1`) are identical in both layouts, so its three
    expectations are untouched — which makes them the control.
  - **The 2s `LLMHighTTFT` threshold survives, and is now pinned rather than assumed.** A
    new test block brackets it as tightly as can be done portably — `0.9875` below,
    `4.875` above.

    ⚠️ *Portably* is load-bearing there. The tighter test — a tenant inside `(1.0, 2.5]`,
    the bucket `2` actually falls in — **cannot be written**: arm64 and amd64 return
    values one ULP apart (`2.4250000000000003` vs `2.425`), promtool compares exactly, and
    the annotation flips between `2.42s` and `2.43s`. The mechanism is not established and
    is deliberately not guessed at in the comment. Every pinned percentile is now verified
    against a real linux/amd64 promtool as well as the local one;
    [tests/](tests/#-check-a-new-expected-value-on-amd64-before-committing-it) has the
    recipe and the list of values known green on both.
  - `verify.sh`'s L3b 120s bound still holds but no longer means what it did. 58s
    quantises to `78` in `(40, 80]`; the next bucket up interpolates to `152`, so the
    check jumps straight past 120 rather than degrading. It now reads "the queue wait has
    not escaped `(40, 80]`", and says so.

  ⚠️ **The screenshots in the README predate this** and show the saturated tenant at
  `1.20 mins` rather than 78s. Noted in place rather than silently left to disagree.

### Added

- **`--vllm-surface v1|v0|both`** on `scripts/llm-sim.py` (or `LLM_SIM_VLLM_SURFACE`),
  defaulting to `v1`. `both` emits the superseded v0 names alongside the current ones,
  which turns the rename above into something useful: point an existing dashboard at a
  `both` simulator and every panel still bound to a v0 name is precisely the set your
  engine upgrade will break. `METRIC_SURFACES` in that file is the single place the
  mapping lives, and `--selftest` now asserts the default emits v1 and *not* v0.
  Real vLLM only ever emits one surface — `both` is a rig affordance, not a fidelity
  claim.

- **`scripts/check-vllm-buckets.py`, and a CI job that runs it weekly.** Fetches
  `vllm/v1/metrics/loggers.py` and asserts each of the three bucket lists appears there
  verbatim, reporting the exact point of divergence when one does not.

  It exists because **nothing else in this repo could have caught either drift.** Every
  test here reads the simulator, and the simulator was perfectly consistent with itself —
  it was consistent with the wrong thing. A fault in a relationship to something *outside*
  the suite needs a check that points outside it, which is the same reasoning behind the
  existing weekly Helm-chart drift detection.

  It does not model upstream's file structure, which would itself be a thing that drifts:
  an `ast` walk pulls out every numeric list literal and looks for ours among them, so it
  survives renames and code moving between functions, and fires only when the *numbers*
  change. **Scheduled and dispatch only** — an upstream release is not a contributor's
  fault, and reddening unrelated pull requests trains people to ignore red. Exits `0` in
  sync, `1` on drift, `2` when it could not check at all, because "we did not look" and
  "we looked and it was fine" are not the same result.

- **A trimmed monitoring profile — `LITE=1`.**
  [`helm/kube-prometheus-stack/values-lite.yaml`](helm/kube-prometheus-stack/values-lite.yaml),
  an overlay applied on top of `values.yaml` rather than a second copy of it, so nothing
  load-bearing is stated twice. It drops Alertmanager, kube-state-metrics, node-exporter
  and the chart's ~100 default rules, and puts Prometheus on 256Mi/512Mi with 2h
  retention — taking the runtime floor from 5 GiB to 3 and the recommendation from 8 to 4.

  This exists because the 8 GiB ask is the most common reason a first `task local:up`
  never finishes: colima ships 2 CPU / 2 GiB, Prometheus sits Pending, and the run reads
  as a broken repo rather than an under-provisioned VM. The compose path answers "show me
  the boards"; this answers "let me exercise Kubernetes on the laptop I have".

  Nothing `local` exists to prove is given up — ServiceMonitor discovery, PrometheusRule
  evaluation, the sidecar dashboard import and `nvidia.com/gpu` scheduling all still run,
  and every check in `verify.sh` still applies. Verified rather than assumed: no `kube_*`
  or `node_*` series appears in either dashboard, either rule file or `verify.sh`, and
  the alert checks read Prometheus's own `ALERTS` series, which never involves
  Alertmanager. Alert *delivery* is not exercised — but it was not exercised by the full
  profile either.

  `LITE` is resolved in `scripts/config.sh`, so the Helm values stack and `kind-up.sh`'s
  sizing floor cannot disagree about which profile is being installed.

- **A social preview card** — `docs/social-preview.png`, the 1280×640 image GitHub
  renders in place of a bare link wherever this repo is shared. The setting lives only in
  GitHub's web UI and is exposed by no API, so the artefact is kept here to give it a
  history and a way back: the card is the top row of the LLM board — both tenants either
  side of the 2s threshold — over the repo name, the compose one-liner and the CI claim.

  `docs/social-preview.py` regenerates it (needs `pillow`, nothing else). It crops
  `docs/llm-dashboard.png` by **fraction rather than by pixel**, because the invariant
  that actually holds is "the top row occupies this proportion of the board" — Grafana
  lays panels out on a 24-column grid, so that survives both a differently-sized window
  and a later resize of the file. It warns if the crop lands under 1280px, the width
  below which it would be upscaling.

  Uploading it is manual, and re-uploading is the only way to change what GitHub serves:
  **Settings → General → Social preview → Edit → Upload an image**. Confirm with
  `gh api repos/ChrisAdkin8/k8s-ai-observability --jq .uses_custom_open_graph_image`.

- `docs/optimize-images.py` — resizes and palette-quantises the dashboard screenshots to
  the settings below. Idempotent: a file already at the target width and depth is skipped,
  so it is safe to re-run and safe to wire into a pre-commit hook.

### Changed

- **The dashboard screenshots are optimised** — 1.1 MB down to 414 KB, a 62% cut in what
  the README costs a first-time visitor before they have read a word. They were 3456px
  wide against GitHub's ~896px content column, so three quarters of every byte was
  reaching no display at any pixel ratio.

  Visually lossless rather than merely acceptable: these are flat UI screenshots with
  ~2700 distinct colours, so a 256-entry palette reproduces them at a measured mean
  channel error of 0.19/255, and dithering changes nothing because there is almost no
  gradient to dither. Re-check that if a board ever gains a heat-map or a photographic
  panel.

  **2400px, not the 1792px the README alone would want**, because `social-preview.py`
  crops 78% of `llm-dashboard.png` and renders it 1280px wide: at a 1792px source that
  crop is a 1.09x downscale and the card's text visibly softens. 2400px keeps it a 1.45x
  supersample. The alternative was a second full-resolution copy of the same screenshot,
  which is the kind of duplicate this repo avoids everywhere else.

## [0.2.0] — 2026-07-31

Two things this rig could not do before: show you the boards without a cluster, and hand
you a dashboard you could import anywhere.

⚠️ **Upgrading from 0.1.0 requires one manual step** — see the ConfigMap rename under
Changed.

### Added

- **A no-Kubernetes path** — [`compose/`](compose/). `docker compose up -d` gives both
  dashboards and both alert sets on `localhost:3000` in about a minute, with nothing
  installed on the host. It reads the *same* dashboards, recording rules, alerts,
  simulator and load profiles the cluster applies, and uses the same scrape job names, so
  every query in `docs/observability.md` pastes in unchanged. What it cannot exercise is
  Kubernetes itself — scheduling on `nvidia.com/gpu`, ServiceMonitor discovery, the
  sidecar import — and `compose/README.md` says so plainly.
- `compose/gpu-metrics-sim.py` — a compose-only stand-in for the fake-gpu-operator's
  exporter, which is a device plugin and a DaemonSet and so has no meaning outside a
  cluster. Copies its exact surface: three series, same labels, same eight `Tesla-T4`s,
  same all-or-nothing memory model. Three series are enough for all four GPU panels and
  every GPU alert, because temperature and power are derived by the shipped rules.
- `scripts/extract.sh` — unwraps the Prometheus rules and LLM profiles out of the
  Kubernetes manifests. Shared by the promtool tests and the compose stack, so both read
  what the cluster actually applies rather than a second copy.
- `task compose` / `make compose`.
- Instructions for publishing the boards to grafana.com, in
  `manifests/dashboards/README.md`. Both were checked against a Grafana whose only
  datasource was named and uid'd differently: `id` is null and no panel carries a fixed
  datasource uid, so they import anywhere.

### Changed

- **Dashboards are now plain `.json`** in `manifests/dashboards/`, rather than JSON
  embedded in hand-maintained ConfigMap YAML. One artefact, used three ways: wrapped in a
  ConfigMap by `install.sh`, mounted by the compose stack, or uploaded to grafana.com and
  imported into any Grafana. The filename is the uid, and `assert_dashboard_contract`
  fails the install if a filename and the `uid` inside it disagree.

  The generated ConfigMap names change with it — `dcgm-gpu-dashboard` becomes
  `gpu-sim-dcgm-dashboard`, `llm-sim-dashboard` becomes `llm-sim-overview-dashboard`. On
  a cluster installed before this change, delete the two old ConfigMaps by hand;
  otherwise both are imported and Grafana holds two boards under one uid.

- Dashboards now carry `app.kubernetes.io/part-of=gpu-sim-dashboards`, and `teardown.sh`
  removes them by it.

- The README now opens with the compose path, since it is the fastest way to see the
  boards; `task local:up` follows as the way to exercise Kubernetes itself.

- The compose stack's ports are overridable — `PROMETHEUS_PORT` and `GRAFANA_PORT`, the
  same names `scripts/config.sh` already uses. Not cosmetic: `scripts/prometheus.sh` and
  `scripts/grafana.sh` hold port-forwards on 9090 and 3000, and a loopback-bound
  port-forward wins over the container's wildcard binding. Run both at once and you get
  the *other* Prometheus at the same URL, with nothing reporting an error.

### Fixed

- `teardown.sh` left the dashboard ConfigMaps behind. They were never applied from a file,
  so there was no `-f` to delete them by, and nothing else covered them. Selected on the
  ownership label above rather than `grafana_dashboard=1`, because the chart ships several
  boards under that same sidecar label and the delete runs before the Helm uninstall.

## [0.1.0] — 2026-07-30

Initial public release. Build and test GPU and LLM observability without a GPU.

### Added

- **GPU simulation** — [`run-ai/fake-gpu-operator`](https://github.com/run-ai/fake-gpu-operator)
  advertises `nvidia.com/gpu` through the device plugin's `Allocate()` response, injects a
  fake `nvidia-smi`, and emits DCGM-format metrics, so the standard NVIDIA observability
  stack works unmodified.
- **LLM serving simulation** — `scripts/llm-sim.py`, a dependency-free standard-library
  Python file emitting the real vLLM metric surface: names, types and histogram bucket
  boundaries. Two tenants run side by side, one healthy and one deliberately saturated,
  driven by JSON profiles that can be edited without restarting a pod. `--selftest`
  validates the exposition with no cluster.
- **Observability stack** — `kube-prometheus-stack`, two Grafana dashboards shipped as
  sidecar ConfigMaps under stable `uid`s (`gpu-sim-dcgm`, `llm-sim-overview`), and
  recording rules and alerts across both domains. No grafana.com egress at install time.
  Temperature and power are synthesised from utilisation by recording rules and recorded
  under the real DCGM names, because the fake exporter emits neither.
- **Three targets from one definition** — `local` (kind), `eks` and `gke`, with per-target
  tasks defined once in `taskfiles/target.yml` and included three times so they cannot
  drift apart. `local` needs no cloud account, credentials or spend, and runs the same
  manifests, pinned charts and acceptance checks as the clouds.
- **Two front doors** — a Taskfile and a Makefile, both thin wrappers over `scripts/`,
  which remains the source of truth for install ordering and the wrong-context guard.
- **Tests that need no cluster** — `promtool` unit tests over every recording rule and
  alert (`task rule-tests`), and a simulator exposition selftest (`task selftest`). Both
  run in seconds. They cover what a live check structurally cannot: both sides of every
  threshold, the derived temperature and power arithmetic, the `by (model_name)`
  aggregation that keeps the two tenants apart, the self-disabling `unless on (UUID)`
  guard, and the alerts nothing on the rig ever drives. See [tests/](tests/), which also
  documents the two properties these tests cannot pin, and why.
- **Acceptance suite** — `scripts/verify.sh`, GPU checks 1-5 (including 4b/4c/4d) and LLM
  checks L1-L6: metrics flowing, both boards served to an unauthenticated request, and the
  alerts genuinely reaching `firing`.
- **Drift assertions** — `scripts/config.sh` aborts the install when its constants
  disagree with the static manifests, the Helm values, `kind/gpu-sim.yaml` or the
  Terraform outputs. Each guards a failure that would otherwise be a green install with
  zero GPUs, or a confident dashboard link to a Grafana 404.
- **CI** — a fast job plus the full stack stood up on kind on a runner that has never seen
  a GPU, running the advertised `task local:up` end to end including every acceptance
  check, with a diagnostics bundle on failure and weekly upstream-drift detection.

[Unreleased]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/releases/tag/v0.1.0
