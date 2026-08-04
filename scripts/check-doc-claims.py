#!/usr/bin/env python3
"""check-doc-claims.py — fail when a number in the prose drifts from the code it describes.

    python3 scripts/check-doc-claims.py            # check the working tree
    python3 scripts/check-doc-claims.py --selftest # test the matchers on fixtures

Every check here is a correction commit that already happened, mechanised. The 0.6.0
"Fixed" entry alone corrected SEVEN prose numbers in one release — alert count, emitted
metric count, the L-check range in two files, the PromQL query count, the `${datasource}`
reference count — and the changelog says why it matters: *"every one of them is a number a
reader checks this repo's credibility against."* The ITL caveat wrong by 2x reached a
PUBLISHED board the same way.

The pattern behind all of them: a fact stated in prose in more than one place, with no
machine comparing the copies. This script is the machine. It holds NO copy of any truth —
every expected value is derived at run time from the file that owns it.

WHAT IT CHECKS
  1. Dashboard ids — every grafana.com id in tracked markdown is one the dashboards README
     knows, from its catalog table or its prose. This is the 25619->25620 incident.
  2. Emitted metric count — vs `llm-sim.py --print`. The preemptions removal moved this
     16 -> 15 and two files needed hand-correction.
  3. LLM alert count — vs `- alert:` entries in the rule file. The SLO's two burn alerts
     moved it 4 -> 6.
  4. The `L1-Ln` verify.sh range — vs the highest L actually implemented. L9 shipped while
     two files still said L8. ⚠️ This one scans verify.sh itself as well as markdown: the
     stale range was in that script's own header.
  5. PromQL query count — vs the numbered queries in observability.md. Said eight, had
     nine since `ALERTS{alertstate="firing"}` was added.
  6. The pinned Kubernetes version — the README badge and docs/versions.md vs
     kind/gpu-sim.yaml. A badge is prose with a colour, and nothing else verified it.
  7. The chart's `helm test` kubectl image minor vs `config.sh` K8S_VERSION. kubectl
     supports the API server within +/-1 minor; this was 1.31 against 1.36.
  8. `${datasource}` reference count — vs the LLM board. Said 22, "true when that board had
     nine panels, 33 now".

WHAT IT DELIBERATELY DOES NOT CHECK
  * **The drift-check gap count** ("23 upstream metrics this simulator does not emit",
    CONTRIBUTING.md). It is NOT derivable offline: the only local upstream list is
    tests/fixtures/upstream-vllm-metric-names.txt, which its own header says is "a STUBBED
    upstream vLLM metric set — NOT a copy of the real one, and it must never be updated to
    track upstream". Deriving from it would compare the prose against a fiction and pass.
    The real number needs the network, which this script and `task preflight` refuse; it
    belongs to check-vllm-buckets.py's weekly job.
  * The "around 40" upstream series count — same reason, and the prose hedges it on purpose.
  * grafana.com revision currency — needs the network and an account; recorded as prose in
    manifests/dashboards/README.md instead.
  * CHANGELOG.md — it is the historical record. A superseded number THERE is history, not
    drift, so it is excluded from every scan.

⚠️ A CHECKER THAT FINDS NOTHING MUST DIE, NOT PASS. If a claim is reworded so its matcher
stops matching, the check is dead — and a dead check reporting green is the exact failure
genre this repo writes assertions against. Every check therefore treats "no claims found"
as fatal and names itself in the error, so the fix is to teach it the new phrasing rather
than to wonder why it went quiet. --selftest pins every matcher to a committed fixture so a
regex edit cannot quietly retire one.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS_README = "manifests/dashboards/README.md"
EXCLUDED = {"CHANGELOG.md"}  # append-only history; stale numbers there are the record

# Prose spells small numbers as words ("six alerts", "nine PromQL queries") and larger
# ones as digits ("33 of them"). Both forms have to be readable or the matchers miss
# exactly the claims that bit.
WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM = r"(\d+|" + "|".join(WORDS) + r")"


def to_int(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


def die(check: str, msg: str) -> None:
    print(f"check-doc-claims: FAIL [{check}]: {msg}", file=sys.stderr)
    sys.exit(1)


def read(rel: str) -> str:
    p = ROOT / rel
    if not p.is_file():
        die("setup", f"{rel} is missing — it owns a value the prose is checked against")
    return p.read_text()


# ---------------------------------------------------------------- derivations (truth)
# Each returns the single number the prose is compared against, derived from the file
# that owns it. None of them may fall back to a literal.

def derive_emitted() -> int:
    """Distinct vllm: families in one scrape at default settings."""
    out = subprocess.run([sys.executable, "scripts/llm-sim.py", "--print"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        die("emits", f"llm-sim.py --print exited {out.returncode}; cannot derive the "
                     f"metric count\n{out.stderr}")
    n = sum(1 for line in out.stdout.splitlines() if line.startswith("# HELP vllm:"))
    return n or die("emits", "--print rendered no vllm: families — the derivation is dead")


def derive_llm_alerts() -> int:
    n = len(re.findall(r"^\s*- alert:", read("manifests/alerts/llm-prometheusrule.yaml"),
                       re.MULTILINE))
    return n or die("alerts", "no `- alert:` entries in the LLM rule file — dead")


def derive_max_l() -> int:
    """Highest L-check verify.sh actually implements, from its pass/fail/skip strings.

    Read from the CALLS rather than the `# Ln.` section comments: a comment is prose and
    could itself be the thing that drifted. A check that reports a result is the check.
    """
    ns = [int(m) for m in re.findall(r'(?:pass|fail|skip) "L(\d+)', read("scripts/verify.sh"))]
    return max(ns) if ns else die("l-range", "no L-check pass/fail calls in verify.sh — dead")


def derive_promql_queries() -> int:
    """Numbered queries in the 'Queries to start with' block — `# 1.` … `# 9.`."""
    n = len(re.findall(r"^# \d+\.", read("docs/observability.md"), re.MULTILINE))
    return n or die("promql", "no numbered queries in observability.md — dead")


def derive_datasource_refs() -> int:
    n = read("manifests/dashboards/llm-sim-overview.json").count("${datasource}")
    return n or die("datasource", "no ${datasource} references on the LLM board — dead")


def derive_k8s_minor() -> str:
    """The repo's pinned Kubernetes MINOR, from config.sh.

    The chart's `helm test` image ships a kubectl, and kubectl supports the API server
    only within +/-1 minor. It was pinned at 1.31 against a cluster pinned at 1.36 —
    five minors out — and nothing caught it, because CI never runs `helm test`.
    """
    m = re.search(r'^K8S_VERSION="([\d.]+)"', read("scripts/config.sh"), re.MULTILINE)
    return m.group(1) if m else die("kubectl-skew",
                                    "no K8S_VERSION in config.sh — the derivation is dead")


CI_WORKFLOW = ".github/workflows/ci.yml"
CI_DOC = "docs/ci.md"
REQUIRED_CHECKS = ".github/required-checks.txt"


def ci_check_names(text: str = None) -> set:
    """Every status-check name ci.yml can produce, with matrix values expanded.

    ⚠️ THE MATRIX VALUE IS INTERPOLATED INTO THE NAME, which is what makes this
    worth deriving rather than eyeballing: `full stack on kind (${{ matrix.profile }})`
    is not a check anyone can require, `full stack on kind (lite)` is. A rename or a
    changed matrix value silently changes the strings the ruleset must match.

    Parsed with regex rather than a YAML library on purpose — this repo's scripts
    are stdlib-only (CLAUDE.md rule 14) and Python has no stdlib YAML. Only the
    `jobs:` section is scanned, so the `on:` triggers cannot be mistaken for jobs.
    """
    body = (read(CI_WORKFLOW) if text is None else text).split("\njobs:\n", 1)
    if len(body) != 2:
        die("ci-jobs", f"no `jobs:` section in {CI_WORKFLOW} — the derivation is dead")
    blocks = re.split(r"^  ([a-z][a-z0-9-]*):[ \t]*$", body[1], flags=re.M)
    names = set()
    for i in range(1, len(blocks), 2):
        job = blocks[i + 1]
        m = re.search(r"^    name:[ \t]*(.+?)[ \t]*$", job, re.M)
        if not m:
            continue                      # a job with no display name uses its key
        label = m.group(1)
        mx = re.search(r"^\s+matrix:\s*\n\s+(\w+):\s*\[([^\]]+)\]", job, re.M)
        if mx and "${{ matrix." in label:
            key = re.escape(mx.group(1))
            for v in (v.strip() for v in mx.group(2).split(",")):
                names.add(re.sub(r"\$\{\{\s*matrix\." + key + r"\s*\}\}", v, label))
        else:
            names.add(label)
    return names or die("ci-jobs", f"no named jobs found in {CI_WORKFLOW} — dead")


def derive_ci_job_count() -> int:
    """How many jobs ci.yml defines BEYOND `fast`.

    CONTRIBUTING.md counts them in prose, and that count went stale the moment a job
    was added — it said six when there were eight, and named `upstream-drift` as the
    only job absent from pull requests after `settings-drift` joined it. Exactly the
    prose-versus-code drift this file exists for, and the derivation was already here
    for the check names.
    """
    body = read(CI_WORKFLOW).split("\njobs:\n", 1)
    if len(body) != 2:
        die("ci-job-count", f"no `jobs:` section in {CI_WORKFLOW} — the derivation is dead")
    keys = re.findall(r"^  ([a-z][a-z0-9-]*):[ \t]*$", body[1], re.M)
    keys = [k for k in keys if k != "fast"]
    return len(keys) or die("ci-job-count", f"no jobs found in {CI_WORKFLOW} — dead")


def derive_ci_gated_count() -> int:
    """How many jobs `fast` GATES, which is not the same as how many jobs exist.

    ⚠️ TWO DIFFERENT NUMBERS, AND CONFLATING THEM IS THE EASY MISTAKE. `changes`,
    `upstream-drift` and `settings-drift` are jobs beyond `fast` that `fast` does not
    gate, so "jobs beyond fast" and "expensive jobs fast gates" differ — 8 against 5
    at the time of writing. Each claim gets its own derivation rather than one being
    quietly reused for the other.
    """
    body = read(CI_WORKFLOW).split("\njobs:\n", 1)
    if len(body) != 2:
        die("ci-gated", f"no `jobs:` section in {CI_WORKFLOW} — the derivation is dead")
    blocks = re.split(r"^  ([a-z][a-z0-9-]*):[ \t]*$", body[1], flags=re.M)
    n = 0
    for i in range(1, len(blocks), 2):
        m = re.search(r"^    needs:\s*\[([^\]]*)\]", blocks[i + 1], re.M)
        if m and "fast" in [x.strip() for x in m.group(1).split(",")]:
            n += 1
    return n or die("ci-gated", f"no job in {CI_WORKFLOW} lists `fast` in needs — dead")


def within_one_minor(claimed: str, derived: str) -> bool:
    """kubectl is supported within ONE minor of the API server, EITHER direction.

    This is the one check that is not an equality, and deliberately so: equality would
    assert something stricter than the truth. 1.35 against a 1.36 cluster is supported,
    so failing it would be a false alarm, and it would also make the pin unsatisfiable
    on any day the image publisher has not yet cut the exact matching minor. The bug
    this check exists for — 1.31 against 1.36, five minors out — is caught either way.
    """
    try:
        (cmaj, cmin), (dmaj, dmin) = (tuple(int(x) for x in s.split("."))
                                      for s in (claimed, derived))
    except ValueError:
        return False
    return cmaj == dmaj and abs(cmin - dmin) <= 1


def derive_kind_node_version() -> str:
    """The pinned kind node image — the version the README's badge asserts.

    A badge is prose with a colour: `kubernetes-v1.36.1` is hardcoded into an
    img.shields.io URL and nothing else verifies it, so a node-image bump leaves the
    most prominent line in the repo quietly lying. Same class as the ITL caveat that
    reached a published board.
    """
    m = re.search(r"kindest/node:v([\d.]+)", read("kind/gpu-sim.yaml"))
    return m.group(1) if m else die("k8s-version",
                                    "no kindest/node pin in kind/gpu-sim.yaml — dead")


# --------------------------------------------------------------------- claim checks
# pattern  : must capture the claimed number as group 1
# context  : the claim only counts on a line ALSO matching this — so a number about
#            something else is never dragged into the comparison
# extra    : non-markdown files to scan as well (L-range's stale copy was in verify.sh)

CLAIM_CHECKS = [
    # ⚠️ The context names WHAT IS BEING COUNTED, and must. `upstream|vllm` was too
    # loose: "Real vLLM only ever emits one surface" is a true sentence about metric
    # SURFACES (v0/v1) on a line that mentions vLLM, and it was dragged into a families
    # comparison on this check's first real run. Counted nouns only.
    dict(name="emits", pattern=rf"\bemits {NUM}\b", context=r"\b(metric|series|name)s?\b",
         derive=derive_emitted, unit="vllm: families the simulator emits",
         hint="fix the prose, or the simulator, so they agree"),
    dict(name="alerts", pattern=rf"\b{NUM} alerts?\b", context=r"llm",
         derive=derive_llm_alerts, unit="alerts in the LLM rule file",
         hint="count `- alert:` in manifests/alerts/llm-prometheusrule.yaml"),
    dict(name="l-range", pattern=r"\bL1[-–—]L(\d+)\b", context=r".",
         derive=derive_max_l, unit="highest verify.sh LLM check", extra=["scripts/verify.sh"],
         hint="verify.sh's own header counts too — it was stale last time"),
    dict(name="promql", pattern=rf"\b{NUM} PromQL queries\b", context=r".",
         derive=derive_promql_queries, unit="numbered queries in observability.md",
         hint="they are the `# N.` comments inside the promql block"),
    # Two shapes, one truth: the README badge (`kubernetes-v1.36.1-326ce5`) and
    # docs/versions.md's `kindest/node:v1.36.1` row. Neither is a count, hence cast=str.
    dict(name="k8s-version", pattern=r"(?:kubernetes-|kindest/node:)v([\d.]+)", context=r".",
         derive=derive_kind_node_version, cast=str, unit="pinned kind node image",
         hint="the badge and docs/versions.md must both match kind/gpu-sim.yaml"),
    # The chart's test image carries a kubectl; its MINOR must stay inside Kubernetes'
    # supported window around the cluster the repo pins. Captures "1.36" out of
    # "alpine/k8s:1.36.2" or "registry.k8s.io/kubectl:v1.36.2".
    #
    # ⚠️ The ONLY non-equality check here. `ok` widens it to the +/-1 minor Kubernetes
    # actually supports, because equality would fail a supported 1.35 and could be
    # unsatisfiable when the image publisher has not cut the matching minor yet.
    dict(name="kubectl-skew",
         pattern=r"(?:alpine/k8s|kubectl):v?(\d+\.\d+)\.\d+", context=r"image",
         derive=derive_k8s_minor, cast=str, extra=["charts/k8s-ai-observability/values.yaml"],
         ok=within_one_minor, relation="are within +/-1 minor of",
         unit="K8S_VERSION minor from config.sh",
         hint="kubectl supports the API server within +/-1 minor; the helm-test image's "
              "kubectl is outside that window against config.sh K8S_VERSION"),
    # "`fast` gates the four expensive jobs" — a DIFFERENT number from the one below.
    dict(name="ci-gated", pattern=rf"\b{NUM} expensive jobs\b", context=r"fast|gate",
         derive=derive_ci_gated_count, unit="jobs gated on `fast` in ci.yml",
         hint="count jobs whose `needs:` includes `fast` — not the same as the total"),
    # "CI runs six jobs beyond `fast`" — a count in prose about a file in the tree.
    dict(name="ci-job-count", pattern=rf"\b{NUM} jobs beyond\b", context=r"CI|job",
         derive=derive_ci_job_count, unit="jobs in ci.yml beyond `fast`",
         hint="count the job keys in .github/workflows/ci.yml, excluding `fast`"),
    dict(name="datasource", pattern=rf"\b{NUM} of them\b", context=r"datasource",
         derive=derive_datasource_refs, unit="${datasource} refs on the LLM board",
         hint="count them in manifests/dashboards/llm-sim-overview.json"),
]

URL_ID = re.compile(r"grafana\.com/grafana/dashboards/(\d+)")
# Prose blessings in the dashboards README: "grafana.com id 12239", "board 12239".
# 4+ digits so a bare year ("2026") can never bless an id.
PROSE_ID = re.compile(r"\b(?:board|id)\s+(\d{4,})\b", re.IGNORECASE)


def claims(pattern: str, context: str, path: str, text: str, cast=to_int) -> list[tuple]:
    ctx, pat = re.compile(context, re.I), re.compile(pattern, re.I)
    return [(path, n, cast(m.group(1)))
            for n, line in enumerate(text.splitlines(), 1) if ctx.search(line)
            for m in pat.finditer(line)]


def blessed_ids(readme_text: str) -> set[str]:
    return set(URL_ID.findall(readme_text)) | set(PROSE_ID.findall(readme_text))


def referenced_ids(path: str, text: str) -> list[tuple[str, int, str]]:
    return [(path, n, m) for n, line in enumerate(text.splitlines(), 1)
            for m in URL_ID.findall(line)]


def tracked(pattern: str) -> list[str]:
    # No check=True: a CalledProcessError here would surface as a traceback, which is
    # the one failure shape this script otherwise never produces. Everything else
    # reports through die() with the check named, and a missing git is exactly the
    # case where a clear message matters most.
    out = subprocess.run(["git", "ls-files", pattern],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        die("setup", f"git ls-files exited {out.returncode} — the scan cannot enumerate "
                     f"tracked files\n{out.stderr.strip()}")
    return [p for p in out.stdout.splitlines() if p and p not in EXCLUDED]


def main() -> None:
    md = tracked("*.md")
    if not md:
        die("setup", "git ls-files returned no markdown — every scan is dead")
    texts = {p: (ROOT / p).read_text() for p in md}

    # ---- dashboard ids: a set comparison, not a count
    blessed = blessed_ids(read(DASHBOARDS_README))
    if not blessed:
        die("ids", f"no dashboard ids parsed from {DASHBOARDS_README} — the blessing is dead")
    refs = [r for p, t in texts.items() for r in referenced_ids(p, t)]
    if not refs:
        die("ids", "no grafana.com dashboard references in any tracked markdown — dead")
    strays = [(p, n, i) for p, n, i in refs if i not in blessed]
    if strays:
        for p, n, i in strays:
            print(f"  {p}:{n}: dashboard id {i} is not known to {DASHBOARDS_README}",
                  file=sys.stderr)
        die("ids", "stale or undocumented dashboard id(s). If the reference is deliberate "
                   f"(someone else's board), give it a home in {DASHBOARDS_README} first.")
    print(f"  ok  ids        {len(refs):>3} references, all known to the catalog "
          f"({', '.join(sorted(blessed))})")

    # ---- CI check names: two set comparisons, not counts
    #
    # ⚠️ The ruleset on `main` requires these strings and lives in GitHub SETTINGS,
    # outside this repository. Rename a job and the required check is never reported;
    # GitHub waits for it forever and every pull request becomes unmergeable, with
    # nothing here saying why. This is the offline half of catching that — the
    # `settings-drift` job checks the live ruleset, which needs the network.
    ci_names = ci_check_names()

    required = [ln.strip() for ln in read(REQUIRED_CHECKS).splitlines()]
    required = [ln for ln in required if ln and not ln.startswith("#")]
    if not required:
        die("ci-required", f"{REQUIRED_CHECKS} lists no checks — the comparison is dead")
    unknown = [r for r in required if r not in ci_names]
    if unknown:
        for r in unknown:
            print(f"  {REQUIRED_CHECKS}: '{r}' is required but {CI_WORKFLOW} never "
                  f"produces it", file=sys.stderr)
        print(f"  {CI_WORKFLOW} produces: " + ", ".join(f"'{n}'" for n in sorted(ci_names)),
              file=sys.stderr)
        die("ci-required", "a required status check cannot be reported by any job. Every "
                           "pull request would wait on it forever — rename the job back, "
                           "or update BOTH the ruleset and this file.")
    print(f"  ok  ci-required {len(required)} required check(s) are names {CI_WORKFLOW} "
          f"produces")

    # Completeness the other way: a job nobody documented is a job nobody can
    # reason about, and a RENAMED job stops matching the prose that describes it.
    ci_doc = read(CI_DOC)
    undocumented = sorted(n for n in ci_names if n not in ci_doc)
    if undocumented:
        for n in undocumented:
            print(f"  {CI_DOC}: no mention of the check name '{n}'", file=sys.stderr)
        die("ci-jobs", f"every name {CI_WORKFLOW} produces must appear in {CI_DOC} — a "
                       f"renamed or added job leaves the page describing something that "
                       f"no longer exists.")
    print(f"  ok  ci-jobs     all {len(ci_names)} check name(s) appear in {CI_DOC}")

    # ---- the numeric claims
    for c in CLAIM_CHECKS:
        scan = dict(texts)
        for rel in c.get("extra", []):
            scan[rel] = read(rel)
        cast = c.get("cast", to_int)
        found = [x for p, t in scan.items()
                 for x in claims(c["pattern"], c["context"], p, t, cast)]
        if not found:
            die(c["name"], f'no claim matched /{c["pattern"]}/ anywhere — the check is '
                           f"dead. If the wording changed, teach the pattern the new one.")
        actual = c["derive"]()
        # Equality unless the check says otherwise — see kubectl-skew, the one place
        # where the true invariant is a window rather than a point.
        ok = c.get("ok", lambda v, a: v == a)
        relation = c.get("relation", "match")
        wrong = [(p, n, v) for p, n, v in found if not ok(v, actual)]
        if wrong:
            for p, n, v in wrong:
                print(f"  {p}:{n}: claims {v}; derived {actual} ({c['unit']})",
                      file=sys.stderr)
            die(c["name"], f"prose drifted from code — {c['hint']}")
        print(f"  ok  {c['name']:<10} {len(found):>3} claim(s) {relation} the derived "
              f"{actual} ({c['unit']})")


# --------------------------------------------------------------------------- selftest
# Committed fixtures, so a regex edit cannot quietly retire a matcher — the same reason
# check-vllm-buckets.py tests its matching rules against a fixture rather than live input.

BLESS_FIXTURE = """\
| GPU board | `gpu-sim-dcgm.json` | [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) |
Swapping in the fuller upstream DCGM board (grafana.com id 12239) is supported.
Published in 2026, revision 2.
"""

SCAN_FIXTURE = """\
See [25618](https://grafana.com/grafana/dashboards/25618) for the GPU board,
and [25619](https://grafana.com/grafana/dashboards/25619) for the old typo.
"""

CLAIM_FIXTURE = """\
Upstream declares around 40 `vllm:` metrics and this simulator emits 15 of them.
| `llm-simulation-alerts` | `monitoring` | Recording rules + six alerts |
The GPU rule file carries three alerts.
| the simulators serve the surface | `verify.sh` L1-L9 | `helm test` |
nine PromQL queries that also work against real hardware
repoints every `${datasource}` reference — 33 of them on the LLM board — and drops it.
CI runs eight jobs beyond `fast`: changes, compose, chart, image and the stack legs.
Docs-only changes skip the cluster, and `fast` gates the five expensive jobs.
The exporter emits 3 events per scrape, and 12 of them are dropped.\n[![Kubernetes](https://img.shields.io/badge/kubernetes-v1.36.1-326ce5.svg)](kind/gpu-sim.yaml)
Real vLLM only ever emits one surface — `both` is a rig affordance, not a fidelity claim.\n  image: alpine/k8s:1.36.2
"""


CI_FIXTURE = """\
on:
  push:
    branches: [main]

jobs:
  keyed-only:
    runs-on: ubuntu-latest
  plain:
    name: a plain job
    runs-on: ubuntu-latest
  matrixed:
    name: stack on kind (${{ matrix.profile }})
    strategy:
      matrix:
        profile: [full, lite]
"""


def selftest() -> None:
    print("check-doc-claims --selftest")

    # ⚠️ The `on:` triggers must NOT be mistaken for jobs, a job with no `name:`
    # contributes nothing (its key is the check name, which nothing requires here),
    # and the matrix value must be interpolated INTO the name rather than left as
    # the literal ${{ }} — that literal is exactly what a skipped matrix job
    # reports, and requiring it would block every pull request forever.
    ci = ci_check_names(CI_FIXTURE)
    assert ci == {"a plain job", "stack on kind (full)", "stack on kind (lite)"}, ci
    print("  ok  ci-jobs     matrix expanded, triggers ignored, unnamed job skipped")

    got = blessed_ids(BLESS_FIXTURE)
    assert got == {"25618", "12239"}, got  # 2026 must NOT be blessed by the year
    print("  ok  ids        catalog URL + prose id bless; a bare year does not")

    refs = referenced_ids("f.md", SCAN_FIXTURE)
    assert [i for _, _, i in refs] == ["25618", "25619"], refs
    assert [i for _, _, i in refs if i not in got] == ["25619"]
    print("  ok  ids        the 25619-class stray is flagged, the known id is not")

    # Every numeric matcher, against one fixture holding all five claim shapes plus
    # three decoys. The decoys are the point, and the third is a REGRESSION TEST: "3
    # events" and "12 of them" must not reach the alert or datasource comparisons, and
    # "emits one surface" must not reach the families comparison — it did, on this
    # check's first real run against the tree, because the context regex named the
    # subject (vLLM) instead of the counted noun.
    expected = {"emits": [15], "alerts": [6], "l-range": [9],
                "promql": [9], "datasource": [33], "k8s-version": ["1.36.1"],
                "kubectl-skew": ["1.36"], "ci-job-count": [8], "ci-gated": [5]}
    for c in CLAIM_CHECKS:
        vals = [v for _, _, v in claims(c["pattern"], c["context"], "f.md",
                                        CLAIM_FIXTURE, c.get("cast", to_int))]
        assert vals == expected[c["name"]], (c["name"], vals)
        print(f"  ok  {c['name']:<10} extracted {vals} from the fixture, decoys ignored")

    # ⚠️ The skew window's EDGES, which is the whole reason it is not an equality. The
    # bug it exists for (1.31 vs 1.36) must still fail, and a supported neighbour must
    # not. A malformed or major-crossing value fails closed rather than raising.
    assert all(within_one_minor(v, "1.36") for v in ("1.35", "1.36", "1.37"))
    assert not any(within_one_minor(v, "1.36")
                   for v in ("1.34", "1.38", "1.31", "2.36", "latest", "1"))
    print("  ok  skew       1.35/1.36/1.37 supported; 1.34, 1.38, 1.31 and junk rejected")

    assert to_int("six") == 6 and to_int("33") == 33
    assert claims(CLAIM_CHECKS[1]["pattern"], CLAIM_CHECKS[1]["context"],
                  "f.md", "The GPU rule file carries three alerts.") == []
    print("  ok  words      word and digit forms both parse; contextless claims ignored")

    for c in CLAIM_CHECKS:
        assert claims(c["pattern"], c["context"], "f.md", "nothing relevant",
                      c.get("cast", to_int)) == []
    assert blessed_ids("no ids at all") == set()
    print("  ok  empty      no-match is representable — main() treats it as fatal")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
