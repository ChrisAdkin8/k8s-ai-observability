<!--
One logical change per commit (CONTRIBUTING.md). If this PR does two things, it
is probably two PRs — the changelog entry is easier to write when it is one.
-->

## What changed, and why

<!-- The reasoning, not the diff. Reviewers can read the diff. -->

## Checks

- [ ] `task preflight` passes — selftests, rule tests, doc claims, chart
- [ ] Behaviour change? `CHANGELOG.md` entry added under `[Unreleased]`
- [ ] Numbers in prose still match the code they describe (`task doc-claims`)
- [ ] Cluster path touched? verified with `./scripts/verify.sh <target>`

<!--
⚠️ Two things here fail SILENTLY and CI cannot catch either:

  * A dashboard panel change makes the README screenshots stale, and the social preview
    card cropped from docs/llm-dashboard.png with it — CONTRIBUTING.md, "Editing
    dashboards".
  * A dashboard change does not reach anyone who imported the board by id until
    it is re-uploaded to grafana.com as a REVISION of the existing id, never as a
    new dashboard — manifests/dashboards/README.md.
-->
