#!/usr/bin/env python3
"""chart-build.py — assemble the installable Helm chart into dist/.

    python3 scripts/chart-build.py          # build into dist/charts/
    task chart                              # the same, plus `helm lint`

WHY THERE IS A BUILD STEP AT ALL — this is W-C2, and it is the hard part.

Helm's `.Files.Get` cannot read outside the chart directory. So a chart under
charts/ cannot reference manifests/dashboards/*.json or manifests/alerts/*.yaml
where those files live. There were three ways out and only one of them keeps
this repo's central rule:

  (a) BUILD STEP — assemble the chart into gitignored dist/ from the canonical
      files, on the same terms as scripts/dashboard-publish.py. THIS IS WHAT
      THIS FILE DOES.
  (b) symlinks under charts/.../files/ — `helm package` and `git archive` follow
      them inconsistently across platforms.
  (c) generate and COMMIT the copies, with a CI check that they still match.

(a) is the only option where the second copy NEVER EXISTS IN THE TREE. That
matters more here than the convenience (c) would buy: a drifted copy of a
dashboard or a rule file is invisible from the outside, and this repo refuses
second copies everywhere else — the DCGM surface contract, the dashboards, the
simulator image, all one source and several derived forms.

THE COST, stated plainly because it surprises people:

    helm install ./charts/k8s-ai-observability     # DOES NOT WORK

charts/ holds templates with no files beside them. The installable chart is the
BUILT one:

    task chart
    helm install rig dist/charts/k8s-ai-observability

The chart's own assertions fail with that instruction rather than with a
template error, so someone who tries the obvious thing is told the right one.

⚠️ scripts/llm-sim.py IS NOT COPIED, and that is new. It used to be the hardest
item on this list — executable code, the one file a drifted copy of would be
genuinely dangerous. The published container image removed it from the problem
entirely: a chart whose simulator Deployment references an image needs a TAG in
values.yaml, not the file. What is left is dashboards and rules, both static
JSON/YAML, which is what makes option (a) cheap rather than merely correct.

⚠️ THE PROFILES ARE NOT COPIED EITHER, for a different reason. The chart
TEMPLATES them from values.yaml, so the numbers are genuinely reachable; copying
manifests/llm/10-profiles.yaml in would freeze every one of them at its default
while appearing configurable.

THE RULE EXTRACTION IS NOT REIMPLEMENTED HERE. scripts/extract.sh already
unwraps the PrometheusRule custom resources into plain rule files, and is what
promtool and the compose stack both read. Two copies of that transformation is
how the rules promtool tests and the rules Prometheus loads start to differ, so
this shells out to it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
CHART_NAME = "k8s-ai-observability"
SRC_CHART = os.path.join(ROOT, "charts", CHART_NAME)
OUT_ROOT = os.path.join(ROOT, "dist", "charts")
OUT_CHART = os.path.join(OUT_ROOT, CHART_NAME)

DASHBOARDS = os.path.join(ROOT, "manifests", "dashboards")
ALERTS = os.path.join(ROOT, "manifests", "alerts")


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read_chart_field(field, text=None):
    """One top-level scalar out of Chart.yaml, without a YAML parser.

    The repo has no PyYAML dependency and this script is release tooling — it
    has to run anywhere `task chart` does, which is the same "stdlib only"
    constraint the simulator carries. Two fields, both plain scalars at column
    zero, so a regex is honest here rather than a shortcut.
    """
    path = os.path.join(SRC_CHART, "Chart.yaml")
    if text is None:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    m = re.search(rf'^{field}:\s*"?([^"\s#]+)"?\s*$', text, re.M)
    if not m:
        fail(f"could not read `{field}:` from {path}")
    return m.group(1)


def values_image_tag(text=None):
    """The pinned simulator tag in values.yaml, or "" for 'use appVersion'."""
    path = os.path.join(SRC_CHART, "values.yaml")
    if text is None:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    # Scoped to the llm.image block rather than the first `tag:` anywhere.
    block = re.search(r"^  image:\n((?:    .*\n)+)", text, re.M)
    if not block:
        fail(f"could not find the llm.image block in {path}")
    m = re.search(r'^    tag:\s*"?([^"\s#]*)"?\s*(?:#.*)?$', block.group(1), re.M)
    if m is None:
        fail(f"could not read llm.image.tag from "
             f"{path if text is None else '<fixture>'}")
    return m.group(1)


def release_tag():
    """The repo's current release tag, from git.

    Best-effort: a shallow CI checkout or a tree with no tags yet has none, and
    that is not a failure — it is an absence of one. The cross-check below is
    skipped rather than guessed at.
    """
    try:
        out = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                             cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    tag = out.stdout.strip()
    return tag or None


def check_image_tag_agrees(app_version, strict):
    """⚠️ THE COUPLING THE IMAGE INTRODUCED, AND THE ONE NOTHING ELSE ENFORCES.

    The chart's default simulator tag and the repo's release tag must agree. A
    chart pinned to a stale tag INSTALLS CLEANLY AND RUNS AN OLD SIMULATOR — a
    green install with something silently wrong, which is the failure class this
    repo builds assertions against. Nothing about the Helm render can catch it:
    an old tag is a perfectly valid image reference.

    Two things are checked:
      * values.yaml's llm.image.tag is either empty (meaning "use appVersion",
        which is the coupling working by construction) or exactly appVersion;
      * appVersion matches the repo's own latest tag.
    """
    pinned = values_image_tag()
    if pinned and pinned != app_version:
        fail(f"charts/{CHART_NAME}/values.yaml pins llm.image.tag={pinned!r} but "
             f"Chart.yaml says appVersion={app_version!r}.\n"
             f"       A chart on a stale tag installs green and runs an old simulator.\n"
             f"       Leave the tag EMPTY to track appVersion, or move them together.")
    if not pinned:
        print(f"  ok    llm.image.tag is empty -> tracks appVersion ({app_version})")
    else:
        print(f"  ok    llm.image.tag {pinned} == appVersion")

    tag = release_tag()
    if tag is None:
        print("  note  no git tag readable here; skipping the release cross-check.\n"
              "        That is an absence of a result, not a passing one.")
        return
    if tag != app_version:
        msg = (f"Chart.yaml appVersion={app_version!r} but the repo's latest tag is "
               f"{tag!r}.\n"
               f"       appVersion is what the simulator image tag defaults to, so this "
               f"chart\n"
               f"       would install and run the {app_version} simulator on a {tag} repo.")
        if strict:
            fail(msg)
        print(f"  WARN  {msg}\n"
              f"        Not fatal here — appVersion is bumped as part of cutting a\n"
              f"        release, so it legitimately leads the tag on an unreleased\n"
              f"        branch. The publish workflow checks it strictly, where it is\n"
              f"        the last chance to catch it.")
    else:
        print(f"  ok    appVersion {app_version} == the repo's latest tag")


def shipped_chart_versions(also_exclude=()):
    """Chart versions carried by the repo's own release tags: {version: [tags]}.

    The registry is the real authority on what has been pushed, and it is exactly
    the authority this must not need. A check that requires network and
    credentials does not run on a laptop, does not run offline, and reports at
    `helm push` — the last possible moment, after the tag exists and the release
    is already cut. Git answers the same question locally, because every publish
    is driven by a tag and the tag carries the Chart.yaml that shipped with it.

    Tags from before the chart existed have no Chart.yaml and are skipped: an
    absent file is an absence of a version, not a version of "".
    """
    try:
        out = subprocess.run(["git", "tag"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None

    # ⚠️ EXCLUDE THE TAG BEING RELEASED, OR THE CHECK COLLIDES WITH ITSELF. At
    # release time the new tag points at HEAD and carries the very version being
    # published, so counting it would reject EVERY release — the guard firing on
    # its own subject, permanently. Caught by running the documented pre-push
    # check on the first release after this guard landed, which is the one moment
    # it was ever going to show up.
    #
    # Only tags at HEAD are skipped, so an OLDER tag carrying this version is
    # still a genuine collision and still fails. On a workflow_dispatch from a
    # branch nothing points at HEAD, nothing is skipped, and a version that
    # already exists is still refused.
    try:
        at_head = subprocess.run(["git", "tag", "--points-at", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=10)
        being_released = set(at_head.stdout.split()) if at_head.returncode == 0 else set()
    except (OSError, subprocess.SubprocessError):
        being_released = set()
    # ⚠️ AND THE TAG BEING PUBLISHED AS, which is NOT always the one at HEAD. A
    # publish that fails partway — a transient registry error, or a bug in this
    # workflow — is retried by dispatching against the SAME release tag from a
    # branch that has since moved on. HEAD is then a later commit, the release
    # tag is no longer at HEAD, and without this the guard would refuse the
    # retry and force a version to be burned over a fault that published
    # nothing. Exactly that happened on v0.8.0.
    being_released |= {t for t in also_exclude if t}

    seen = {}
    for tag in (t.strip() for t in out.stdout.splitlines() if t.strip()):
        if tag in being_released:
            continue
        try:
            blob = subprocess.run(
                ["git", "show", f"{tag}:charts/{CHART_NAME}/Chart.yaml"],
                cwd=ROOT, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if blob.returncode != 0:
            continue                      # chart did not exist at this tag
        m = re.search(r"^version:\s*(\S+)", blob.stdout, re.M)
        if m:
            seen.setdefault(m.group(1), []).append(tag)
    return seen


def version_is_reused(chart_version, seen, publishing_as=None):
    """Which tags, other than the one being published, already carry this version.

    Split out from check_version_not_reused so the DECISION can be tested without a
    git repository — the two bugs this guard has had were both decisions, not I/O:
    it counted the tag being released against itself, and it could not be told about
    a version published off-tag.
    """
    tags = [t for t in seen.get(chart_version, []) if t != publishing_as]
    return sorted(tags)


def check_version_not_reused(chart_version, strict, publishing_as=None):
    """⚠️ A REGISTRY VERSION CANNOT BE RE-USED, CORRECTED, OR OVERWRITTEN.

    The failure this prevents is not subtle, it is just late: the chart version
    is not bumped as part of cutting a release, the tag is pushed, the workflow
    builds a perfectly good package, and the push is rejected for a version that
    already exists. By then the tag is public and the release is out.

    ⚠️ THIS IS DELIBERATELY STRICTER THAN THE REGISTRY, and the message says so
    rather than claiming more than it knows. It fires on any version already
    carried by a tag, including the four tags that shipped 0.1.0 before anything
    was ever published. Those would not collide on a push today — but re-using
    one still contradicts the bump policy in Chart.yaml, and "it happens not to
    have been published yet" is a reason that expires the first time it is.

    WARN by default and FAIL under --strict-version, for the same reason
    appVersion does: on an unreleased branch the version legitimately still
    equals the last released one, right up until the release commit bumps it.

    ⚠️ IT ONLY KNOWS VERSIONS CARRIED BY A TAG, AND THAT IS A REAL HOLE.
    publish-chart.yml can be dispatched from a branch with push_to_registry=true,
    which publishes a version no tag carries — chart 0.2.1 was published exactly
    that way on 2026-08-04, after the tagged publish of 0.2.0 failed. Re-using
    0.2.1 would sail past this check and be rejected by the registry instead,
    which is the failure this exists to move earlier. The escape hatch is
    deliberate and explicit (push_to_registry defaults false), so this is
    recorded rather than closed: closing it needs a record of published versions
    that git alone cannot provide.
    """
    seen = shipped_chart_versions(also_exclude=(publishing_as,))
    if seen is None:
        print("  note  no git tags readable here; skipping the re-use check.\n"
              "        That is an absence of a result, not a passing one.")
        return
    tags = version_is_reused(chart_version, seen, publishing_as)
    if not tags:
        print(f"  ok    chart version {chart_version} is not carried by any existing "
              f"tag ({len(seen)} version(s) seen)")
        return
    msg = (f"chart version {chart_version} already shipped at "
           f"{', '.join(sorted(tags))}.\n"
           f"       Registry versions are immutable, so publishing this again is\n"
           f"       rejected — bump `version:` in charts/{CHART_NAME}/Chart.yaml.\n"
           f"       Note a release publishes the chart even when no template\n"
           f"       changed, so the bump is required either way.")
    if strict:
        fail(msg)
    print(f"  WARN  {msg}\n"
          f"        Not fatal here — the version legitimately still equals the last\n"
          f"        released one until the release commit bumps it. The publish\n"
          f"        workflow checks it strictly, where it is the last chance.")


def check_dependency_pins_agree():
    """⚠️ THE TWO CHART VERSIONS ARE NOW PINNED IN TWO PLACES.

    `scripts/config.sh` pins kube-prometheus-stack and fake-gpu-operator for the
    SCRIPT install path; `charts/k8s-ai-observability/Chart.yaml` pins the same
    two as dependencies for the CHART path. Helm cannot read a shell variable and
    the shell cannot read Chart.yaml, so unlike the dashboards and the rules this
    duplication cannot be assembled away — it is genuinely two copies of one
    number.

    That makes it exactly the kind this repo refuses to leave unchecked. Bump one
    and not the other and BOTH installs still succeed, while the two paths
    silently deploy different versions of the same operator: `verify.sh` passes on
    each, CI passes on each, and the only symptom is that a chart bump verified
    through one path was never verified through the other. The fake-gpu-operator
    pin is the dangerous half — `config.sh` records that this repo hard-codes
    facts true of 0.0.59 specifically (the exporter's three series, the
    ServiceMonitor's selector, the labels the dashboards join on), none of which
    has a plan-time check.

    So the copies exist and the divergence fails here, which is the option W-C2
    demands whenever a second copy is unavoidable.
    """
    cfg = os.path.join(ROOT, "scripts", "config.sh")
    with open(cfg, "r", encoding="utf-8") as fh:
        cfg_text = fh.read()
    with open(os.path.join(SRC_CHART, "Chart.yaml"), "r", encoding="utf-8") as fh:
        chart_text = fh.read()

    for shell_var, dep in (("KPS_CHART_VERSION", "kube-prometheus-stack"),
                           ("FAKE_GPU_CHART_VERSION", "fake-gpu-operator")):
        m = re.search(rf'^{shell_var}="([^"]+)"', cfg_text, re.M)
        if not m:
            fail(f"could not read {shell_var} from {cfg} — has it been restructured? "
                 f"Without it this check cannot compare the two pins.")
        want = m.group(1)
        # The `version:` belonging to this dependency's block, not the first one
        # in the file: the two entries are structurally identical and matching
        # loosely would compare kube-prometheus-stack against fake-gpu-operator's
        # pin and pass on a coincidence.
        d = re.search(rf'^  - name: {re.escape(dep)}\n((?:    .*\n)+)', chart_text, re.M)
        if not d:
            fail(f"no `- name: {dep}` dependency found in Chart.yaml.")
        v = re.search(r'^    version:\s*"?([^"\s#]+)"?', d.group(1), re.M)
        if not v:
            fail(f"the {dep} dependency in Chart.yaml has no version.")
        if v.group(1) != want:
            fail(f"chart dependency pin disagrees with scripts/config.sh:\n"
                 f"         Chart.yaml   {dep} => {v.group(1)}\n"
                 f"         config.sh    {shell_var} => {want}\n"
                 f"       The script path and the chart path would install DIFFERENT\n"
                 f"       versions of the same operator, and both installs would still\n"
                 f"       go green. Move them together.")
        print(f"  ok    {dep} {want} matches config.sh {shell_var}")


def copy_dashboards(files_dir):
    out = os.path.join(files_dir, "dashboards")
    os.makedirs(out, exist_ok=True)
    boards = sorted(f for f in os.listdir(DASHBOARDS) if f.endswith(".json"))
    if not boards:
        fail(f"no dashboards found in {DASHBOARDS}")
    for name in boards:
        src = os.path.join(DASHBOARDS, name)
        # Parsed rather than merely copied, for the same reason
        # assert_dashboard_contract parses it: install.sh wraps every .json in
        # this directory into a ConfigMap, and a malformed one fails the apply
        # PARTWAY THROUGH. Catching it here means the chart is never built
        # around a board that cannot load.
        with open(src, "r", encoding="utf-8") as fh:
            try:
                doc = json.load(fh)
            except ValueError as exc:
                fail(f"{src} is not valid JSON: {exc}")
        uid = doc.get("uid")
        stem = name[:-len(".json")]
        if uid != stem:
            fail(f"{src} declares uid={uid!r} but its filename says {stem!r}.\n"
                 f"       Grafana serves /d/<uid> from the JSON while every link to the\n"
                 f"       board is built from the filename, so a mismatch is a confident\n"
                 f"       link to a 404. Rename the file or change the uid — not neither.")
        shutil.copy2(src, os.path.join(out, name))
        print(f"  dashboards/{name}   uid={uid}")
    return len(boards)


def copy_rules(files_dir):
    out = os.path.join(files_dir, "rules")
    os.makedirs(out, exist_ok=True)
    # extract.sh, not a reimplementation — see the module docstring.
    subprocess.run([os.path.join(HERE, "extract.sh"), "rules", out],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    produced = sorted(f for f in os.listdir(out) if f.endswith("-rules.yaml"))
    if not produced:
        fail("scripts/extract.sh produced no rule files from manifests/alerts/.")
    for name in produced:
        path = os.path.join(out, name)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        # Silently producing nothing is the one failure a build step must not
        # have: the chart would install, every object would report success, and
        # not a single rule would exist.
        if not re.search(r"^groups:", text, re.M):
            fail(f"{name} has no top-level `groups:` — is `spec:` still top-level "
                 f"in manifests/alerts/?")
        n = len(re.findall(r"^\s+- (record|alert):", text, re.M))
        if not n:
            fail(f"{name} contains no rules.")
        print(f"  rules/{name}   {n} rules")
    return len(produced)


def assert_no_committed_copies():
    """⚠️ The rule this whole build step exists to keep.

    If a dashboard, a rule file or the simulator ever appears under charts/,
    the single-source-of-truth constraint has been quietly abandoned and the
    build step is doing nothing. Checked here rather than only in CI, so it
    fails for whoever introduced it rather than for the next person.
    """
    offenders = []
    for dirpath, _dirnames, filenames in os.walk(SRC_CHART):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            if name.endswith(".json") and name != "values.schema.json":
                offenders.append(rel)
            if re.match(r"llm[-_]sim\.py$", name):
                offenders.append(rel)
            if name.endswith("-rules.yaml") or name.endswith("-prometheusrule.yaml"):
                offenders.append(rel)
    if offenders:
        fail("a second copy of a canonical file is committed under charts/:\n"
             + "\n".join(f"         {o}" for o in offenders)
             + "\n       These are assembled into dist/ by this script precisely so no\n"
               "       copy exists in the tree. Delete them.")


def fetch_dependencies(skip):
    """Vendor the two conditional dependencies into the BUILT chart.

    ⚠️ NOT OPTIONAL, however much it looks like it should be. Helm refuses to
    render a chart whose Chart.yaml declares a dependency that is absent from
    charts/, EVEN WHEN ITS CONDITION IS FALSE:

        Error: found in Chart.yaml, but missing in charts/ directory:
        kube-prometheus-stack, fake-gpu-operator

    The condition gates whether the subchart's TEMPLATES render, not whether the
    archive has to be there. So `helm template`, `helm lint` and `helm install`
    all fail on the default values — the very configuration this chart exists to
    serve — until this has run.

    They land in dist/ rather than in charts/, which is the whole point: nothing
    vendored is ever committed. Needs network, exactly once per build.
    """
    if skip:
        print("\n  note  --skip-deps: dependencies NOT fetched.\n"
              "        `helm template`/`lint`/`install` on this build will fail with\n"
              "        \"missing in charts/ directory\" even with both conditions false.\n"
              "        That is Helm's behaviour, not a fault in the build.")
        return
    if not shutil.which("helm"):
        fail("helm not found on PATH, and the built chart cannot render without its\n"
             "       dependency archives (Helm requires them present even when the\n"
             "       condition is false). Install helm, or pass --skip-deps if you only\n"
             "       want the assembled files.")
    print("\nfetching the conditional dependencies (needs network):")
    try:
        subprocess.run(["helm", "dependency", "update", OUT_CHART],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        fail("`helm dependency update` failed — see the output above.\n"
             "       Both repositories must be reachable:\n"
             "         https://prometheus-community.github.io/helm-charts\n"
             "         https://runai.jfrog.io/artifactory/api/helm/fake-gpu-operator-charts-prod")
    vendored = sorted(f for f in os.listdir(os.path.join(OUT_CHART, "charts"))
                      if f.endswith(".tgz"))
    for name in vendored:
        print(f"  charts/{name}")


# --------------------------------------------------------------------------- selftest
# ⚠️ THIS FILE HAD THREE BUGS BEFORE IT HAD A TEST, and all three were DECISIONS
# rather than I/O — which is why the fixtures below are text and lists, not files:
#
#   * `values.yaml` was grepped for `tag:` with a regex requiring at least one
#     character. That cannot match `tag: ""`, which is the value meaning "track
#     appVersion" and therefore the CORRECT one. Under `set -e` it would have failed
#     every properly configured release. (Fixed before it shipped; pinned here.)
#   * the re-use guard counted the tag being released against itself, so it could
#     never pass — it rejected v0.8.0 for a version only v0.8.0 carried.
#   * the same guard cannot see a version published off-tag by workflow_dispatch,
#     which is how 0.2.1 became invisible to it. Still open, and marked in
#     check_version_not_reused where it lives.
#
# Every checker in scripts/ has a --selftest. This one did not, and it is the one
# that produced the bugs.

VALUES_FIXTURE_EMPTY = """\
llm:
  image:
    repository: ghcr.io/example/sim
    tag: ""            # "" -> .Chart.AppVersion
    pullPolicy: IfNotPresent
"""

VALUES_FIXTURE_PINNED = """\
llm:
  image:
    repository: ghcr.io/example/sim
    tag: "v1.2.3"
    pullPolicy: IfNotPresent
"""

CHART_FIXTURE = """\
apiVersion: v2
name: example
version: 0.4.2
appVersion: "v1.2.3"
"""


def selftest():
    print("chart-build --selftest")

    # ⚠️ The empty tag is the CORRECT configuration, and the regex that could not
    # express it is the bug this pins. "" must parse as "", not as a failure.
    assert values_image_tag(VALUES_FIXTURE_EMPTY) == "", values_image_tag(VALUES_FIXTURE_EMPTY)
    assert values_image_tag(VALUES_FIXTURE_PINNED) == "v1.2.3"
    print('  ok  tag        `tag: ""` reads as empty (tracks appVersion), a pin reads as itself')

    assert read_chart_field("version", CHART_FIXTURE) == "0.4.2"
    assert read_chart_field("appVersion", CHART_FIXTURE) == "v1.2.3"
    print("  ok  fields     version and appVersion parse, quoted or bare")

    # The re-use decision, with no git anywhere near it.
    seen = {"0.1.0": ["v0.5.0", "v0.6.0"], "0.2.0": ["v0.8.0"]}
    assert version_is_reused("0.2.0", seen, publishing_as="v0.8.0") == []
    assert version_is_reused("0.2.0", seen, publishing_as=None) == ["v0.8.0"]
    assert version_is_reused("0.1.0", seen, publishing_as="v0.8.0") == ["v0.5.0", "v0.6.0"]
    assert version_is_reused("0.3.0", seen, publishing_as=None) == []
    print("  ok  re-use     the tag being published is excluded; an OLDER tag still fails")

    print("\nSELFTEST PASSED")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true",
                    help="check this script's own parsing and decisions against "
                         "fixtures, and exit")
    ap.add_argument("--strict-version", action="store_true",
                    help="fail rather than warn when Chart.yaml appVersion does "
                         "not match the repo's latest git tag (used at release)")
    ap.add_argument("--publishing-as", metavar="TAG", default=None,
                    help="the release tag this build is being published as. "
                         "Excluded from the already-shipped set, so re-running a "
                         "publish that failed before it pushed anything does not "
                         "require burning a chart version.")
    ap.add_argument("--skip-deps", action="store_true",
                    help="do not fetch the conditional dependencies (offline). The "
                         "result will NOT render — Helm needs them present even "
                         "when their condition is false.")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if not os.path.isdir(SRC_CHART):
        fail(f"{SRC_CHART} not found (run from the repo root)")

    print(f"assembling {CHART_NAME} into dist/charts/\n")
    assert_no_committed_copies()

    app_version = read_chart_field("appVersion")
    chart_version = read_chart_field("version")
    print(f"chart {chart_version}, appVersion {app_version}")
    check_image_tag_agrees(app_version, args.strict_version)
    check_version_not_reused(chart_version, args.strict_version, args.publishing_as)
    check_dependency_pins_agree()
    print()

    # Rebuilt from scratch every time: a file deleted from manifests/ must
    # disappear from the built chart, and an incremental copy would leave it
    # behind to be installed forever.
    if os.path.isdir(OUT_CHART):
        shutil.rmtree(OUT_CHART)
    shutil.copytree(SRC_CHART, OUT_CHART,
                    ignore=shutil.ignore_patterns(".helmignore~", "*.swp", "charts", "*.tgz"))

    files_dir = os.path.join(OUT_CHART, "files")
    os.makedirs(files_dir, exist_ok=True)
    n_boards = copy_dashboards(files_dir)
    n_rules = copy_rules(files_dir)
    fetch_dependencies(args.skip_deps)

    print(f"\n{CHART_NAME} {chart_version} ready:")
    print(f"  {os.path.relpath(OUT_CHART, ROOT)}")
    print(f"  {n_boards} dashboard(s), {n_rules} rule file(s)")
    print()
    print("Install the BUILT chart — ./charts/... has templates with no files beside")
    print("them, which is the cost of never committing a second copy:")
    print()
    print(f"  helm install rig {os.path.relpath(OUT_CHART, ROOT)} \\")
    print("    --set releaseLabel=<your monitoring release>")
    print()
    print("  helm test rig --logs      # <- the two silent labels are checked HERE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
