#!/usr/bin/env python3
"""check-green-ci.py — refuse to publish from a commit CI has not passed.

    python3 scripts/check-green-ci.py --sha <commit>   # poll until every required
                                                       # check on <commit> concludes
    python3 scripts/check-green-ci.py --selftest       # unit-test the rules, no network

WHY THIS EXISTS. The ruleset on `main` gates PULL REQUESTS. It has nothing to say about
a tag, so `git tag` on any commit and `git push origin vX.Y.Z` published an image and a
chart built from whatever that commit contained. A registry version is immutable, which
is a sentence this repository has had to write about a real release, so this is the last
point at which the answer can still be no.

⚠️ WHY IT IS A SCRIPT AND NOT A HEREDOC IN A COMPOSITE ACTION, which is what it was for
about an hour. The same three reasons that moved `settings-drift` out of ci.yml:

  * inline python in a `run:` body is linted by nothing — not shellcheck, not
    actionlint, not CodeQL, which reads .py files and not YAML string scalars;
  * it cannot be unit-tested, and "verified by hand" means running a COPY of it in a
    scratch directory, which is the drifting second copy this repo refuses everywhere;
  * it also dragged in `gh`, an unpinned binary from the runner image, in the one path
    that decides whether an immutable artefact ships. stdlib urllib needs nothing.

⚠️ WHY IT POLLS. docs/releasing.md step 4 is `git push origin main && git push origin
vX.Y.Z`, so the tag lands seconds after the commit and CI cannot have finished. A single
read reliably finds nothing. Iron rule 5, in the one place where the race is guaranteed
rather than occasional.

⚠️ WHY IT PAGINATES. `/commits/{ref}/check-runs` is paginated — default 30, max 100 —
and check runs ACCUMULATE across re-runs on a commit. The first version read one page
of 100. Past that a required check falls off page one, is classified absent and
therefore pending, and the release blocks for the full deadline before failing with "CI
has not reported" — on a green commit. `total_count` is compared against what was
actually collected, so a future pagination change fails loudly instead of truncating.

AUTH. The endpoint is readable anonymously on a public repository (verified 2026-08-06,
HTTP 200), but anonymous is 60 requests/hour against a shared runner IP and this polls
roughly ninety times in a worst case. So GITHUB_TOKEN is sent when present and the
anonymous path is the fallback rather than the plan.

⚠️ A `skipped` REQUIRED CHECK COUNTS AS SATISFIED, as it does for a ruleset — a
docs-only commit genuinely cannot break the cluster. It is reported separately anyway,
because "it was skipped" and "it ran and passed" are different facts about a release.

EXIT CODES:  0 green  ·  1 not green (failed, timed out, or no run at all)  ·  2 could
not check (network, API shape)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REQUIRED_CHECKS = os.path.join(ROOT, ".github", "required-checks.txt")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "check-runs.json")

API = "https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}"

# Far above any real commit — 2000 check runs — and present only so a malformed
# `total_count` cannot spin this forever.
MAX_PAGES = 20

# A conclusion that satisfies a required check. `skipped` is here for the reason the
# module docstring gives; `neutral` is NOT, because a neutral check has deliberately
# declined to answer and treating that as a pass is how a gate becomes decorative.
PASSING = ("success", "skipped")


class Unreadable(Exception):
    """The API could not be read, or returned a shape this does not understand."""


def recorded(path: str = REQUIRED_CHECKS) -> list:
    """The checks this repository RECORDS as required, de-duplicated and sorted.

    Read rather than restated: a second list of these names would be a fourth place
    they are written down, free to disagree with the ruleset, with ci.yml, and with the
    file that records both.
    """
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh]
    got = sorted({ln for ln in lines if ln and not ln.startswith("#")})
    if not got:
        raise Unreadable(f"{path} lists no checks — this gate would pass anything, "
                         f"which is worse than not having it")
    return got


def flatten(pages: list) -> tuple:
    """(all check runs, total_count) across paginated responses.

    ⚠️ NO SILENT CAPS. If the collected list is short of what the API says exists, some
    page was missed and every verdict drawn from it is partial — which shows up as a
    required check "pending" forever rather than as an error. Refusing to guess is the
    whole point; this is the assertion the first version did not have.
    """
    runs, total = [], 0
    for page in pages:
        try:
            runs += page["check_runs"]
            total = max(total, page["total_count"])
        except (KeyError, TypeError) as exc:
            raise Unreadable(f"check-runs response has an unexpected shape: {exc!r}")
    if len(runs) < total:
        raise Unreadable(f"read {len(runs)} of {total} check runs — the pagination is "
                         f"incomplete, so a required check could be misreported as "
                         f"pending")
    return runs, total


def newest_per_name(runs: list) -> dict:
    """The most recently started check run for each name.

    A re-run does not replace the old check run, it adds one. Taking any other than the
    newest means a since-fixed failure blocks a release forever.
    """
    latest = {}
    for r in runs:
        prev = latest.get(r["name"])
        if prev is None or r["started_at"] > prev["started_at"]:
            latest[r["name"]] = r
    return latest


def classify(want: list, runs: list) -> dict:
    """Split the required checks into pending / failed / ok.

    An ABSENT check counts as pending, not failed: early in a run GitHub has not created
    it yet, and treating absent as failed would refuse every release for the first
    thirty seconds. The empty-commit case is handled by the caller on `total`, which is
    the only way to tell "not created yet" from "no run will ever exist".
    """
    latest = newest_per_name(runs)
    out = {"pending": [], "failed": [], "ok": []}
    for name in want:
        r = latest.get(name)
        if r is None or r.get("status") != "completed":
            out["pending"].append(name)
        elif r.get("conclusion") in PASSING:
            out["ok"].append(f'{name}={r["conclusion"]}')
        else:
            out["failed"].append(f'{name}={r.get("conclusion")}')
    return out


def fetch_pages(repo: str, sha: str, token: str = "") -> list:
    """Every page of check runs for `sha`. Raises Unreadable on any failure."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    pages = []
    for page in range(1, MAX_PAGES + 1):
        url = API.format(repo=repo, sha=sha, page=page)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.load(resp)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            raise Unreadable(f"could not read check runs for {sha}: {exc}")
        pages.append(body)
        got = sum(len(p.get("check_runs", [])) for p in pages)
        if got >= body.get("total_count", 0) or not body.get("check_runs"):
            break
    return pages


def poll(want, fetch, deadline_s, empty_grace_s, clock=time.monotonic,
         sleep=time.sleep, interval=20, out=print, read_retries=3) -> int:
    """Poll `fetch` until every check in `want` concludes. Returns an exit code.

    `fetch`, `clock` and `sleep` are injected so the selftest can drive the timing
    paths — the empty-commit bail-out and the deadline — without waiting on a clock.
    Those two branches decide whether a release is refused, and they are exactly the
    ones a live run would exercise least often.
    """
    start = clock()
    misses = 0
    while True:
        try:
            runs, total = flatten(fetch())
        except Unreadable as exc:
            # ⚠️ A TRANSIENT READ IS NOT A VERDICT. This loop already runs for minutes
            # against a remote API, so a single 502 or DNS blip is an ordinary event,
            # and the first version treated one as terminal. It failed CLOSED, which is
            # the right direction — nothing unsafe ships — but by then the tag is
            # already pushed (docs/releasing.md), so the cost of a blip was a re-run of
            # the release rather than a wrong publish.
            #
            # Consecutive, and reset on any good read: the signal worth stopping on is
            # "the API is down", not "three blips over half an hour".
            misses += 1
            elapsed = clock() - start
            if misses > read_retries or elapsed >= deadline_s:
                out(f"::error::{exc}")
                out(f"::error title=Could not determine whether CI passed::"
                    f"{misses} consecutive unreadable response(s). This is not a "
                    f"verdict on the commit — it is the absence of one, so the exit "
                    f"code is 2 rather than 1, and nothing is published either way.")
                return 2
            out(f"  unreadable ({misses}/{read_retries}), retrying in {interval}s: {exc}")
            sleep(interval)
            continue
        misses = 0
        v = classify(want, runs)
        elapsed = clock() - start

        if v["failed"]:
            out(f"::error title=Refusing to publish from a red commit::"
                f"{' '.join(v['failed'])} did not pass. A registry version is "
                f"immutable, so this is the last point at which the answer is still "
                f"'no'. Fix main, tag the fixed commit, and release that.")
            return 1

        if not v["pending"]:
            out(f"  ok  every required check passed: {' '.join(v['ok'])}")
            skipped = [o for o in v["ok"] if o.endswith("=skipped")]
            if skipped:
                out(f"::warning title=Some required checks were SKIPPED::"
                    f"{' '.join(skipped)}. Accepted, as a ruleset would, but this "
                    f"release was not verified by every check it names.")
            return 0

        # ⚠️ ANSWERED IN MINUTES, NOT IN HALF AN HOUR. Tagging a commit that was never
        # pushed to a branch CI watches is a real mistake with a knowable answer, and
        # polling to the full deadline wastes 30 minutes to report it. A grace period
        # rather than an instant check, because right after the tag push the run
        # genuinely does not exist yet.
        if total == 0 and elapsed >= empty_grace_s:
            out(f"::error title=No CI run exists for this commit::no check runs at all "
                f"after {empty_grace_s}s, so there is nothing to wait for. CI runs on "
                f"pushes to main and on pull requests — a commit that reached neither "
                f"was never verified. Tag a commit that is on main, or set "
                f"allow_red_ci to publish deliberately.")
            return 1

        if elapsed >= deadline_s:
            out(f"::error::still waiting on {' '.join(v['pending'])} after "
                f"{deadline_s}s. CI has not reported on this commit.")
            return 1

        out(f"  waiting on: {' '.join(v['pending'])}")
        sleep(interval)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--empty-grace", type=int, default=180)
    args = ap.parse_args(argv)

    if not args.repo or not args.sha:
        print("::error::--repo and --sha are required (GITHUB_REPOSITORY / GITHUB_SHA "
              "supply them in Actions)", file=sys.stderr)
        return 2
    try:
        want = recorded()
    except (OSError, Unreadable) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    print(f"  commit    {args.sha}")
    for name in want:
        print(f"  requiring {name}")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("  (no GITHUB_TOKEN — falling back to anonymous, which is rate limited)")
    return poll(want, lambda: fetch_pages(args.repo, args.sha, token),
                args.timeout, args.empty_grace)


# ---------------------------------------------------------------------------
# ⚠️ EVERY CASE BELOW IS A PATH A LIVE RUN EXERCISES RARELY OR NEVER. That is the
# argument for the fixtures: the happy path proves itself on every release, and the
# branches that REFUSE one — a re-run whose newest attempt failed, a truncated read, a
# commit with no run at all, a deadline — are the branches nobody watches. Iron rule 18.
# ---------------------------------------------------------------------------
def selftest() -> int:
    print("check-green-ci --selftest")
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

    # Stub names, not the repo's real required list: this tests the RULES, not whatever
    # .github/required-checks.txt happens to say today.
    want = ["alpha", "beta"]

    def run(case, deadline=600, grace=180, ticks=None):
        """Drive poll() over a fixture with an injected clock. Returns (rc, log)."""
        pages = cases[case]["pages"]
        log, t = [], {"now": 0.0}
        seq = list(ticks or [0, 1])

        def clock():
            return t["now"]

        def sleep(_):
            t["now"] += seq.pop(0) if seq else 10_000

        return poll(want, lambda: pages, deadline, grace, clock=clock, sleep=sleep,
                    out=log.append), "\n".join(log)

    # 1. The happy path, so every refusal below means something.
    rc, log = run("all-green")
    check(rc == 0, "a commit with every required check green passes")

    # 2. ⚠️ THE PAGINATION BUG. 104 check runs; the four that matter are on page two.
    #    Reading one page classified them absent -> pending -> a 30-minute wait ending
    #    in "CI has not reported", on a green commit.
    rc, log = run("two-pages")
    check(rc == 0, "required checks on page TWO are found, not reported pending")

    # 3. The same commit read with page two missing must ERROR, not quietly proceed.
    rc, log = run("truncated")
    check(rc == 2, "a short read fails loudly rather than misreporting pending")
    check("read 100 of 102" in log, "...and says how much of the set it actually saw")

    # 4. A re-run adds a check run, it does not replace one. Taking any but the newest
    #    would let a since-fixed failure block every future release from this commit.
    rc, log = run("rerun-newest-passed")
    check(rc == 0, "an old FAILED attempt is superseded by a newer green one")
    rc, log = run("rerun-newest-failed")
    check(rc == 1, "...and a newer RED attempt is not masked by an older green one")

    # 5. The commit that was never pushed to a branch CI watches.
    rc, log = run("empty", grace=0)
    check(rc == 1, "a commit with zero check runs is refused")
    check("No CI run exists" in log, "...with the diagnosis, not a generic timeout")

    # 6. ...but only after the grace period, or every release fails in its first second.
    # `waiting on:` is the per-poll line; `still waiting` is the deadline message. The
    # distinction is the assertion: it must POLL first, not bail on the first read.
    rc, log = run("empty", grace=300, deadline=600, ticks=[10])
    check("waiting on:" in log,
          "before the grace period elapses it waits rather than bailing")
    check(rc == 1 and "No CI run exists" in log,
          "...and bails once the grace period has passed")

    # 7. A required check still running holds the release, then times out.
    rc, log = run("pending", deadline=30, ticks=[60])
    check(rc == 1 and "still waiting on beta" in log,
          "a check still in progress holds, then fails on the deadline")

    # 8. `skipped` satisfies, as it does for a ruleset — and is said out loud.
    rc, log = run("skipped")
    check(rc == 1 or rc == 0, "a skipped required check does not error")
    check(rc == 0 and "SKIPPED" in log,
          "...it passes, with a warning naming which were skipped")

    # 9. A conclusion that is neither pass nor fail must not be read as a pass.
    rc, log = run("neutral")
    check(rc == 1, "a `neutral` conclusion is NOT treated as satisfied")

    def run_fetch(fetch, deadline=600, grace=180, ticks=None, read_retries=3):
        """As `run`, but over an arbitrary fetch, so a read can FAIL rather than return."""
        log, t = [], {"now": 0.0}
        seq = list(ticks or [0, 1])

        def clock():
            return t["now"]

        def sleep(_):
            t["now"] += seq.pop(0) if seq else 10_000

        return poll(want, fetch, deadline, grace, clock=clock, sleep=sleep,
                    out=log.append, read_retries=read_retries), "\n".join(log)

    # 11. ⚠️ ONE TRANSIENT READ MUST NOT DECIDE A RELEASE. This loop runs for minutes
    #     against a remote API, so a 502 is an ordinary event; the first version
    #     returned 2 on the first one and ended there. Two blips then a good read has
    #     to publish.
    blips = {"n": 0}

    def flaky():
        blips["n"] += 1
        if blips["n"] <= 2:
            raise Unreadable("simulated 502 from the check-runs API")
        return cases["all-green"]["pages"]

    rc, log = run_fetch(flaky, ticks=[0, 1, 2, 3])
    check(rc == 0, "two transient read failures, then a green read, still publishes")
    check("retrying" in log, "...and the retries are announced, not silent")

    # 12. ...but an API that is genuinely down still refuses, and with exit 2 rather
    #     than 1: the absence of a verdict is not a verdict of red.
    def dead():
        raise Unreadable("simulated outage")

    rc, log = run_fetch(dead, ticks=[0, 1, 2, 3])
    check(rc == 2, "a persistently unreadable API gives up with exit 2, not 0")

    # 13. ⚠️ THE COUNTER RESETS, and this is the half that is easy to get wrong. The
    #     signal worth stopping on is "the API is down", not "four blips over half an
    #     hour". Without the reset, a long wait on a flaky network exhausts the budget
    #     and refuses a release that was fine.
    seq = {"n": 0}

    def intermittent():
        seq["n"] += 1
        # blip, pending, blip, blip, blip, pending, green — never 4 in a row.
        script = {1: "x", 2: "pending", 3: "x", 4: "x", 5: "x", 6: "pending"}
        step = script.get(seq["n"], "all-green")
        if step == "x":
            raise Unreadable("simulated blip")
        return cases[step]["pages"]

    rc, log = run_fetch(intermittent, ticks=[0, 1, 2, 3, 4, 5, 6, 7])
    check(rc == 0, "blips spread across a long poll do not accumulate into a refusal")

    # 10. The real file is read, de-duplicated, and non-empty — so a passing selftest
    #     cannot coexist with a gate that requires nothing.
    real = recorded()
    check(real == sorted(set(real)) and len(real) > 0,
          f"the recorded required list reads and de-duplicates ({len(real)} checks)")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-green-ci selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))
