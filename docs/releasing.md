# Cutting a release

Four releases were cut on 2026-08-04 and three of them hit a bug in the release procedure
itself. Not in the rig, not in the chart — in the steps below. This page exists because
that knowledge was living in commit messages, which is the wrong place for something you
follow at the end of a long day.

The short version: **tag locally, verify, then push.** Everything else follows from that.

---

## The sequence

### 1. Write the release entry

Move the `[Unreleased]` content in [`CHANGELOG.md`](../CHANGELOG.md) into a
`## [X.Y.Z] — <date>` section, and update the two links at the foot of the file:

```
[Unreleased]: .../compare/vX.Y.Z...HEAD
[X.Y.Z]:      .../compare/v<previous>...vX.Y.Z
```

Pick MAJOR/MINOR/PATCH from the table at the top of the CHANGELOG, and justify the choice
in the commit body. Commit as `Draft the X.Y.Z entry`.

### 2. The release commit

Bump `appVersion` in [`Chart.yaml`](../charts/k8s-ai-observability/Chart.yaml) to the new
tag. Commit as `Release vX.Y.Z`.

**Decide the chart `version` deliberately.** The policy is written where the number is:
it moves on every release, because a release publishes the chart whether or not any
template changed, and a registry version that already exists is rejected.

⚠️ **`chart-build.py` only knows versions carried by a git tag.** A chart published by a
`workflow_dispatch` — as `0.2.1` was, after the tagged publish of `0.2.0` failed — is
invisible to it. Before choosing, ask the registry:

```sh
tok=$(curl -s "https://ghcr.io/token?scope=repository:chrisadkin8/charts/k8s-ai-observability:pull&service=ghcr.io" \
       | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $tok" \
  -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
  "https://ghcr.io/v2/chrisadkin8/charts/k8s-ai-observability/manifests/<version>"
```

`404` means free. `200` means taken, permanently.

### 3. Verify while it is still private

⚠️ **This is the step that saves releases, and it is the one that looks skippable.**

```sh
task preflight
git tag -a vX.Y.Z -m "vX.Y.Z — <what it is about>"
python3 scripts/chart-build.py --strict-version --skip-deps --publishing-as vX.Y.Z
```

Tag **first**, then run the strict check. It resolves the release tag with
`git describe --tags`, so it means nothing until the tag exists. If it fails,
`git tag -d vX.Y.Z`, fix, retry — nothing has left your machine.

⚠️ **The tag must sit ON the release commit, not before it.** `v0.7.0` was tagged ahead of
the `appVersion` bump, so the tag carried `appVersion: v0.6.0`. The guard caught it, but
only after the tag was public and had to be recreated.

### 4. Push, commit before tag

```sh
git push origin main && git push origin vX.Y.Z
```

### 5. Watch the two publish workflows

⚠️ **Both wait for CI to go green on the tagged commit before they build anything.** The
ruleset on `main` gates pull requests and says nothing about a tag, so until this existed
`git push origin vX.Y.Z` published from whatever that commit contained — and a registry
version is immutable, which is why the table below has four rows. The first step of each
workflow polls the check runs on the commit until every name in
`.github/required-checks.txt` has concluded. Since step 4 above pushes the branch and the
tag in one line, expect roughly the length of a CI run (about 5m30s, docs/ci.md) of
apparent inactivity before anything else happens. That is the gate doing its job.

If a required check was *skipped* on that commit — a docs-only change — the gate accepts it
the way a ruleset would, and says so with a warning naming which.

The tag fires [`publish-image.yml`](../.github/workflows/publish-image.yml) and
[`publish-chart.yml`](../.github/workflows/publish-chart.yml). Both verify by *consuming*
what they published rather than trusting the push: the image is pulled back and scraped,
and the chart is pulled back **anonymously** — which is also the only way to establish that
the package is public — then installed against a foreign Prometheus with `helm test`
required to fail on the default `releaseLabel` and pass when it is set.

**First publish only:** ghcr.io packages default to private. The anonymous pull will fail
with a link to the visibility setting. That is expected once, and the workflow refuses to
call a private package a successful publish.

### 6. Create the GitHub release

```sh
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z — <title>" --notes-file <notes>
```

`--verify-tag` refuses to invent a tag that does not exist.

---

## What went wrong, so it does not again

| Release | What bit | Now prevented by |
|--|--|--|
| `v0.7.0` | tagged before the `appVersion` bump, so the tag carried the old version | step 3, tag then verify |
| `v0.8.0` | the chart publish failed on `tar tzf \| head` returning 141 on a correct archive | `check-sigpipe.py` |
| `v0.8.0` | the version guard counted the tag being released against itself, so it could never pass | `--publishing-as` |
| `v0.8.0` | `helm test --logs` exited 1 on a chart whose every hook succeeded | the `chart on kind` CI job |

`v0.9.0` was the first release where both publish workflows went green on the first
attempt. That is what the sequence above is worth.

---

## Retrying a failed publish

A publish that fails **before** the push step has published nothing, and the same version
can be retried. `publish-chart.yml` accepts a `workflow_dispatch` with an explicit
`version` and `push_to_registry`, which defaults to **false** — a rehearsal builds,
vendors, packages and inspects the archive, publishes nothing, and says so in the summary.

⚠️ A dispatch runs the workflow from the ref you choose, so a retry after a fix runs the
*fixed* workflow but builds from that ref rather than from the tag. If the chart content is
identical it does not matter; record it in the CHANGELOG either way, as `v0.8.0` did.

⚠️ That is also the case the **`allow_red_ci`** dispatch input exists for. A branch carrying
a workflow fix may have no completed CI run of its own, so the green-CI gate would refuse
it. Setting `allow_red_ci: true` publishes anyway and prints a `::warning::` saying the
release was not checked against a green run. It is the escape hatch, not the normal path —
if you find yourself reaching for it twice, the tag is on the wrong commit.
