# Prompt: A live board a stranger can click, and the funnel that reaches it

> Supersedes the still-live half of `prompt-adoption.md` (written 2026-07-31, deleted
> 2026-08-07). Four of that document's five priorities shipped between 0.4.0 and 0.9.2 —
> the image, the chart, the burn-rate rules and the community health files — and its
> stated non-goal reversal is already recorded where it belongs, struck through in
> `prompt-chart.md:209`, `prompt-fidelity.md:309` and `prompt-packaging.md:229`. What is
> left is the one priority it ranked first and nothing has touched.

## Role & Objective

Everything downstream of the scrape is verified here. Nothing verifies that a stranger can
*see* it. The fastest proof this repo offers still asks for a `git clone` and a working
Docker, and the two assets that already have an audience — the grafana.com catalog pages —
point at a repository rather than at a board.

**Objective: a URL that renders a live board with moving data, anonymously, and the three
places that should point at it doing so.**

This is distribution work, not capability work. `ROADMAP.md:6-9` deliberately scopes itself
to "capability the repo has never had" and pushes defects to `task outstanding`, so neither
page owns this and it has drifted for a week as a result.

### Effort, and where to stop

Estimates from reading the code — **treat the ordering as firmer than the numbers**, and
re-derive the largest line. Verification is priced in at roughly a third, per house rule;
here it is unusually cheap because the acceptance criteria are all externally observable.

| | Estimate | Depends on |
|---|---|---|
| W1 host the stack and expose it read-only | ~half a day, most of it the host | nothing |
| W1.4 the hardening pass | ~1 hour | W1.1 |
| W2.1 homepage + README | ~15 minutes | W1 |
| W2.2 catalog pages, republished as revisions | ~1 hour | W1, and see the ⚠️ |
| W3 Discussions and seeded issues | ~1 hour | nothing |

**W1 and W2.1 are the deliverable.** W2.2 is where the existing traffic actually is, so it
is the highest-value hour on the page, but it touches published artefacts and carries the
only irreversible step. W3 is independent and can land first if W1 stalls on a host.

## Background / Facts — VERIFIED 2026-08-07

### The repo is eight days old, and nobody has found it

`gh repo view` on 2026-08-07: **2 stars, 0 forks, 0 open issues**, `hasDiscussionsEnabled:
false`, `homepageUrl: ""`. Against that, `git rev-list --count HEAD` is **162** and tags run
to **v0.9.2** from a first commit on **2026-07-30**.

That ratio is the whole argument. The constraint is not engineering output.

### The demo that exists is a recording

`README.md:41` embeds `demo.gif`. It is good, and it is not clickable — a reader cannot
hover a panel, change a time range, or find the alert that is firing.

### The traffic source already exists and points at the wrong thing

`README.md:5-6` badge the two catalog pages, and `README.md:70-71` link them again in prose.
Their source lives in `manifests/dashboards/gpu-sim-dcgm.grafana-com.md` and
`manifests/dashboards/llm-sim-overview.grafana-com.md`, derived by `task dashboards`.

⚠️ **`CLAUDE.md` rule 9 governs any edit to those pages: republish as a *revision* of the
same id, never a new upload.** Ids **25618** (GPU) and **25620** (LLM) do not change. Both
files are also in `EM_DASH_FREE` (`scripts/check-doc-claims.py`), so `task doc-claims`
checks the prose you add.

### The stack is already 90% a public demo, and 10% a security incident

`compose/compose.yaml` is the candidate, run by `task compose` (`Taskfile.yml:438-441`):

| Line | What it already does |
|---|---|
| `:113-114` | `GF_AUTH_ANONYMOUS_ENABLED: "true"`, `GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer` |
| `:108` | `grafana/grafana:11.6.0` — the boards' kiosk-mode URL syntax is Grafana 11 |
| `:109` | port 3000, overridable via `GRAFANA_PORT` |

⚠️ **`compose/compose.yaml:115` is `GF_SECURITY_ADMIN_PASSWORD: admin`.** That is correct
for a localhost demo and disqualifying the moment the port is public. It is the single
thing most likely to be missed, because everything around it is already right.

### What the simulator gives you for free here

It polls its profile every 10s and applies changes **without restarting** (`CLAUDE.md`, Map).
A public demo can therefore be made to move between states on a schedule without a redeploy,
which is the difference between a live board and a screenshot with a clock on it.

## W1 — A board on the public internet, read-only

**W1.1 Pick the cheapest host that keeps the stack intact.** Two candidates, and the
decision is empirical rather than architectural:

- **A small VM running `task compose`.** Self-contained, one box, no data leaves it, and it
  is the same artefact CI already exercises. Preferred unless it costs more than the
  alternative.
- **Grafana Cloud free tier.** Removes the Grafana operational burden but requires an
  always-on producer plus `remote_write`, which is a new path this repo does not have and
  would then have to keep working.

Price both for a month before choosing. Record the choice and the number in this file.

**W1.2 Anonymous viewer access only.** Already configured at `compose/compose.yaml:113-114`.
Confirm from a logged-out browser, not from your own session.

**W1.3 Both boards reachable, GPU and LLM.** The home dashboard is currently the GPU board
(`compose/compose.yaml:118`). A stranger arriving from the **25620** catalog page must land
on the LLM board, so either the link carries the path or the home board changes. Decide
which, and say why here.

**W1.4 ⚠️ The hardening pass, written as a list before you expose the port.** At minimum:
`GF_SECURITY_ADMIN_PASSWORD` off the default and out of the file; the admin login reachable
only over a private path or not at all; Grafana's own `/api` not writable anonymously;
Prometheus **not** exposed publicly, only Grafana. Write the list first, then check it, then
open the port. That ordering is rule 6 applied to a firewall.

**W1.5 Make it move.** A board with a flat line reads as broken. Rotate the driven tenant
through its profiles on a schedule so an arriving reader sees change within a minute.
⚠️ Rule 1: this targets `llm-driven` in `manifests/llm/extras/`. `llm-steady` and
`llm-saturated` are fixtures and are not touched, even here.

## W2 — Point the three doors at it

**W2.1 The repository homepage and the README.** `gh repo edit --homepage <url>`, and a link
in the README above the fold, near `README.md:41` where the GIF already is. Keep the GIF:
it works where a live link cannot, in a search result and on a phone.

**W2.2 ⚠️ Both catalog pages — the irreversible one.** Add the demo link to
`manifests/dashboards/*.grafana-com.md`, re-run `task dashboards`, and upload each as a
**revision of its existing id**. A new upload orphans the badge, the README link, the
ratings and the install count. Read rule 9 before you touch the upload form.

**W2.3 State the funnel you expect.** Catalog page → live board → repo. Writing it down is
what makes W3's seeded issues answerable rather than decorative.

## W3 — Open the doors that are shut

**W3.1 Turn Discussions on.** `gh repo edit --enable-discussions`. It is currently false and
there is no reason for it to be.

**W3.2 Seed `good first issue`s from work that already exists.** ⚠️ Do not invent them.
`task outstanding` lists five marked items today, and `ROADMAP.md` items 1 and 2 decompose
into pieces a stranger could take. An invented starter issue is the same failure class as an
invented number.

**W3.3 The README's opening claim.** `prompt-adoption.md` argued the README should lead with
the reader's problem — you cannot test GPU or LLM alerting without a GPU, so your thresholds
are untested — rather than with what the project is. That argument survives its source
document and is recorded here so it is not lost. It is a judgement call, not a defect, and
it is explicitly optional.

## Non-goals

- **Any change to what the rig does.** This is distribution. If a W-item starts editing
  `scripts/llm-sim.py`, it has escaped.
- **A documentation site.** Thirteen files in `docs/` are navigable. Premature, and
  `prompt-adoption.md` said so first at eight.
- **Chasing metric-count parity.** A visible, checked gap beats an unverified full set.
  `check-vllm-buckets.py` reports the distance honestly and that is the design.
- **k3d / minikube support.** Correctly a non-goal elsewhere; each target multiplies the
  matrix.
- **Paid hosting beyond the smallest thing that works.** If the demo costs enough to think
  about, it will be turned off in three months and the links will rot.
- **Analytics or tracking on the demo.** Nothing here is worth a cookie banner.

## Acceptance criteria

Written before the work, per house rule. Each is checkable by someone who did not do it.

1. A URL loads both boards in a logged-out private window, with data timestamped inside the
   last five minutes, and no login prompt.
2. `gh repo view --json homepageUrl` returns that URL.
3. The demo link appears in the README above the fold.
4. Both catalog pages carry the link, and `grafana.com` still serves them at **25618** and
   **25620**, with their revision counts incremented rather than reset.
5. `gh repo view --json hasDiscussionsEnabled` returns `true`.
6. At least three open issues carry `good first issue`, each traceable to a marked item or a
   ROADMAP decomposition, none invented for the purpose.
7. The W1.4 hardening list exists in this file, written before exposure, with each line
   checked off against the running host.
8. `task preflight` is green — the catalog page edits are inside `doc-claims`.
9. Anonymous access cannot write: an unauthenticated `POST` to the Grafana API is refused.
   Drive it to failure once, per rule 18, rather than assuming the default.

## Process

One logical change per commit (rule 11). W1 is at least two: the host, then the hardening.

⚠️ **W2.2 lands last and alone.** It is the only step that touches an artefact outside this
repository, and the only one that cannot be reverted with `git revert`. Everything else
should be green and stable before a catalog page changes.

⚠️ **Re-derive the W1 host estimate before starting.** It is the largest line on the effort
table, it is the one nobody here has done, and per house rule the largest line is the one
that gets re-derived rather than inherited.
