#!/usr/bin/env python3
"""check-required-checks.py — fail when the LIVE branch ruleset stops matching
.github/required-checks.txt.

    python3 scripts/check-required-checks.py            # check against the GitHub API
    python3 scripts/check-required-checks.py --selftest  # unit-test the rules, no network

WHY THIS EXISTS. The `main` ruleset is repository SETTINGS. It is editable in a
browser, versioned by nothing, and it is the single control that decides whether a
pull request can merge. .github/required-checks.txt is this repository's only record
of it, and a record that nobody compares to the thing it records is a comment.

The coupling is checked in two halves, because no single check can cover it:

  * check-doc-claims.py asserts every line of required-checks.txt is a name ci.yml can
    actually produce. Offline, on every run, so a job rename fails in the pull request
    that made it.
  * this, weekly, with the network. It catches the other direction — someone editing
    the ruleset in a browser — which nothing offline can see.

⚠️ WHY IT IS A SCRIPT AND NOT FORTY LINES OF INLINE PYTHON IN ci.yml, which is what it
was until 2026-08-06. Three reasons, and the third is the one that matters:

  * inline python in a `run:` body is linted by nothing — not shellcheck, not
    actionlint, not CodeQL, which reads .py files and not YAML string scalars;
  * it cannot be unit-tested, so it fell straight into iron rule 18;
  * and it was WRONG, in both directions, in ways only a unit test would surface.

WHAT WAS WRONG, because both faults are the specification for the tests below.

  1. It said, in a comment, "find the ruleset targeting `main`. Named lookup rather
     than a hardcoded id" — and then iterated EVERY ruleset with target == "branch"
     and unioned their required checks. No name filter, no `conditions.ref_name`
     filter. /repos/{owner}/{repo}/rulesets defaults to includes_parents=true, so an
     organisation-level ruleset is returned here too and would have been folded in,
     reporting "the ruleset requires checks the repo does not record" against a
     perfectly correct configuration. A false alarm on the job whose whole purpose is
     to be believed.

  2. It never read `enforcement`. A ruleset switched to `disabled`, or to `evaluate`
     (which reports without blocking), STILL lists its required_status_checks — so
     `main` could be protected by nothing at all while this printed that everything
     agreed. That is the one failure the job exists to detect, and it was invisible
     to it.

Both fail SILENTLY. The real run prints a plausible answer either way, which is
exactly the shape check-vllm-buckets.py's docstring describes: the fault is in a
relationship to something outside the suite, and the only input that would prove the
rules work is a repository setting deliberately broken in a browser. So the rules are
unit-tested against committed fixtures instead, offline, on every push.

EXIT CODES:  0 in sync  ·  1 drift found  ·  2 could not check (network, API shape)
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REQUIRED_CHECKS = os.path.join(ROOT, ".github", "required-checks.txt")
# One file, keyed by case, rather than seven files in a directory: tests/fixtures/ is
# flat here, and seven near-identical JSON blobs would bury the one line that differs
# between them, which in every case below IS the test.
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "rulesets.json")

API = "https://api.github.com/repos/{repo}/rulesets"

# The branch this repository protects. required-checks.txt names it in prose and
# ci.yml's `on.push.branches` agrees; stated once here because the comparison needs
# it as a value rather than as English.
PROTECTED_BRANCH = "main"

# GitHub's spelling for "whatever the default branch is", which a ruleset may use in
# place of the literal name. Both mean this repository's `main`.
DEFAULT_BRANCH_TOKEN = "~DEFAULT_BRANCH"

# ⚠️ `evaluate` IS NOT ACTIVE. It reports what WOULD have been blocked and blocks
# nothing, which is indistinguishable from active protection in every field except
# this one. Treating it as protection is the second bug described above.
ENFORCING = "active"


def recorded() -> list:
    """The checks this repository RECORDS as required, de-duplicated.

    ⚠️ THE de-duplication IS NOT COSMETIC. The live side was already `sorted(set(...))`
    and this side was not, so a line accidentally repeated in required-checks.txt —
    a paste, a merge — produced a length mismatch and a confusing report about lists
    that print identically. Both sides are now sets of the same shape.
    """
    with open(REQUIRED_CHECKS, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh]
    got = sorted({ln for ln in lines if ln and not ln.startswith("#")})
    if not got:
        sys.exit(f"::error::{REQUIRED_CHECKS} lists no checks — the comparison is dead")
    return got


def targets_protected_branch(ruleset: dict) -> bool:
    """Does this ruleset actually apply to `main`?

    A ruleset carries `conditions.ref_name.include` as a list of refs or of GitHub's
    `~ALL` / `~DEFAULT_BRANCH` tokens, and `exclude` alongside it. Anything that does
    not include our branch is somebody else's rule — a `release/*` policy, or an
    organisation-level ruleset arriving through includes_parents — and folding its
    required checks into ours is a false alarm, not a finding.

    ⚠️ ABSENT CONDITIONS MEAN "EVERYTHING", so an empty include list is treated as a
    match. Being wrong in that direction over-reports, which is recoverable; the other
    way silently drops the ruleset that matters.
    """
    if ruleset.get("target") != "branch":
        return False
    cond = (ruleset.get("conditions") or {}).get("ref_name") or {}
    include = cond.get("include")
    exclude = cond.get("exclude") or []
    ours = {f"refs/heads/{PROTECTED_BRANCH}", PROTECTED_BRANCH,
            DEFAULT_BRANCH_TOKEN, "~ALL"}
    if any(x in ours for x in exclude):
        return False
    if not include:
        return True
    return any(x in ours for x in include)


def live_checks(rulesets: list, fetch) -> tuple:
    """(required check names, [names of enforcing rulesets], [names of inactive ones]).

    `fetch` resolves a ruleset's own URL to its full body — the list endpoint does not
    include `rules`. Passed in so the selftest can serve fixtures without a network.
    """
    names, enforcing, inactive = set(), [], []
    for stub in rulesets:
        if not targets_protected_branch(stub):
            continue
        label = stub.get("name") or f"id {stub.get('id')}"
        if stub.get("enforcement") != ENFORCING:
            inactive.append(f"{label} (enforcement={stub.get('enforcement')!r})")
            continue
        enforcing.append(label)
        full = fetch(stub["_links"]["self"]["href"])
        for rule in full.get("rules", []):
            if rule["type"] != "required_status_checks":
                continue
            params = rule.get("parameters") or {}
            names |= {c["context"] for c in params.get("required_status_checks", [])}
    return sorted(names), enforcing, inactive


def report(want: list, live: list, enforcing: list, inactive: list) -> int:
    """Print the comparison. 0 when they agree, 1 otherwise.

    ⚠️ A NON-ENFORCING RULESET IS A WARNING, NOT A FAILURE, AND THE FIRST VERSION OF
    THIS GOT IT BACKWARDS. It returned 1 on ANY matching ruleset that was not `active`,
    which is wrong the moment more than one ruleset is in scope. `/rulesets` defaults to
    includes_parents=true, so an ORGANISATION-level ruleset in `evaluate` mode targeting
    `~ALL` — the normal state while an org rolls a policy out — made this print "main is
    unprotected" and exit 1 while `main` was, in fact, correctly protected by its own
    active ruleset.

    That is the same false-alarm class this file's docstring convicts the INLINE version
    of, reproduced by the rewrite that was meant to fix it. The question is not "is every
    ruleset enforcing" but "do the ACTIVE ones require what this repo records". A
    non-enforcing ruleset is still worth saying out loud, because one that used to
    enforce is exactly how protection disappears — but it is only a failure when it
    leaves the active set unable to answer.
    """
    for label in inactive:
        print(f"::warning title=Ruleset is not enforcing::{label} targets "
              f"{PROTECTED_BRANCH} but is not active, so nothing it lists is enforced. "
              f"That is only a fault if it was the ruleset protecting "
              f"{PROTECTED_BRANCH} — which the active set below decides.")
    if not enforcing:
        print(f"::error::no ACTIVE branch ruleset targets {PROTECTED_BRANCH}. Either it "
              f"was deleted or it no longer applies — both mean main is less protected "
              f"than this repo claims.")
        if inactive:
            print(f"::error::and the only ruleset(s) that do target it are not "
                  f"enforcing: {inactive}. If one was switched to `evaluate` or "
                  f"`disabled` in the browser, that is when main stopped being "
                  f"protected.")
        return 1
    if not live:
        print(f"::error::the ruleset(s) on {PROTECTED_BRANCH} ({', '.join(enforcing)}) "
              f"require no status checks at all.")
        return 1
    if live != want:
        print(f"  ruleset requires : {live}")
        print(f"  repo records     : {want}")
        missing = [c for c in want if c not in live]
        extra = [c for c in live if c not in want]
        if missing:
            print(f"::error::recorded as required but the ruleset does NOT require: "
                  f"{missing}. main is less protected than "
                  f".github/required-checks.txt claims.")
        if extra:
            print(f"::error::the ruleset requires checks the repo does not record: "
                  f"{extra}. If a job was renamed in settings and not here, pull "
                  f"requests may already be blocked.")
        return 1
    print(f"  ok  {', '.join(enforcing)} and .github/required-checks.txt agree on "
          f"{len(want)}: {want}")
    return 0


def get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("::error::GITHUB_REPOSITORY is unset — nothing to look up", file=sys.stderr)
        return 2
    # ⚠️ NO CREDENTIALS. The rulesets endpoint is readable anonymously for a public
    # repository (verified 2026-08-04), which also means this cannot fail for a fork
    # the way a secret-gated step would. If this repo ever goes private, this is the
    # line that needs a token and this comment is why it stopped working.
    try:
        stubs = get_json(API.format(repo=repo))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print(f"::error::could not read the rulesets for {repo}: {exc}", file=sys.stderr)
        return 2
    try:
        live, enforcing, inactive = live_checks(stubs, get_json)
    except (KeyError, TypeError) as exc:
        print(f"::error::the rulesets API returned a shape this script does not "
              f"understand ({exc!r}) — it cannot report on protection it cannot "
              f"parse.", file=sys.stderr)
        return 2
    return report(recorded(), live, enforcing, inactive)


# ---------------------------------------------------------------------------
# ⚠️ EVERY CASE BELOW IS A BUG THE INLINE VERSION HAD, REINTRODUCED ON PURPOSE.
# Iron rule 18: a check that has never failed is a guess. These fixtures are the
# only way to drive this one to failure, because the real inputs are repository
# settings that would have to be broken in a browser to reproduce.
# ---------------------------------------------------------------------------
def selftest() -> int:
    print("check-required-checks --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    try:
        with open(FIXTURES, encoding="utf-8") as fh:
            cases = json.load(fh)
    except OSError as exc:
        print(f"  FAIL  fixture unreadable: {exc}")
        return 1
    print(f"  ({len(cases)} case(s) from {os.path.relpath(FIXTURES, ROOT)})")

    def load(name):
        return cases[name]

    def fetch_from(bodies):
        return lambda url: bodies[url]

    want = ["a", "b"]

    # 1. The happy path, so every FAIL below means something.
    fx = load("active-main")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(report(want, live, enf, inact) == 0, "an active ruleset on main that agrees")

    # 2. THE BUG THAT REPORTED GREEN OVER AN UNPROTECTED BRANCH. Same required checks,
    #    enforcement flipped to `disabled`. The old code read neither field and would
    #    have printed that the two agree.
    fx = load("disabled-main")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(report(want, live, enf, inact) == 1, "a DISABLED ruleset on main fails")

    # 3. `evaluate` is the subtler half of the same bug: it reports and blocks nothing.
    fx = load("evaluate-main")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(report(want, live, enf, inact) == 1, "an `evaluate` ruleset on main fails")

    # 4. THE FALSE ALARM. A second, active ruleset on release/* requiring something
    #    else — the shape an organisation-level ruleset arrives in, since /rulesets
    #    defaults to includes_parents=true. The old code unioned it and reported drift
    #    on a correct configuration.
    fx = load("foreign-ruleset")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(live == want, "a ruleset targeting release/* is ignored, not unioned")
    check(report(want, live, enf, inact) == 0, "and the correct config still passes")

    # 4b. ⚠️ THE FALSE ALARM THE REWRITE ITSELF INTRODUCED, and the reason this case
    #     exists. `main` IS correctly protected by its own active ruleset; separately,
    #     an ORG-level ruleset in `evaluate` mode targets ~ALL, which is the ordinary
    #     state while an org rolls a policy out and arrives here because /rulesets
    #     defaults to includes_parents=true. The first version returned 1 on ANY
    #     non-active match and screamed "main is unprotected" at a correct config —
    #     the exact fault this file's docstring convicts the INLINE version of.
    #     It must PASS, and warn.
    fx = load("active-plus-evaluating-org")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(live == want, "an evaluating ORG ruleset is not unioned into the live set")
    check(len(inact) == 1, "...it is still reported, because a lapsed one is how "
                           "protection disappears")
    check(report(want, live, enf, inact) == 0,
          "...but a correctly protected main PASSES rather than false-alarming")

    # 5. Real drift must still be caught, or 2-4 could be passing by being blind.
    fx = load("drifted-main")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(report(want, live, enf, inact) == 1, "a ruleset missing a recorded check fails")

    # 6. No ruleset on main at all — the deletion case.
    live, enf, inact = live_checks([], fetch_from({}))
    check(report(want, live, enf, inact) == 1, "no ruleset on main fails")

    # 7. ~DEFAULT_BRANCH is GitHub's other spelling for the same branch, and reading it
    #    as a literal ref name would silently drop the only ruleset that matters.
    fx = load("default-branch-token")
    live, enf, inact = live_checks(fx["list"], fetch_from(fx["bodies"]))
    check(live == want, "~DEFAULT_BRANCH counts as targeting main")

    # 8. The de-duplication, on the recorded side. Both sides must be the same shape or
    #    a repeated line reports as drift between two lists that print identically.
    check(recorded() == sorted(set(recorded())), "recorded() returns a de-duplicated set")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-required-checks selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
