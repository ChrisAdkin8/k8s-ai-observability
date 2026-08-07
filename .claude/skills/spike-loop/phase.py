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

    # Artefacts the branch added under spike/, and whether the prompt names any of them.
    cites = 0
    if exists:
        added = [os.path.basename(p) for p in
                 _git("diff", "--name-only", f"main...{branch}", "--", "spike/").split()]
        if added:
            try:
                with open(os.path.join(ROOT, prompt_path), encoding="utf-8") as fh:
                    text = fh.read()
                cites = sum(text.count(a) for a in set(added))
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
