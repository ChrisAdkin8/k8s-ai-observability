#!/usr/bin/env python3
"""check-second-copy.py — refuse a second committed copy of any canonical file.

⚠️ THE RULE THE CHART'S BUILD STEP EXISTS TO KEEP. Helm's `.Files.Get` cannot read
outside the chart directory, so `charts/k8s-ai-observability/` cannot reference
`manifests/dashboards/*.json` or `manifests/alerts/*.yaml` where they live. The
tempting fix is to copy them in. `scripts/chart-build.py` assembles them into
gitignored `dist/` instead, precisely so no second copy ever exists in the tree — and
if one appears, that whole build step is doing nothing and nobody would notice.

⚠️ IT MATCHES ON FILENAME, WHICH IS NOT THE SAME AS "IS A COPY". The pattern is
repo-wide on purpose: the allowlist below names the only paths a canonical file may
live at, so a copy ANYWHERE is caught rather than only one under `charts/`. The cost
is that a file which merely ends in `-rules.yaml` trips it while being a copy of
nothing. `spike/` is excluded for exactly that reason — see ALLOWED.

⚠️ IT LIVED ONLY IN ci.yml UNTIL 2026-08-07, AND THAT IS HOW IT WENT RED UNNOTICED.
It needs no cluster, no cloud and no Docker, which is `task preflight`'s entire
stated scope, so a local run said green while `main` was red for a day. It is now one
implementation with two callers: `task second-copy` (inside preflight) and the chart
job in ci.yml. A guard that only CI can run is a guard you meet after pushing.
"""
import re
import subprocess
import sys

# The canonical files. A name matching this may exist at exactly one path, named in
# ALLOWED; anywhere else it is a copy. Kept as ONE pattern rather than a list of
# per-file rules, because a new dashboard or rule file must be covered by default.
CANONICAL = re.compile(
    r"(^|/)(llm[-_]sim\.py|.*-rules\.yaml|.*-prometheusrule\.yaml)$"
    r"|^charts/.*\.json$")

# The one path each canonical file is allowed to live at.
#
#   scripts/llm-sim.py                       the simulator itself
#   manifests/alerts/*-prometheusrule.yaml   the rules themselves
#   tests/rules/*_test.yaml                  promtool cases, not rules
#   charts/*/values.schema.json              the chart's own schema, not a dashboard
#   spike/*                                  scaffolding, never assembled, never shipped
#
# ⚠️ `spike/` IS A NAME COLLISION, NOT AN EXEMPTION. spike/stale-rules.yaml is a
# candidate detector written from scratch for prompt-fault-injection.md W3.5 —
# chart-build.py has never read it. It was committed in 0d426e5 and held the chart job
# red on `main` for a day because the job is not a required check. See spike/README.md.
ALLOWED = re.compile(
    r"scripts/llm-sim\.py"
    r"|manifests/alerts/.*-prometheusrule\.yaml"
    r"|tests/rules/.*_test\.yaml"
    r"|charts/.*/values\.schema\.json"
    r"|spike/.*")


def offenders(paths) -> list:
    """Every path whose name is canonical but whose location is not allowed."""
    return [p for p in paths
            if CANONICAL.search(p) and not ALLOWED.fullmatch(p)]


def tracked() -> list:
    """Every tracked path, from git rather than from a walk of the filesystem.

    Only committed copies matter: an uncommitted one is a work in progress, and
    gitignored build products (dist/, and the extracts spike/README.md describes) are
    exactly what this rule exists to permit.
    """
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                         check=True).stdout
    return [ln for ln in out.splitlines() if ln]


def report(bad, out=print) -> int:
    if not bad:
        out("  ok  second-copy  none — the chart is assembled, not vendored")
        return 0
    out("a canonical file name appears outside the one path that owns it:")
    for p in bad:
        out(f"  {p}")
    out("")
    out("If it IS a copy: delete it. chart-build.py assembles the chart into dist/")
    out("precisely so no second copy exists in the tree.")
    out("If it is NOT a copy and merely shares the name, it does not belong in this")
    out("check — add its path to ALLOWED in this file, and say why.")
    return 1


def selftest() -> int:
    print("check-second-copy --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    # ⚠️ FIRST, PROVE THE PATTERN IS NOT INERT. Every case below is a negative, and a
    # regex that matched nothing would pass all of them. If CANONICAL stops matching
    # the canonical files at their OWN paths, this check has quietly become a no-op —
    # which is the failure mode rule 18 exists for.
    live = ["scripts/llm-sim.py",
            "manifests/alerts/llm-prometheusrule.yaml",
            "charts/k8s-ai-observability/values.schema.json"]
    check(all(CANONICAL.search(p) for p in live),
          "the pattern matches the canonical files at their own paths (not inert)")
    check(offenders(live) == [],
          "...and the ALLOWLIST is what spares them, not a hole in the pattern")

    # ⚠️ ONE ALLOWLIST ENTRY IS INERT, AND SAYING SO IS CHEAPER THAN REDISCOVERING IT.
    # `tests/rules/*_test.yaml` never matched CANONICAL in the first place: the promtool
    # fixtures end in `_test.yaml`, not `-rules.yaml`. It came across from the inline
    # bash unexamined. Kept, because a fixture named `foo-rules.yaml` would need it and
    # the cost is a regex branch, but pinned here so the next reader knows it is a
    # belt-and-braces entry rather than load-bearing.
    check(not CANONICAL.search("tests/rules/llm-rules_test.yaml"),
          "the promtool fixtures never matched the pattern — that allowlist entry is "
          "defensive, not load-bearing")

    # The copies this exists to refuse. Each was reintroduced by hand against the
    # inline version this replaces, and each went red.
    for path, expect, why in [
            ("charts/k8s-ai-observability/llm-prometheusrule.yaml", True,
             "a rule file copied under charts/ is caught"),
            ("charts/k8s-ai-observability/llm-sim.py", True,
             "the simulator copied under charts/ is caught"),
            ("charts/k8s-ai-observability/gpu-sim-dcgm.json", True,
             "a dashboard copied under charts/ is caught"),
            # ⚠️ The `.json` clause is charts-scoped while the `.yaml` clauses are
            # repo-wide. That asymmetry is deliberate — every .json outside charts/
            # would otherwise be a hit — and it is exactly the kind of detail a
            # rewrite silently flattens.
            ("manifests/dashboards/llm-sim-overview.json", False,
             "...but the dashboards at their own path are not, since .json is "
             "charts-scoped and the .yaml clauses are not"),
    ]:
        check((offenders([path]) == [path]) == expect, why)

    # ⚠️ THE EXCLUSION MUST STAY NARROW. spike/ is spared; a `-rules.yaml` anywhere
    # else is not. Widening this to "any -rules.yaml outside manifests/" would make
    # the check pass on a real copy dropped into docs/ or terraform/.
    check(offenders(["spike/stale-rules.yaml"]) == [],
          "spike/ scaffolding is spared (the 0d426e5 regression)")
    check(offenders(["docs/my-rules.yaml"]) == ["docs/my-rules.yaml"],
          "...but a -rules.yaml OUTSIDE spike/ is still caught")
    check(offenders(["spike/nested/deep-rules.yaml"]) == [],
          "the spike exclusion reaches nested paths")

    # A hit reports rather than guesses, and says both repairs.
    log = []
    rc = report(["charts/x/llm-rules.yaml"], out=log.append)
    check(rc == 1, "a hit exits 1")
    check("delete it" in "\n".join(log) and "ALLOWED" in "\n".join(log),
          "...and names both repairs, so the reader chooses")

    # Finally the real tree, which is the assertion CI actually runs.
    check(offenders(tracked()) == [],
          f"the tracked tree is clean ({len(tracked())} paths)")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-second-copy selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(report(offenders(tracked())))
