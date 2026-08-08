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
    0: "done",
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


def decide(*, branch_exists: bool, merged: bool, commits: int, artefacts: int,
           cites: int, routing_ok: bool) -> tuple:
    """(phase number, reason). Pure, so the selftest drives every branch."""
    # ⚠️ MERGED IS CHECKED FIRST, AND THE LOOP HAD NO TERMINAL STATE WITHOUT IT. A landed
    # spike usually leaves its branch behind; `main..branch` is then 0, which is the same
    # observation a freshly-opened branch produces, so a finished brief read as phase 2
    # and `/spike-loop` sent the author back to run the experiment again. "Empty" and
    # "already in main" are opposite facts that `commits == 0` cannot tell apart.
    if merged:
        return 0, ("the spike branch is already an ancestor of main — this brief has "
                   "been through the loop, and there is nothing to do")
    if not branch_exists:
        return 1, "no spike branch yet — the review has not produced one"
    if commits == 0:
        return 2, "the spike branch exists but is empty — run the experiment"
    # ⚠️ `artefacts and` IS LOAD-BEARING. `cites` is pinned at 0 when the branch adds no
    # file under spike/, so without this guard a spike whose output is a measurement
    # written straight into the prompt sat at phase 3 for ever and phases 4 and 5 were
    # unreachable. No artefacts means nothing to cite, not a fold-back that never
    # happened — and the phase 5 reason below says which of the two it is.
    if artefacts and cites == 0:
        return 3, (f"the branch has {commits} commit(s) and the prompt cites none of "
                   f"its {artefacts} new artefact(s) — fold the findings back")
    if not routing_ok:
        return 4, ("the prompt cites the artefacts but `task spike-routing` is red — "
                   "give each one a stated heir")
    if not artefacts:
        return 5, (f"the branch has {commits} commit(s) and adds nothing under `spike/`, "
                   f"so whether the fold-back happened CANNOT be observed mechanically — "
                   f"confirm it yourself before landing")
    return 5, (f"the branch has {commits} commit(s), the prompt newly cites {cites} of "
               f"its {artefacts} artefact(s), and routing is green — land it")


class GitError(RuntimeError):
    """git failed. Raised rather than returned, because the empty string is a lie here."""


def _git_raw(*args) -> str:
    """git's stdout, unstripped. Raises on a non-zero exit.

    ⚠️ THE EXIT STATUS WAS DISCARDED, AND THAT IS THE SILENT-WRONG-ANSWER THIS FILE'S
    OWN DOCSTRING SAYS MUST NOT HAPPEN. `rev-list --count main..<branch>` with no local
    `main` — a fresh clone whose default branch is checked out under another name, a
    worktree, a detached CI checkout — writes to stderr, exits non-zero, and leaves
    stdout empty. `"".isdigit()` is False, so `commits` became 0 and the loop reported
    "the spike branch exists but is empty — run the experiment" on a branch with twenty
    commits. An answer that cannot be distinguished from a failure is not an answer.
    """
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
    if p.returncode != 0:
        raise GitError(f"`git {' '.join(args)}` exited {p.returncode}: "
                       f"{p.stderr.strip() or '(no stderr)'}")
    return p.stdout


def _git(*args) -> str:
    return _git_raw(*args).strip()


def _at_main(path: str) -> str:
    """The file's text as `main` has it, or '' when main does not have it at all."""
    p = subprocess.run(["git", "show", f"main:{path}"], capture_output=True, text=True,
                       cwd=ROOT)
    return p.stdout if p.returncode == 0 else ""


def cited_artefacts(text: str, added: set, baseline: str = "") -> set:
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

    ⚠️ AND IT MUST BE A *NEW* REFERENCE, WHICH IS WHERE `baseline` COMES IN. Counting
    every path the prompt names counts the ones a PREVIOUS spike wrote. `prompt-fault-
    injection.md` already names `spike/worker_freeze.py` in five places, so a second
    spike branch modifying that same file — the diff lists modifications, not only
    additions — read as "already folded back" at its very first commit, and phase 3 was
    skipped entirely for the findings that mattered most. The question is not "does the
    prompt mention this file" but "did THIS branch's fold-back put it there", so the
    baseline is the prompt as `main` has it.
    """
    return {a for a in added if a in text and a not in baseline}


def observe(prompt_path: str) -> dict:
    """Read the tree. Every value here is what `decide` turns into a phase."""
    subj = subject(prompt_path)
    branch = f"spike/{subj}"
    exists = subprocess.run(["git", "rev-parse", "--verify", "-q", branch],
                            capture_output=True, cwd=ROOT).returncode == 0
    merged, commits, artefacts, cites, routing = False, 0, 0, 0, True
    if exists:
        merged = subprocess.run(["git", "merge-base", "--is-ancestor", branch, "main"],
                                capture_output=True, cwd=ROOT).returncode == 0
        commits = int(_git("rev-list", "--count", f"main..{branch}"))

        # Artefacts the branch touched under spike/, and how many the prompt cites that
        # main's copy of it does not.
        raw = _git_raw("diff", "--name-only", "-z", f"main...{branch}", "--", "spike/")
        added = {p for p in raw.split("\0") if p}
        artefacts = len(added)
        if added:
            try:
                with open(os.path.join(ROOT, prompt_path), encoding="utf-8") as fh:
                    cites = len(cited_artefacts(fh.read(), added,
                                                _at_main(prompt_path)))
            except OSError:
                cites = 0

        # ⚠️ OBSERVED FROM THE BRANCH, NOT FROM WHATEVER IS CHECKED OUT. Every other
        # value here describes the spike branch; this one described the working tree, so
        # running from `main` — which is where phase 1 happens and the only place
        # `main..branch` means anything — saw main's already-routed artefacts, exited 0,
        # and reported the branch as routed however many unrouted files it carried. That
        # made phase 4 unreachable from the one checkout the loop is normally driven from.
        routing = subprocess.run([sys.executable, "scripts/check-spike-routing.py",
                                  "--ref", branch],
                                 capture_output=True, cwd=ROOT).returncode == 0
    return {"branch_exists": exists, "merged": merged, "commits": commits,
            "artefacts": artefacts, "cites": cites, "routing_ok": routing,
            "branch": branch}


def selftest() -> int:
    print("spike-loop phase --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    def state(**kw):
        """A landable state, overridden one field at a time — so each case names the
        single observation it is about rather than restating all six."""
        base = dict(branch_exists=True, merged=False, commits=3, artefacts=2, cites=7,
                    routing_ok=True)
        base.update(kw)
        return base

    # Expected phase for each state, written before running (rule 6).
    cases = [
        (state(branch_exists=False, commits=0, artefacts=0, cites=0), 1),
        (state(commits=0, artefacts=0, cites=0), 2),
        (state(cites=0), 3),
        (state(routing_ok=False), 4),
        (state(), 5),
        (state(merged=True), 0),
    ]
    for st, want in cases:
        got, _why = decide(**st)
        check(got == want, f"{st} -> phase {want} ({PHASES[want]})")

    # ⚠️ ORDER MATTERS, AND THIS IS THE CASE THAT PINS IT. An empty branch must read as
    # phase 2 even though the prompt cites nothing and routing could be anything —
    # otherwise a freshly-opened spike is sent to "fold back" with no diff to read.
    got, _ = decide(**state(commits=0, artefacts=0, cites=0, routing_ok=False))
    check(got == 2, "an empty branch is phase 2 regardless of routing or citations")

    # ⚠️ THE TERMINAL STATE, WHICH DID NOT EXIST. A landed spike leaves its branch behind,
    # so `main..branch` is 0 — indistinguishable from a branch just opened, and the loop
    # answered "phase 2, run the experiment" on a brief it had already finished. `merged`
    # outranks every other observation, including the empty-branch case above, because a
    # merged branch is empty for exactly that reason.
    got, _ = decide(**state(merged=True, commits=0, artefacts=0, cites=0,
                            routing_ok=False))
    check(got == 0, "a merged branch is phase 0 even though it also looks empty")

    # ⚠️ NO ARTEFACTS IS NOT A MISSING FOLD-BACK. `cites` cannot exceed 0 when the branch
    # adds no file under spike/, so the phase-3 test had to be conditioned on there being
    # something to cite — otherwise phases 4 and 5 were unreachable for any spike whose
    # output was a measurement rather than a script.
    got, why = decide(**state(artefacts=0, cites=0))
    check(got == 5 and "CANNOT be observed mechanically" in why,
          "a branch with no spike/ artefacts reaches phase 5, and says why it is unsure")

    # ⚠️ NOT INERT. Every case above asserts a different number, so a function returning
    # a constant fails at least five of them — but assert it directly too.
    seen = {decide(**s)[0] for s, _ in cases}
    check(len(seen) == 6, "the six states produce six distinct phases")

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
    #    ⚠️ Expected: 0 — the path is there, but main's copy already had it, so this
    #    branch did not put it there. Counting it is how a second spike on a file a
    #    previous spike already cited skipped phase 3 at its first commit.
    check(cited_artefacts("Evidence: `spike/worker_freeze.py`.", added,
                          "old text citing `spike/worker_freeze.py` already") == set(),
          "a path main's copy of the prompt ALREADY cited is not a new citation")
    #    ...and a genuinely new one still counts against the same baseline.
    check(cited_artefacts("`spike/worker_freeze.py` and `spike/stale_e2e.sh`", added,
                          "old text citing `spike/worker_freeze.py` already")
          == {"spike/stale_e2e.sh"},
          "...while an artefact the baseline does NOT name still counts")

    # ⚠️ git FAILING MUST RAISE, NOT RETURN "". This is the assertion behind the
    # silent-zero bug: a bad ref used to leave `commits` at 0 and report phase 2.
    try:
        _git("rev-list", "--count", "main..no/such/ref/anywhere")
        check(False, "_git raises GitError on a failing git command")
    except GitError:
        check(True, "_git raises GitError on a failing git command")
    check(_at_main("no/such/file/at/all.md") == "",
          "_at_main returns '' for a path main does not have, rather than raising")

    # observe() against the real tree: it must agree with what git says, or the phase is
    # decided from a fiction. This asserts the wiring, not a particular phase.
    #
    # ⚠️ IT MUST NOT REQUIRE A PARTICULAR BRANCH TO EXIST. This asserted against
    # `spike/fault-injection` by name; that branch was deleted on 2026-08-08 and the case
    # would have started asserting a fiction. Whatever git says is the expectation.
    live = observe("prompts/prompt-fault-injection.md")
    real_branch = subprocess.run(["git", "rev-parse", "--verify", "-q",
                                  live["branch"]],
                                 capture_output=True, cwd=ROOT).returncode == 0
    check(live["branch_exists"] == real_branch,
          f"observe() agrees with git on whether {live['branch']} exists ({real_branch})")
    check(isinstance(live["commits"], int) and live["commits"] >= 0,
          "observe() returns a non-negative commit count")
    check(set(live) == {"branch_exists", "merged", "commits", "artefacts", "cites",
                        "routing_ok", "branch"},
          "observe() returns exactly the observations decide() consumes, plus the branch")
    if real_branch:
        check(live["routing_ok"] == (subprocess.run(
                  [sys.executable, "scripts/check-spike-routing.py", "--ref",
                   live["branch"]], capture_output=True, cwd=ROOT).returncode == 0),
              "observe() agrees with check-spike-routing.py --ref on that branch")
    else:
        check(live["routing_ok"] is True and live["artefacts"] == 0,
              "with no spike branch, observe() reports nothing to route and no artefacts")

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
    # ⚠️ A git failure is reported, not absorbed. Exit 2 rather than the usual 0, because
    # this is the one case where there IS no phase — and a caller that reads "phase 2"
    # off a broken checkout would run the experiment again for no reason.
    try:
        st = observe(prompt)
    except GitError as exc:
        print(f"::error::cannot read the tree, so there is no phase to report: {exc}",
              file=sys.stderr)
        return 2
    n, why = decide(branch_exists=st["branch_exists"], merged=st["merged"],
                    commits=st["commits"], artefacts=st["artefacts"],
                    cites=st["cites"], routing_ok=st["routing_ok"])
    print(f"phase {n} — {PHASES[n]}")
    print(f"  because: {why}")
    print(f"  branch {st['branch']}: "
          f"{'merged into main' if st['merged'] else 'exists' if st['branch_exists'] else 'absent'}, "
          f"{st['commits']} commit(s), {st['artefacts']} spike/ artefact(s), "
          f"{st['cites']} newly cited by the prompt, "
          f"spike-routing {'green' if st['routing_ok'] else 'RED'}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))
