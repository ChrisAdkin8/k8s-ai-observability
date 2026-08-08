#!/usr/bin/env python3
"""phase.py — which phase of the loop is this prompt file in?

    python3 .claude/skills/spike-loop/phase.py prompts/prompt-x.md
    python3 .claude/skills/spike-loop/phase.py --selftest

⚠️ THE ONE DECIDABLE STEP IN `/spike-loop`, SO IT IS DECIDED BY CODE. The skill's other
mechanical assertions already route to a task with a selftest behind it — citations,
spike-routing, doc-claims, preflight. Phase detection was the last thing left as prose,
which meant a model working it out by eye from `git branch` and `grep`.

That is the failure this repo spent 2026-08-07 cataloguing: four ad-hoc searches that
returned confidently while answering a different question, each exiting 0. A phase read
wrongly sends the whole loop to the wrong step, and nothing downstream would notice.

Prints one line: the phase, then why. Exit 0 always — the phase is an answer, not a
verdict, and a caller that treats "phase 1" as failure would be wrong.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

PHASES = {
    1: "review",
    2: "the experiment",
    3: "fold back",
    4: "route",
    5: "land",
}


def subject(prompt_path: str) -> str:
    """`prompts/prompt-fault-injection.md` -> `fault-injection`."""
    base = os.path.basename(prompt_path)
    base = re.sub(r"\.md$", "", base)
    return re.sub(r"^prompt-", "", base)


def decide(*, branch_exists: bool, commits: int, cites: int, routing_ok: bool) -> tuple:
    """(phase number, reason). Pure, so the selftest drives every branch."""
    if not branch_exists:
        return 1, "no spike branch yet — the review has not produced one"
    if commits == 0:
        return 2, "the spike branch exists but is empty — run the experiment"
    if cites == 0:
        return 3, (f"the branch has {commits} commit(s) and the prompt cites none of "
                   f"its artefacts — fold the findings back")
    if not routing_ok:
        return 4, ("the prompt cites the artefacts but `task spike-routing` is red — "
                   "give each one a stated heir")
    return 5, (f"the branch has {commits} commit(s), the prompt cites its artefacts "
               f"{cites} time(s), and routing is green — land it")


def _git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()


def cited_artefacts(text: str, added: set) -> set:
    """Which of `added` the prompt refers to BY PATH.

    ⚠️ A BARE MENTION IS NOT A REFERENCE, AND THIS COUNTED MENTIONS. It was
    `text.count(basename)`, so any sentence naming `worker_freeze.py` — a code block, a
    passing aside, this docstring — moved the loop past phase 3. It also could not tell
    two artefacts apart when they shared a basename.

    ⚠️ BUT REQUIRING `path:line` IS THE OPPOSITE ERROR, and it was tried first. A
    fold-back cites a whole script — `Evidence: spike/worker_freeze.py` — because the
    evidence IS the script, not one line of it. Demanding a line number reported the
    fault-injection prompt as phase 3 when its fold-back had already landed, which is
    the detector being wrong about a case that exists rather than about a hypothetical.

    So: the artefact's PATH, as the diff names it. Stronger than a basename appearing
    anywhere in prose, and it does not invent a line number the author had no reason to
    write. A passing mention that spells the full path still counts, and that is the
    accepted looseness — the observation is "has the fold-back happened", which no
    pattern decides perfectly.
    """
    return {a for a in added if a in text}


def observe(prompt_path: str) -> dict:
    """Read the tree. Every value here is what `decide` turns into a phase."""
    subj = subject(prompt_path)
    branch = f"spike/{subj}"
    exists = subprocess.run(["git", "rev-parse", "--verify", "-q", branch],
                            capture_output=True, cwd=ROOT).returncode == 0
    commits = 0
    if exists:
        out = _git("rev-list", "--count", f"main..{branch}")
        commits = int(out) if out.isdigit() else 0

    # Artefacts the branch added under spike/, and how many the prompt CITES.
    cites = 0
    if exists:
        raw = subprocess.run(["git", "diff", "--name-only", "-z", f"main...{branch}",
                              "--", "spike/"], capture_output=True, text=True,
                             cwd=ROOT).stdout
        added = {p for p in raw.split("\0") if p}
        if added:
            try:
                with open(os.path.join(ROOT, prompt_path), encoding="utf-8") as fh:
                    cites = len(cited_artefacts(fh.read(), added))
            except OSError:
                cites = 0

    routing = subprocess.run([sys.executable, "scripts/check-spike-routing.py"],
                             capture_output=True, cwd=ROOT).returncode == 0
    return {"branch_exists": exists, "commits": commits, "cites": cites,
            "routing_ok": routing, "branch": branch}


def selftest() -> int:
    print("spike-loop phase --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    # Expected phase for each state, written before running (rule 6).
    cases = [
        (dict(branch_exists=False, commits=0, cites=0, routing_ok=True), 1),
        (dict(branch_exists=True, commits=0, cites=0, routing_ok=True), 2),
        (dict(branch_exists=True, commits=3, cites=0, routing_ok=True), 3),
        (dict(branch_exists=True, commits=3, cites=7, routing_ok=False), 4),
        (dict(branch_exists=True, commits=3, cites=7, routing_ok=True), 5),
    ]
    for state, want in cases:
        got, _why = decide(**state)
        check(got == want, f"{state} -> phase {want} ({PHASES[want]})")

    # ⚠️ ORDER MATTERS, AND THIS IS THE CASE THAT PINS IT. An empty branch must read as
    # phase 2 even though the prompt cites nothing and routing could be anything —
    # otherwise a freshly-opened spike is sent to "fold back" with no diff to read.
    got, _ = decide(branch_exists=True, commits=0, cites=0, routing_ok=False)
    check(got == 2, "an empty branch is phase 2 regardless of routing or citations")

    # ⚠️ NOT INERT. Every case above asserts a different number, so a function returning
    # a constant fails at least four of them — but assert it directly too.
    seen = {decide(**s)[0] for s, _ in cases}
    check(len(seen) == 5, "the five states produce five distinct phases")

    check(subject("prompts/prompt-fault-injection.md") == "fault-injection",
          "the subject is the filename without `prompt-` or `.md`")

    # ⚠️ `cited_artefacts` WAS THE UNTESTED HALF, and it carried the bug. `decide()` is
    # pure and was covered from the start; the observation feeding it was not, so
    # counting prose mentions as citations passed every case above.
    added = {"spike/worker_freeze.py", "spike/stale_e2e.sh"}
    #    Expected: 0 — a bare basename in prose is not a reference.
    check(cited_artefacts("we ran worker_freeze.py and it was fine", added) == set(),
          "a bare basename in prose is NOT counted as citing the artefact")
    #    Expected: 1 — the path, as the diff names it.
    check(cited_artefacts("Evidence: `spike/worker_freeze.py`.", added)
          == {"spike/worker_freeze.py"},
          "the artefact path IS counted")
    #    Expected: 2 — both, once each, however many times they appear.
    check(len(cited_artefacts("`spike/worker_freeze.py` twice: `spike/worker_freeze.py` "
                              "and `spike/stale_e2e.sh`", added)) == 2,
          "each artefact counts once, not once per mention")
    #    Expected: 0 — nothing added means nothing to cite.
    check(cited_artefacts("`spike/worker_freeze.py`", set()) == set(),
          "an empty artefact set cites nothing (the rule is not inert in reverse)")

    # observe() against the real tree: it must agree with what git says, or the phase is
    # decided from a fiction. This asserts the wiring, not a particular phase.
    live = observe("prompts/prompt-fault-injection.md")
    real_branch = subprocess.run(["git", "rev-parse", "--verify", "-q",
                                  "spike/fault-injection"],
                                 capture_output=True, cwd=ROOT).returncode == 0
    check(live["branch_exists"] == real_branch,
          f"observe() agrees with git on whether spike/fault-injection exists "
          f"({real_branch})")
    check(isinstance(live["commits"], int) and live["commits"] >= 0,
          "observe() returns a non-negative commit count")
    check(live["routing_ok"] == (subprocess.run(
              [sys.executable, "scripts/check-spike-routing.py"],
              capture_output=True, cwd=ROOT).returncode == 0),
          "observe() agrees with check-spike-routing.py's own exit code")

    print(f"\n{'FAIL' if failures else 'PASS'}  phase selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


def main(argv) -> int:
    if not argv:
        print("usage: phase.py <prompt-file>|--selftest", file=sys.stderr)
        return 2
    prompt = argv[0]
    if not os.path.exists(os.path.join(ROOT, prompt)):
        print(f"::error::no such prompt file: {prompt}", file=sys.stderr)
        return 2
    st = observe(prompt)
    n, why = decide(branch_exists=st["branch_exists"], commits=st["commits"],
                    cites=st["cites"], routing_ok=st["routing_ok"])
    print(f"phase {n} — {PHASES[n]}")
    print(f"  because: {why}")
    print(f"  branch {st['branch']}: "
          f"{'exists' if st['branch_exists'] else 'absent'}, "
          f"{st['commits']} commit(s), prompt cites its artefacts {st['cites']} time(s), "
          f"spike-routing {'green' if st['routing_ok'] else 'RED'}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))
