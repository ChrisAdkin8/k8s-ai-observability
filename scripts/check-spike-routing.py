#!/usr/bin/env python3
"""check-spike-routing.py — every tracked file under `spike/` must have a stated heir.

    python3 scripts/check-spike-routing.py             # scan the working tree
    python3 scripts/check-spike-routing.py --ref spike/x  # scan that ref instead
    python3 scripts/check-spike-routing.py --selftest  # unit-test the rule, then the tree

⚠️ A SPIKE IS THROWAWAY, SO ITS ARTEFACTS NEED A DESTINATION OR THEY ARE NOT THROWAWAY.
`docs/development-method.md` stage 3 says the output of a spike is knowledge, and that
anything worth keeping is rewritten in its real place. `spike/README.md` carries the list
of what survives and where it goes. A file that reaches `spike/` without an entry there
has quietly become permanent — kept because deleting it feels lossy, with nobody able to
say what would replace it.

⚠️ THIS IS A REAL DEFECT, NOT A HYPOTHETICAL. On 2026-08-07 a fold-back tracked
`spike/worker_freeze.py` and `spike/stale_e2e.sh` and updated `spike/README.md` in the
same commit, but not its survives list — so two files arrived with no heir, directly
beneath a paragraph stating that the list below IS the outstanding work. It was found by
reading. The fix for it was then wrong twice, both times because the `grep` used to check
was truncated and returned exit 0 anyway. That is the argument for this being a script
with a selftest rather than a habit.

The rule is deliberately weak: it asks whether the filename is MENTIONED in the survives
list, not whether the destination is sensible. Judging a destination needs a reader. What
this catches is the case where nobody wrote one down at all, which is the one that
actually happens.

⚠️ `--ref` READS A BRANCH INSTEAD OF THE WORKING TREE, which is what `phase.py` needs.
Routing is a property of the spike branch, and observing it from whatever happens to be
checked out answers a different question: run from `main`, the check sees only main's
already-routed artefacts, exits 0, and reports the branch as routed however many
unrouted files it carries.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPIKE_DIR = "spike"
README = "spike/README.md"

# The heading that opens the list of what survives.
#
# ⚠️ THIS IS AN EXACT MATCH, and an earlier comment here claimed it was loose. Reword
# that sentence in spike/README.md and this check stops finding the list — reporting
# "no survives list" rather than the artefacts, which is at least a loud failure and not
# a silent one. If it needs to tolerate rewording, widen the pattern deliberately rather
# than describing a looseness it does not have.
SURVIVES = re.compile(r"^What survives the spike is:\s*$", re.M)

# Files under spike/ that are not artefacts and need no heir.
EXEMPT = {"spike/README.md", "spike/.gitignore"}


def survives_block(readme: str) -> str:
    """The text of the survives list, or '' if the heading is absent."""
    m = SURVIVES.search(readme)
    if not m:
        return ""
    rest = readme[m.end():]
    # The block runs to the first paragraph that is neither a list item nor an indented
    # continuation of one.
    #
    # ⚠️ THE CONTINUATION TEST READS THE RAW PARAGRAPH, NOT THE STRIPPED ONE. This was
    # `para.lstrip().startswith(("-", "*", " "))` — and `lstrip()` removes exactly the
    # leading whitespace that the `" "` member was looking for, so that member could
    # never match and every indented continuation ended the block. Selftest case 4
    # claimed to cover it and passed anyway, because the artefact it looked for appeared
    # in the entry's FIRST paragraph, before the truncation. A fixture that cannot fail
    # is the failure iron rule 18 names.
    out = []
    for para in rest.split("\n\n"):
        stripped = para.lstrip()
        is_item = stripped.startswith(("-", "*"))
        is_continuation = bool(stripped) and para[:1].isspace()
        if out and not (is_item or is_continuation):
            break
        out.append(para)
    return "\n\n".join(out)


def mentioned(block: str, name: str) -> bool:
    """Is `name` named in `block` as a whole filename, rather than inside a longer one?

    ⚠️ THIS WAS `name in block`, WHICH IS THE 2026-08-07 DEFECT THIS SCRIPT WAS WRITTEN
    FOR, COMMITTED INSIDE THE FIX FOR IT. `spike/README.md` lists `stale_e2e.sh`, so a
    new `spike/e2e.sh` passed on the substring and arrived with no heir — the exact
    silent pass the check exists to refuse. `spike/rules.yaml` against the listed
    `stale-rules.yaml` did the same. A filename character on either side means this is
    part of a longer name and not a mention of this one.
    """
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", block) is not None


def offenders(tracked: list, readme: str) -> list:
    """Tracked spike artefacts whose basename never appears in the survives list."""
    artefacts = [p for p in sorted(tracked)
                 if p.startswith(SPIKE_DIR + "/") and p not in EXEMPT]
    if not artefacts:
        return []          # nothing to route, so no list is required
    block = survives_block(readme)
    if not block:
        return [(f"{README}", "no `What survives the spike is:` list — nothing can be "
                              "routed against it")]
    bad = []
    for path in artefacts:
        if not mentioned(block, os.path.basename(path)):
            bad.append((path, "no entry in the survives list — state where it goes, or "
                              "why it stays"))
    return bad


def _tracked(ref=None):
    # ⚠️ `-z` AND NUL, NOT `.split()`. Whitespace-splitting `git ls-files` turns one path
    # containing a space into two paths that do not exist, and `spike/` is scaffolding
    # where a filename is whatever somebody typed.
    if ref:
        cmd = ["git", "ls-tree", "-r", "-z", "--name-only", ref, "--", SPIKE_DIR]
    else:
        cmd = ["git", "ls-files", "-z", SPIKE_DIR]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True,
                         cwd=ROOT).stdout
    return [p for p in out.split("\0") if p]


def _readme(ref=None):
    """The README's text, or None when it is not there to read."""
    if ref:
        p = subprocess.run(["git", "show", f"{ref}:{README}"], capture_output=True,
                           text=True, cwd=ROOT)
        return p.stdout if p.returncode == 0 else None
    try:
        with open(os.path.join(ROOT, README), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def main(ref=None) -> int:
    where = f" on {ref}" if ref else ""
    tracked = _tracked(ref)
    if not tracked:
        print(f"  ok  spike-routing  no tracked spike/ artefacts{where}")
        return 0
    readme = _readme(ref)
    if readme is None:
        print(f"::error::{README} is missing{where}, so nothing states what survives "
              f"the spike", file=sys.stderr)
        return 1
    bad = offenders(tracked, readme)
    artefacts = [p for p in tracked if p not in EXEMPT]
    if not bad:
        print(f"  ok  spike-routing  {len(artefacts)} spike artefact(s){where} all have "
              f"a stated heir in {README}")
        return 0
    print("spike artefacts with nowhere to go:", file=sys.stderr)
    for path, why in bad:
        print(f"  {path}: {why}", file=sys.stderr)
    print("", file=sys.stderr)
    print("A spike is throwaway by design (docs/development-method.md, stage 3). An",
          file=sys.stderr)
    print("artefact with no heir is being kept by accident rather than by decision —",
          file=sys.stderr)
    print(f"add it to the survives list in {README}, with where it goes or why it stays.",
          file=sys.stderr)
    return 1


def selftest() -> int:
    print("check-spike-routing --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    good = ("intro paragraph\n\n"
            "What survives the spike is:\n\n"
            "- `a.py` -> becomes a selftest (W3).\n"
            "- `b.sh` -> no obvious heir; kept as acceptance evidence.\n\n"
            "Run everything from the repo root.\n")

    # 1. Both artefacts routed. Expected: [].
    check(offenders(["spike/a.py", "spike/b.sh", "spike/README.md"], good) == [],
          "artefacts named in the survives list pass")

    # 2. ⚠️ THE 2026-08-07 DEFECT: a third file arrives, README untouched.
    #    Expected: exactly one finding, naming it.
    got = offenders(["spike/a.py", "spike/b.sh", "spike/c.sh"], good)
    check(len(got) == 1 and got[0][0] == "spike/c.sh",
          "an artefact with no entry is caught (the worker_freeze.py case)")

    # 3. README.md and .gitignore are not artefacts. Expected: [].
    check(offenders(["spike/README.md", "spike/.gitignore"], good) == [],
          "the README and .gitignore need no heir")

    # 4. ⚠️ THE ARTEFACT IS NAMED ONLY IN THE CONTINUATION PARAGRAPH, AND THAT IS THE
    #    WHOLE TEST. The previous version of this case put `b.sh` in the entry's first
    #    paragraph, so it passed whether or not the continuation was included — which it
    #    was not, because `lstrip()` made the continuation branch dead. The fixture could
    #    not fail, which is the failure iron rule 18 names. Expected: [].
    wrapped = ("What survives the spike is:\n\n"
               "- `a.py` -> becomes a selftest (W3).\n"
               "- no obvious heir for the harness. It is the only thing that answers\n\n"
               "  the question the item exists to ask, so `b.sh` is kept as evidence.\n\n"
               "Run everything from the repo root.\n")
    check(offenders(["spike/a.py", "spike/b.sh"], wrapped) == [],
          "an artefact named ONLY in an indented continuation counts as routed")
    #    ...and the block must still END somewhere. Expected: caught — `c.sh` appears
    #    after the list, in ordinary prose, which is not part of the survives list.
    trailing = wrapped.replace("Run everything from the repo root.",
                               "Run everything from the repo root, including `c.sh`.")
    got = offenders(["spike/a.py", "spike/b.sh", "spike/c.sh"], trailing)
    check(len(got) == 1 and got[0][0] == "spike/c.sh",
          "...but a name in the prose AFTER the list does not count")

    # 4b. Only exempt files tracked: there is nothing to route, so a README without a
    #     survives list is not a finding. Expected: [].
    check(offenders(["spike/README.md", "spike/.gitignore"], "no list here\n") == [],
          "a spike/ holding only exempt files needs no survives list")

    # 5. No survives list at all — the whole rule is unanchored. Expected: one finding.
    got = offenders(["spike/a.py"], "just some prose\n")
    check(len(got) == 1 and "no `What survives" in got[0][1],
          "a README with no survives list is itself the finding")

    # 6. ⚠️ PROVE IT IS NOT INERT. Expected: 2 findings.
    check(len(offenders(["spike/x.py", "spike/y.sh"], good)) == 2,
          "two unrouted artefacts are both found (the rule is not inert)")

    # 7. ⚠️ THE SUFFIX COLLISION — THIS SCRIPT'S OWN DEFECT CLASS, IN THIS SCRIPT. The
    #    survives list names `stale_e2e.sh`; a new `e2e.sh` is a substring of it and was
    #    therefore reported as routed. Every fixture above uses names that share no
    #    substring, so none of them could see it. Expected: caught.
    suffix = ("What survives the spike is:\n\n"
              "- `stale_e2e.sh` -> kept as acceptance evidence.\n"
              "- `stale-rules.yaml` -> folded into the rule tests.\n\n"
              "Run everything from the repo root.\n")
    got = offenders(["spike/stale_e2e.sh", "spike/e2e.sh"], suffix)
    check(len(got) == 1 and got[0][0] == "spike/e2e.sh",
          "an artefact whose name is a SUFFIX of a listed one is not excused")
    #    ...the hyphen boundary too, which `\w` alone does not cover.
    got = offenders(["spike/stale-rules.yaml", "spike/rules.yaml"], suffix)
    check(len(got) == 1 and got[0][0] == "spike/rules.yaml",
          "...and a hyphen is a boundary as much as an underscore is")
    #    ...while the listed names themselves still pass, so the fix is narrow.
    check(offenders(["spike/stale_e2e.sh", "spike/stale-rules.yaml"], suffix) == [],
          "...while the names actually listed still count as routed")
    #    ...and a backticked name is matched, which is how the list is written.
    check(mentioned("- `a.py` -> a selftest", "a.py") and
          not mentioned("- `stale_e2e.sh` -> evidence", "e2e.sh"),
          "`mentioned` reads a backticked whole name and rejects a suffix of one")

    # 8. `--ref` reads a git ref rather than the working tree. HEAD is always readable and
    #    must agree with the working-tree read on a clean tree, which is what CI has.
    dirty = subprocess.run(["git", "status", "--porcelain", "--", SPIKE_DIR],
                           capture_output=True, text=True, cwd=ROOT).stdout.strip()
    if not dirty:
        check(_tracked("HEAD") == _tracked(),
              "--ref HEAD lists the same spike artefacts as the working tree")
        check(_readme("HEAD") == _readme(),
              "--ref HEAD reads the same README as the working tree")
    else:
        check(True, "--ref HEAD comparison skipped — spike/ has uncommitted changes")
    #    ...and a ref that does not exist reads as a missing README rather than a crash.
    check(_readme("no/such/ref/at/all") is None,
          "--ref against a nonexistent ref is a missing README, not a traceback")

    # 9. The real tree, which is what CI asserts.
    tracked = _tracked()
    readme = _readme()
    live = offenders(tracked, readme) if readme is not None else [("<missing>", README)]
    check(live == [], f"the tracked tree is clean ({len(tracked)} file(s) under spike/)")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-spike-routing selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    # ⚠️ AN UNRECOGNISED ARGUMENT IS REJECTED, not ignored — CLAUDE.md rule 10. A typo'd
    # `--ref` that silently scanned the working tree would answer the wrong question
    # while looking like it answered the right one, which is this script's whole subject.
    argv = sys.argv[1:]
    _ref = None
    if argv:
        if argv[0] != "--ref" or len(argv) != 2:
            print("usage: check-spike-routing.py [--ref <git-ref>] | --selftest",
                  file=sys.stderr)
            sys.exit(2)
        _ref = argv[1]
    sys.exit(main(_ref))
