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

### Added

- **A "Contributing and support" section in the README**, closing the one gap that graded
  badly against every published README standard at once. `SECURITY.md`,
  `CODE_OF_CONDUCT.md` and both issue forms already existed and already scored on GitHub's
  community profile — but the profile checks that a file *exists*, not that a reader can
  find it, and none of the four was reachable from the page most people actually read.
  Grepping the README for `issue|discussion|support|help` returned nothing.

  The section routes the four requests that actually arrive — an empty panel, a bug or an
  upstream drift, a security report, a change — to the file that owns each, and says *not*
  to open a public issue for the third. Discussions are disabled on the repo, so it does
  not offer them.

  **No response-time commitment is stated**, deliberately. `SECURITY.md` sets one for
  security reports and that is a narrower promise than a general one; inventing a second
  would be a claim about spare time that nothing enforces.

### Changed

- **The LITE sizing block in the README keeps the instruction and delegates the mechanism.**
  0.6.0's `8ea005e` put the whole 3-vs-4 GiB finding in Prerequisites, which was right
  about *where it bites* and wrong about how much of it a reader needs there. What stops
  you cold is "allocate 4, do not try 3"; the arithmetic behind it — reported vs allocated
  GiB, `MemTotal` floored by integer division, 2.83 reading as `2` — is what you want
  *after* it has already bitten. That now lives in
  [docs/troubleshooting.md](docs/troubleshooting.md), with the measurement stamp, and the
  README links to it in one line.

  `scripts/config.sh` moved with it. Its comment beside `KIND_MIN_MEMORY_GIB` used to say
  the README "says why", which stopped being true — the pointer now names troubleshooting,
  because a stale cross-reference between the constant and its explanation is exactly the
  drift class that comment exists to prevent.

- **`CONTRIBUTING.md` records why the README leads with "Try it" before "Install".** The
  order inverts what [Standard Readme](https://github.com/RichardLitt/standard-readme)
  asks for, and it is deliberate: the compose path needs no cluster and no prerequisites,
  so the fastest honest answer to "what is this" is a board you can look at. Written down
  because it has now been questioned once, and an undocumented deviation reads as an
  oversight to the next person who checks.

## [0.6.0] — 2026-08-02

**This release gives the rig an SLO, and then makes the docs tell the truth about it.**
0.5.0 finished the phase breakdown and left three separate warnings not to build an
objective on a percentile here, with no answer to what you should build instead. This one
answers it: a ratio evaluated *at* a bucket boundary interpolates nothing, so it carries
none of the caveats the rest of the board is full of. The bucket layout was never the
obstacle — it is the constraint that tells you where the threshold has to sit.

The rest is consequence. A metric that was always zero stopped being emitted, because a
flat zero is a claim and a false one; every count in the prose that those two changes
invalidated was found and corrected; and the board screenshot was retaken, which obsoleted
a caveat and exposed a second figure worth explaining rather than editing away.

### Added

- **A TTFT service-level objective, expressed as a ratio at a bucket boundary rather than
  a percentile** — `llm:ttft:slo_ratio5m` / `30m` / `1h` / `6h`, with the standard
  fast/slow burn-rate pair over them (`LLMTTFTErrorBudgetFastBurn`,
  `LLMTTFTErrorBudgetSlowBurn`) and a burn-rate panel on the LLM board.

  **This finishes an argument the repo had only half of.** Three separate places told you
  *not* to build an SLO on a percentile here — not on prefill p95 (3.03x overstated), not
  on ITL p95, not on the 2s threshold — and none said what to do instead. The resolution
  is that every one of those caveats is a property of `histogram_quantile` interpolating
  *inside* a bucket. A ratio evaluated *at* a boundary does not interpolate at all, so it
  inherits none of them. The bucket layout was never an obstacle to an SLO; it is the
  constraint that says how to build one, and the objective is **99% of requests reach a
  first token within 2.5s**.

  ⚠️ **2.5 rather than the existing alert's 2s, because `TTFT_BUCKETS` has no 2.0
  boundary** — it steps `1.0, 2.5, 5.0`. `le="2"` matches nothing, the ratio records
  empty, and both burn alerts stay green forever on rules that read correctly. Not 1.0
  either: the steady tenant measures 99.368% there, 0.37 points of headroom against a 99%
  target, so jitter alone would have the *healthy* tenant reporting a blown budget. At 2.5
  it measures 100% and the saturated tenant 0.32%.

  **`LLMHighTTFT` is untouched and stays at 2s.** The two are different instruments — one
  an interpolated percentile that separates the shipped tenants, one a boundary that makes
  a ratio exact — and reconciling them would re-derive the profile arithmetic, `verify.sh`
  L3b's headroom and every existing promtool expectation. Nothing existing moved.

  **Four limits ship with it**, on the catalog page rather than buried here: your threshold
  is constrained to the boundaries you have; "exact" holds only because numerator and
  denominator share a target and scrape timestamps; a total stall reads as *healthy*
  because a request that never reaches a first token contributes no observation, so this is
  a latency objective and not an availability one; and the 6h window never fills on a rig
  that lives minutes.

  **Upgrading needs no action** beyond re-running `install.sh` to apply the rules. The
  alerts carry **no `for:`** — a deliberate break from the other seven alerts here, since
  the long window already provides that smoothing — and a traffic guard whose window
  matches each alert's own short window, without which an idle tenant reads `0/1e-9 = 0`
  and both fire hardest on a model serving nothing at all. `verify.sh` gains **L9**,
  asserting the four ratios record against a live Prometheus, which is the only place a
  boundary that does not exist in your exposition shows up.

### Changed

- **An organisation logo for grafana.com**, `docs/logos/org-chrisadkin.png`, generated by
  the same `docs/dashboard-logos.py` as the two board marks so the set cannot drift.

  It is not a third board mark. The two boards are a die (hardware) and a panel frame
  (serving); the org mark is those two **fused** — the die with the serving signal running
  through it, edge to edge — so it reads as the thing they both came from rather than as a
  sibling sitting beside them on the org page. The first attempt was a frame with a
  threshold and two tenants through it, which *is* the LLM mark, and was discarded for
  exactly that reason.

  ⚠️ **Its die is blue where the LLM mark's frame is `DIM`, and that is a legibility fix
  rather than a preference.** `DIM` keeps a container from competing with its contents,
  which is right at 512px and wrong at 64: it is too close in value to the tile to survive
  the downsample, so the die dissolved and the mark became a green squiggle. Caught by
  rendering the 64px version, which is the only size that settles it.

- **`What you get` tightened from 53 lines to 47**, by cutting justification the repo
  already tells properly elsewhere rather than by dropping claims — every one of the eight
  bullets survives, and a term-by-term check confirms nothing it referenced was lost. The
  weekly-drift bullet was ten lines, half of them re-telling the 0.1.0/0.2.0 story that
  [docs/versions.md](docs/versions.md#keeping-them-honest) tells in full and that the
  bullet did not even link to; it now makes the point in two clauses and links there. The
  dashboards bullet dropped a four-way enumeration of how the JSON is consumed, which is
  detail rather than value at that position.

- **`Bring your own Prometheus` moved out of the README into
  [docs/byo-prometheus.md](docs/byo-prometheus.md).** It was 44 lines — a seventh of the
  README, and the largest single block inside `Install` — covering the
  `--skip-monitoring` flow, the two labels that fail silently, and what `verify.sh --byo`
  relaxes.

  Nothing was cut: a line-by-line check confirms every substantive line survives, and the
  page gains what a README subsection could not carry — the choice between the script path
  and the Helm chart stated up front, and a note that the chart's `helm test` is the only
  thing here that can *verify* those two labels against a live Prometheus rather than
  merely document them. A short pointer keeps the trail from `Install`, and the README
  drops from 306 lines to 280.

### Removed

- **`vllm:num_preemptions_total` is no longer emitted.** It was set to `0` in the
  simulator's `__init__` and never incremented anywhere, so every scrape carried a
  permanent flat zero.

  ⚠️ **Removing an emitted metric name is a breaking change by the table at the top of
  this file**, which is why this bumps the MINOR while the version is `0.x`. **The real
  blast radius is much smaller than that classification suggests, and worth stating
  plainly:** nothing in this repo referenced the series — no panel, recording rule, alert,
  promtool case or `verify.sh` check. **No alert behaviour changes anywhere, for anyone**,
  because an alert on a permanently-zero counter could never have fired. The only
  observable difference is that a panel bound to it stops reading a flat zero and reads no
  data instead.

  That difference is the point rather than a regression. A blank panel says "not
  measured"; a flat zero says "no preemptions are happening", which is a claim, and a false
  one — on a rig whose saturated tenant exists to show a server under pressure, and via
  `ghcr.io/<owner>/vllm-metrics-sim` to people who cannot read the simulator to find out.
  The standard is already written down in
  [`manifests/llm/10-profiles.yaml`](manifests/llm/10-profiles.yaml): an invented number
  presented as a modelled one is the failure that file's arithmetic exists to prevent.
  Unlike the KV-cache ceiling — unreachable on the shipped profiles, and documented as such
  in four places — nothing recorded that this one was a stub.

  **Emitting it honestly would mean modelling KV pressure first, and there is none:**
  `_admit()` gates on `max_concurrency` alone, so `kv_cache_usage()` peaks near 0.43 even
  on the saturated profile. Preemption is what a real engine does when the KV cache runs
  out. That is a feature rather than a fix for one counter, and it would touch the most
  delicate code in the simulator, so it stays out until someone builds it.

  **Not deprecated first, deliberately.** Deprecation would keep a false zero on people's
  boards for a release cycle to be polite about it, which is the harm rather than a
  gentler path to fixing it.

  The metric is not lost so much as reclassified: `scripts/check-vllm-buckets.py` now lists
  `vllm:num_preemptions` among the upstream metrics this simulator does not emit — a list
  that grew from 22 to 23 — which is the honest backlog and where an unmodelled upstream
  metric belongs.

### Fixed

- **Counts in the prose that the two changes above had invalidated**, plus two that had
  been wrong for longer. Nothing here changes behaviour, and every one of them is a number
  a reader checks this repo's credibility against.

  Made stale by the SLO and the preemptions removal: `llm-simulation.md` described the LLM
  rule file as "Recording rules + **four** alerts" (six); it and `versions.md` both said
  the simulator emits **16** of upstream's names (15); `CONTRIBUTING.md` quoted the drift
  check's gap list at **22** (23); `architecture.md` enumerated the LLM alerts without
  either burn alert; the chart README and `verify.sh`'s own section header still read
  `L1–L8` while that script's file header had already moved to `L1–L9`; and
  `tests/README.md` listed the alerts the shipped workloads drive without
  `LLMTTFTErrorBudgetFastBurn`, which `llm-saturated` drives at ~100x.

  Wrong for longer, and surfaced by the same sweep: the README advertised **eight** PromQL
  queries in `observability.md`, which has carried nine since `ALERTS{alertstate="firing"}`
  was added, and `manifests/dashboards/README.md` said `dashboard-publish.py` repoints
  **22** `${datasource}` references on the LLM board — true when that board had nine
  panels, 33 now.

  ⚠️ ~~**25620 has fallen behind this repo in both of its artefacts**~~ **DONE — revision 3
  uploaded and the catalog page re-pasted; confirmed live via grafana.com's API on 2026-08-04.**
  Struck rather than deleted: the reasoning outlives the action, and the next panel will
  need it again. Recorded at the time in
  `manifests/dashboards/README.md` rather than left to be discovered. Published revision 2
  predates the burn-rate panel and the live catalog page predates the SLO section, so
  importing by id today gets neither. That file has always warned that the catalog does not
  update itself; this is the first time it has applied to a **panel** rather than to prose,
  which is the case someone actually notices.

- **`docs/llm-dashboard.png` retaken and optimised**, 3456x1988 RGBA down to 2400x1381 at a
  256-colour palette — **704K to 247K, 65% smaller** — through `docs/optimize-images.py`,
  which `gpu-dashboard.png` had already been through and this one never had.

  **The retake obsoleted a caveat, which is the part worth reading.** The README carried a
  note saying its screenshots predated the vLLM V1 bucket sync, so the saturated tenant
  read `1.20 mins` instead of 78s. The new capture reads **1.30 mins — which is 78s** — so
  the note now contradicted the paragraph above it, and is gone. The board's alt text went
  with it: it said "six panels" for a board that now shows twelve.

  **A second discrepancy the retake exposed is documented rather than reconciled.** The
  capture's *healthy* tenant reads a p95 of **~480 ms** where the README says `~120 ms`,
  with its `waiting` gauge flat at zero throughout. Both are right, and the new
  [Why an observed steady p95 runs higher than ~0.1s](docs/llm-simulation.md#why-an-observed-steady-p95-runs-higher-than-01s)
  says why: Little's Law puts steady's mean concurrency at `1.8 × 5.84` = 10.5 against a
  `max_concurrency` of 16, and arrivals are Poisson (`rng.expovariate`), so the batch
  reaches 16 regularly and those arrivals wait. A gauge sampled once per 15s scrape misses
  a queue that forms and drains between two of them; the TTFT histogram observes every
  request and does not. **Neither figure was edited to match the other** — `~120 ms` is what
  the profile arithmetic models, and `verify.sh` L3b and every promtool expectation are
  built on that arithmetic.

- **`docs/social-preview.py`'s crop re-tuned, and the comment justifying it corrected**,
  then `docs/social-preview.png` regenerated from the new screenshot.

  `FRAC` moves from `(0.1826, 0.1157, 0.9586, 0.4422)` to
  `(0.0908, 0.0565, 0.9792, 0.3968)`, and the band from 310px to 282px. The card now shows
  the top **two** rows — TTFT p95, ITL p95 and both running-vs-waiting repeats, four panels
  complete — rather than one row, because one row of the retaken board is a 10:1 strip that
  leaves the card mostly empty.

  ⚠️ **The old comment's reasoning was wrong, which is why this needed re-tuning at all.**
  It justified fractions-not-pixels by claiming "the top row occupies this proportion of the
  board whatever size the window was". Fractions survive a **resize** — the reason they are
  still fractions — but not a **re-capture**: the retake had a narrower Grafana sidebar and a
  board one panel taller, so every boundary moved, the old fractions landed mid-panel, and
  the card rendered cut through two rows.

  **Nothing failed.** The script exited 0 and wrote a plausible, wrong card — the repo's own
  worst category. The corrected comment now says to re-check the crop whenever
  `llm-dashboard.png` is retaken, and records how the boundaries were measured (scan for
  rows that are uniformly page-background; the gutters are `0.0565-0.2252` and
  `0.2288-0.3968` on the current capture).

  ⚠️ **The card now carries a rendering artefact from the capture itself**: both series in
  each running-vs-waiting panel are labelled `running` in the legend, where the shipped
  `llm-sim-overview.json` specifies `running` and `waiting` and git history shows it never
  held anything else. It is in the screenshot, not in the board — and the social card is the
  most public artefact here, so the next retake matters more than it usually would.

## [0.5.0] — 2026-08-01

**This release finishes the sentence 0.4.0 started.** That one gave a cluster with its own
Prometheus a route in; this one removes the two things still standing between a stranger
and a working board. The **Helm chart** means you no longer need a shell script with the
right flags — `helm install`, and your monitoring stack is untouched. The **published
simulator image** means you do not need this repo at all to point your own dashboards at a
realistic vLLM surface.

The third piece is the one the boards themselves needed. The vLLM board could say how long
a request took and not what it was *doing*: TTFT and queue time covered the waiting half,
and nothing covered the serving half. The **request phase breakdown** closes that, and it
is pure extraction — every term was already computed inside the simulator.

⚠️ **Everything here is additive.** No existing metric, recording rule, threshold, profile
or expected value moved, and no dashboard `uid` changed. An existing install upgrades by
re-running `install.sh`, which now rolls the simulator pods when the script changes — see
*Fixed*.

### Added

- **The request phase breakdown: `vllm:request_prefill_time_seconds`,
  `vllm:request_decode_time_seconds` and `vllm:request_inference_time_seconds`.** The
  board could say how long a request took and not what it was *doing*. TTFT and queue time
  covered the waiting half; the serving half had no series behind it at all, so there was
  nothing to build a prefill-versus-decode panel against — the first thing anyone working
  on disaggregated serving looks for.

  **Additive, so nothing is re-derived.** These are three new series and four new recording
  rules; no existing metric, rule, threshold, profile or expected value moved. Upgrading
  needs no action beyond re-running `install.sh` (which now rolls the simulator pods on a
  script change — see *Fixed*, below).

  This is **extraction, not modelling**. Every term already existed inside the simulator:
  prefill was assigned in `_admit()`, and decode was the `gen_tokens * itl` product
  `finish_at` was already formed from. They are now stored on the request and observed at
  the same point TTFT, e2e and queue time are observed. No invented numbers — that is the
  bar, and it is why the other 22 absent upstream metrics stay absent.

  All three reuse `E2E_BUCKETS`. Verified against `vllm/v1/metrics/loggers.py`: upstream
  declares one `request_latency_buckets` list and passes it to all five request-scoped
  histograms, so no bucket constant was added and the weekly drift check's existing entry
  already watches these boundaries on behalf of every one. That check's reported gap falls
  from 25 to 22, with all three matched rather than reported as drift.

- **A phase breakdown panel and a decode-p95 panel on the LLM board (dashboard 25620),
  plus four recording rules: `llm:queue:mean5m`, `llm:prefill:mean5m`, `llm:decode:mean5m`
  and `llm:e2e:mean5m`.** Both panels are appended below the existing ten rather than
  reflowing them, so an existing user's muscle memory survives.

  ⚠️ **The breakdown is MEANS rather than percentiles, and that is load-bearing.** Two
  measured reasons. Quantiles are not additive — on the steady tenant p95
  queue+prefill+decode is 7.473s against a p95 e2e of 7.468s, while the means sum to
  5.101s against 5.101s exactly — and a stack whose segments do not reach the total reads
  as a bug in the rig forever. And these buckets cannot resolve prefill at this operating
  point: the first boundary is 0.3s against a modelled 0.08s, so every observation lands
  in the first bucket and `histogram_quantile` interpolates from zero across it, **3.03x**
  overstated on both tenants. A histogram mean has no bucket dependence at all.

  ⚠️ **That second effect transfers to real vLLM** — the boundaries are upstream's, so a
  real deployment with sub-300ms prefill reads exactly as high. It is documented on the
  catalog page beside the existing inter-token-latency caveat (which is a 1.08x effect, for
  scale). **Do not build a prefill SLO on a p95 from these buckets.** The one recorded
  percentile is scoped to decode, the only phase these buckets resolve tolerably.

  `llm:e2e:mean5m` exists because the breakdown is asserted to **add up** — as a permanent
  promtool test and again on a live cluster by `verify.sh` **L8**, which also asserts all
  three histograms are receiving observations. An untested design decision is one that gets
  reverted by someone who does not know why it was made.

- **A Helm chart, `charts/k8s-ai-observability`, for clusters that already run
  Prometheus.** This is a new supported installation mode, not a replacement:
  `scripts/install.sh` stays the source of truth for install ordering and the
  wrong-context guard, and remains what CI exercises end to end.

  ```sh
  task chart
  helm install rig dist/charts/k8s-ai-observability --set releaseLabel=<your release>
  helm test rig --logs
  ```

  **Why.** Both boards are published, so people arrive from the catalog, import one, and
  find the panels blank for want of the `llm:*` recording rules. Their only route was a
  script that installs kube-prometheus-stack over the top of whatever they already run,
  which nobody with a production monitoring stack will accept. That was the single biggest
  structural blocker to adoption.

  ⚠️ **There is a build step, and `helm install ./charts/...` does not work.** Helm's
  `.Files.Get` cannot read outside the chart directory, so the chart cannot reference
  `manifests/dashboards/*.json` or `manifests/alerts/*.yaml` where they live. Of the three
  ways out — a build step, symlinks, or committed copies with a drift check — only the
  build step means the second copy **never exists in the tree**, which is what this repo
  refuses everywhere else. `task chart` assembles into gitignored `dist/`. The cost is
  real and the chart's own assertions state it rather than failing with a template error.

  **The simulator image above is what made this affordable.** `scripts/llm-sim.py` used to
  be the hardest item on that list — executable code, the one file a drifted copy of would
  be genuinely dangerous. A chart whose simulator Deployment references an image needs a
  *tag*, not the file, so it drops out entirely; what remains is static JSON and YAML.
  That is the one structural difference between the chart and `install.sh`, which still
  mounts the script from a ConfigMap.

  ⚠️ **Two values fail with NO ERROR AT ALL**, and this is the most likely way the chart
  appears broken. A wrong `releaseLabel` means your Prometheus never adopts the rules or
  ServiceMonitors — they never evaluate, the scrapes never happen, every derived panel is
  empty. A wrong `grafana.dashboardLabel` means the sidecar never imports the boards and
  `/d/<uid>` 404s. Every object reports itself successfully created either way.
  **`helm test` is what says so out loud, and it is opt-in** — a genuine weakness of the
  design, stated rather than hidden, which is why `NOTES.txt` tells you to run it in the
  imperative.

  **`install.sh`'s five assertions are ported rather than lost.** A `helm install` runs
  none of them, and a chart that installs cleanly and produces an empty dashboard is worse
  than no chart, because the failure arrives later and looks like the user's fault.
  Everything knowable at render time is a template-time `fail` caught by `--dry-run` — the
  dashboard filename/`uid` contract, that every board parses, distinct `model_name`s, the
  three-way naming invariant — and everything needing a cluster is a `helm test` hook. The
  chart README maps `CONTRIBUTING.md`'s invariants table onto which half covers each row,
  so the two cannot drift. **CI drives all thirteen to their failure**, because an
  assertion that has quietly stopped firing looks exactly like one that passes.

  One assertion has no `install.sh` counterpart: the capacity arithmetic. The script
  path's profiles are static files, so nobody can set an arrival rate that stops the two
  tenants straddling the 2s alert threshold. Templating them from `values.yaml` — needed
  so the numbers are genuinely reachable rather than frozen at their defaults — created
  that possibility, so the chart refuses to render it.

  ⚠️ **The chart's default image tag and the repo's release tag must agree, and nothing
  else enforces it.** A chart on a stale tag installs cleanly and runs an old simulator.
  `llm.image.tag` defaults to empty, meaning "use `Chart.appVersion`", and both
  `task chart` and the publish workflow cross-check it.

- **The simulator is published as a container image**, `ghcr.io/<owner>/vllm-metrics-sim`,
  for `linux/amd64` and `linux/arm64` on every release tag.

  ```sh
  docker run --rm -p 9401:9401 ghcr.io/chrisadkin8/vllm-metrics-sim:latest
  ```

  **This reverses a stated non-goal, and the reversal is narrower than it looks.** The old
  rule — *stdlib-only Python mounted into a stock image, so there is nothing to build, push
  or patch* — was reasoning about how **this rig** runs the simulator, and remains true of
  that path. It said nothing about how **anyone else** consumes it, which is the case an
  image serves: as a file inside this repo, `llm-sim.py` cannot be pointed at someone's own
  vLLM dashboards without cloning. The stdlib-only constraint is what makes the image
  trivial — a `FROM` and a `COPY` — and **`pip install` remains a non-goal**, unchanged.
  The two are habitually stated in one breath; only one of them moved.

  ⚠️ **Nothing about how the rig runs changes.** `install.sh` still builds the
  `llm-sim-script` ConfigMap from `scripts/llm-sim.py`, and the compose stack still mounts
  the same file. An image-based Deployment would pin a **tag**, so a local edit would stop
  reaching the cluster *silently* with the pod still `Running` — the exact failure the
  checksum annotation below was just added to fix, one layer up. The image is for external
  consumers, and both the Dockerfile and the docs say so, because someone will otherwise
  helpfully "simplify" the Deployment onto it.

  Built **from `scripts/llm-sim.py`** rather than a vendored copy, and CI fails if a second
  copy appears in the tree or if the image's payload stops matching the file byte for byte.
  A drifted copy of the simulator would be undetectable from the outside, which is precisely
  the property `tests/contracts/` guarantees for the DCGM surface.

  CI **runs** the image rather than only building it — building proves the Dockerfile
  parses and nothing more. ⚠️ Both architectures are **built**; only `linux/amd64` is
  **executed** on a GitHub runner, which is amd64. That is defensible while the payload is
  one architecture-independent `.py` file with no compiled extension — an arm64-only
  runtime failure would have to originate inside `python:3.12-slim` itself — and it stops
  being defensible the moment this image grows a native dependency. (`linux/arm64` was
  built and executed by hand during development, on Apple Silicon.)

  ⚠️ **The port override is `LLM_SIM_LISTEN_PORT`, not `LLM_SIM_PORT`.** kubelet injects a
  Docker-link-compatible `<SVCNAME>_PORT` for every Service in the namespace, so a Service
  named `llm-sim` sets `LLM_SIM_PORT=tcp://<ip>:9401`; reading that name meant `int()` got
  a URL and every pod died at startup. Verified against the built image: the correct name
  moves the listener and the wrong one is silently ignored rather than fatal. Documented in
  both directions, because someone will try the obvious name and conclude there is no
  override — and because the obvious name is the bug.

  `task image` builds and smoke-tests it locally. No new CI secrets: `GITHUB_TOKEN` is
  sufficient for `ghcr.io`, which matters because fork PRs never receive secrets at all.

- **A docs-only change no longer stands up two kind clusters and a compose stack.** A
  `changes` job diffs the push or the whole PR and gates the expensive jobs on the result:
  ~13 minutes down to seconds. Verified safe first — nothing under `scripts/`, `compose/`,
  `manifests/` or `tests/` *reads* a `.md` file; every match is prose in a comment.

  Deliberately **not** `paths-ignore:` on the triggers. A ruleset on `main` requires these
  checks, and a job that never *starts* leaves its check pending forever, so a docs-only PR
  would be unmergeable. It fails **open** — new branch, force-push, unreachable base, empty
  diff all run everything — because being wrong that way costs 13 minutes and the other way
  ships untested code. `schedule` and `workflow_dispatch` always run the lot: "does this
  still work" must not depend on what the last commit happened to touch.

### Fixed

- **`install.sh` rebuilt the simulator ConfigMap without rolling the pods, so an UPGRADE
  kept running the old script.** The 0.4.0 note below says to restart the simulator
  Deployments "(or let `install.sh` roll them)" — the second half was not true until now.

  A running pod serves the code it started with. kubelet eventually syncs the projected
  volume, but the Python process read `llm_sim.py` once at exec time and never looks
  again; and because only the ConfigMap's *contents* changed, nothing in the Deployment
  moved, so `kubectl apply` reported `unchanged` and no rollout happened. **The install
  went green while the cluster kept emitting the previous metric surface** — the
  silent-success failure this repo writes assertions against.

  Invisible on a fresh install, because there the first pods already have the current
  file, which is exactly why CI never caught it: CI always builds a new cluster. It
  surfaced as `verify.sh` L7 failing on an existing cluster with both prefix-cache
  counters absent while the ConfigMap plainly contained them.

  Fixed with a checksum annotation on the pod template, whose important property is that
  it is a **no-op when nothing changed** — a re-install with an unmodified script rolls
  nothing. An unconditional `rollout restart` would churn both tenants every install and
  reset the queue the saturated profile spends minutes building, briefly breaking the very
  checks this exists to keep passing. The Deployments are discovered by what they **mount**
  rather than by name, so the opt-in `llm-driven` tenant is covered too; hardcoding the two
  shipped names would have left extras users with the same bug, still silent.

  `python3` is now declared in `install.sh`'s `require_tools`. It was already a hard
  dependency via the dashboard JSON check, where a missing interpreter surfaced as
  "`<board>` is not valid JSON" — an error that sends you to inspect a file that is fine.

- **The first version of that gate blocked docs-only PRs outright**, which is the exact
  failure it was written to prevent. A job-level `if:` on the `stack` **matrix** meant that
  when it skipped, GitHub did not interpolate the job name — the check reported as the
  literal `full stack on kind (${{ matrix.profile }})`, so the two names the ruleset
  requires never reported. Caught on a real docs-only PR: every job green or skipped,
  `mergeable=MERGEABLE`, **`state=BLOCKED`**.

  The gate moved to the **steps**, so the job runs, the matrix expands and both names
  report. Two steps already carried a condition, where a blind second `if:` would have been
  a duplicate YAML key; merged, with `always()` ordered so the gate still holds — on a
  docs-only run there is no cluster to dump.

- **That fix cost the one honest signal**, so the job now says so. A skipped job reports
  `skipped`; a step-gated one reports `success` in three seconds without standing anything
  up, under a check named *"full stack on kind"*. The name cannot carry the caveat — it has
  to stay byte-identical or the ruleset stops matching — so a `::notice::` on the run
  summary states that the green means "nothing here could have been affected", not "the
  stack was verified".

- **The compose leg's readiness wait was tuned to the fast path**, and went red on a
  perfectly healthy stack: every container `Up`, no crashes, nothing in the logs. Prometheus
  was serving at `19:20:15` while Grafana was still running its first-run SQLite migrations
  at `19:21:15`, a full minute behind. 60s → 180s, and the failure now records when each
  service became ready and names whichever did not, instead of the useless "Prometheus
  and/or Grafana never became ready" that made this need a log download to diagnose.

  Also worth recording: the obvious one-liner for tracking both services,
  `[ -z "$x" ] || curl … && x=$i`, is wrong under `set -e` — the failing curl is the line's
  exit status, so the step aborts on the *first* poll, before anything can start. `if`.

- **Documentation sweep.** `docs/llm-simulation.md` contradicted itself on the saturated
  tenant's p95 — two places said `~60s` while a third, two lines below one of them, said
  `~78s`. `verify.sh` settles it: `~58s` is the true queue wait, `~78s` is what p95 *reports*
  once V1's `(40, 80]` bucket quantises it. Also documented `task load`, which appeared in
  `task --list` and nowhere else; linked `manifests/workloads/extras/README.md`, the last
  unreachable doc; and spelled out `GRAFANA_DASHBOARD_LABEL_VALUE` and the `KIND_MIN_*` /
  `KIND_WANT_*` overrides, which had behaviour documented but no names.

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

  ⚠️ ~~**25620 needs re-submitting as a new revision.**~~ **DONE — uploaded 2026-07-31.**
  Struck rather than deleted, because the reasoning outlives the action: the board is
  published, so a merged panel reaches nobody who imported it by id until the catalog copy
  is uploaded again, and it must go up as a revision of the existing id, never as a new
  dashboard — a second upload mints a second id and everyone on the first silently stops
  receiving fixes. That applies to the next panel too, and is stated as standing guidance
  in [`manifests/dashboards/README.md`](manifests/dashboards/README.md).

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

  ⚠️ ~~It proves the trimmed stack **functions**, not that it fits 3 GiB~~ **MEASURED —
  2026-08-04, and the claim was wrong in a way worth stating precisely.** The CI runner has
  ~16 GB and nothing there is under memory pressure, so a constrained local run was the
  only thing that could settle it. Run at last, on colima/aarch64:

  | Runtime allocated | Docker reports | `kind-up.sh` reads | Result |
  |--|--|--|--|
  | 3 GiB — the advertised floor | 2.83 GiB | **2 GiB** | **refused before anything started** |
  | 4 GiB | 3.81 GiB | 3 GiB | `task local:up` green, **ALL CHECKS PASSED** |

  **The trimmed stack does fit — it used 2.197 GiB of 3.813**, 57.6%, with zero restarts,
  zero OOMKills and nothing Pending, through every GPU check and L1–L9. Consumption was
  never the problem.

  ⚠️ **The floor was unreachable, and the cause is a unit mismatch nobody would guess.**
  `scripts/kind-up.sh` computes `mem_bytes / 1024 / 1024 / 1024` — integer division — so a
  runtime is measured by what it REPORTS, while a user sets what it is ALLOCATED. VM
  overhead eats ~0.2 GiB and truncation eats the remainder, putting a full GiB between the
  two. Asking for exactly the documented 3 GiB produced a `2 GiB` reading and an error
  telling you to raise it to 4 — the floor refusing itself.

  Fixed in the docs rather than the check: the README now gives the LITE path its own
  `colima` line at `--memory 4` and says why 3 fails, since the full path always had a
  command to copy and the trimmed one — the only one where the gap bites — never did.
  Rounding instead of truncating was the alternative and was rejected: it would make the
  guard accept 2.83 GiB as "3", which is looser than the check was written to be.

- **`.github/dependabot.yml`** for the SHA-pinned actions. Pinning by SHA is right, but a
  SHA pin never moves on its own — and this repo has a live instance, with
  `actions/upload-artifact` warning on every run that it targets Node 20. It cannot cover
  the two Helm chart pins, which live in shell variables and stay a deliberate manual
  decision; the file says so and says why.

- **Both dashboards are published to the grafana.com catalog**: the GPU board is
  [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) and the vLLM board is
  [25620](https://grafana.com/grafana/dashboards/25620-llm-simulation-vllm-serving-overview/). They can now be imported by id
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

  ⚠️ ~~**Unverified: grafana.com's own logo requirements.**~~ **RESOLVED — 2026-08-04, and the
  answer is that there is no requirement to meet.** Grafana's dashboard-publishing
  documentation states no dimension, aspect ratio, file-size or format limit for a logo —
  it lists the asset and specifies nothing about it. So the assumption could never have
  been checked against a spec, because none is published.

  What settles it instead is acceptance: `GET /api/dashboards/25618` and `/25620` both
  report **`hasLogo: true`**, so the 512×512 PNGs were taken without complaint. The choice
  stands on the reason it was made — it downsamples cleanly — and `SIZE` in the script is
  still the whole edit if that ever changes.

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

[Unreleased]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ChrisAdkin8/k8s-ai-observability/releases/tag/v0.1.0
