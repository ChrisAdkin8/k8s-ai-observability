#!/usr/bin/env python3
"""check-word-splitting.py — find code that RELIES on word-splitting, which zsh and
bash do differently.

    python3 scripts/check-word-splitting.py            # scan tracked *.sh
    python3 scripts/check-word-splitting.py --selftest  # unit-test the rule, no files

⚠️ THE SECOND HALF OF IRON RULE 17, WHICH HAD NO CHECK FROM 2026-08-04 TO 2026-08-07.
`check-sigpipe.py` covers the first half. This covers the other bug from that day:

    args="local --skip-monitoring"
    ./scripts/install.sh $args

bash splits that into two arguments. **zsh does not word-split unquoted parameters**, so
it passes ONE argument, `local --skip-monitoring`, and `install.sh` rejects it. A
correct script looked broken, locally, for a reason nothing in the repo could explain.
That is the whole class: the shell you test in disagrees with the shell CI runs.

⚠️ WHY shellcheck DOES NOT ALREADY CATCH IT. It does — as **SC2086**, which is
`info`-level. `task shellcheck` runs `-S warning` deliberately (`A && B || C` notes are
informational and this repo uses that idiom ~22 times), so SC2086 is suppressed along
with the rest. Lowering the severity repo-wide would report every unquoted expansion,
most of which are correct and none of which are this bug. This asks the narrower
question instead.

⚠️ WHAT IT DOES NOT COVER, stated because a check nobody understands the edges of
creates false confidence — which is worse than no check at all:

  * only tracked `*.sh`. The bash inside workflows and composite actions is not read
    here; `check-action-shell.py` owns that surface and does not ask this question;
  * only a LITERAL multi-word assignment later used unquoted. Any `$` or backtick in the
    value disqualifies it, because a space between two expansions says nothing about how
    many words result. `x="$1 --skip-monitoring"` IS this bug and is not reported —
    catching it needs to know what `$1` holds. That narrowing is the entire difference
    between this file and a first draft that reported 29 findings on 12 correct scripts;
  * only one assignment per line. `local a="x" b="y"` is skipped rather than mis-parsed,
    which is what the first draft did — inventing a value of `x" b="y` and reporting
    `drive-llm-load.sh` twice;
  * it cannot see splitting that is CORRECT and intended. There is no way to tell
    "deliberately splitting a flag list" from "accidentally splitting a path with a
    space" by reading. Both are the bug this covers, because both behave differently in
    the two shells — the repair for either is an array.

THE REPAIR IS AN ARRAY, not quoting. Quoting `"$args"` passes one argument and changes
behaviour; `args=(local --skip-monitoring)` with `"${args[@]}"` means the same thing in
both shells and cannot be split by accident.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `name="a b"` / `local name='a b'`, one assignment occupying the whole line.
#
# ⚠️ THE VALUE IS `[^"]*`, NOT `.*?`, AND THE ANCHOR IS NOT DECORATION. With a non-greedy
# `.*?` before `\s*$` this matched `local rps="$1" note="$2"` as ONE assignment whose
# value was `$1" note="$2` — a string containing a space, so a literal, so a finding.
# It reported `scripts/drive-llm-load.sh` twice on correct code. A line declaring two
# variables is now simply not matched: narrower than ideal, and the alternative is
# parsing shell.
ASSIGN = re.compile(
    r"""^\s*(?:local\s+|readonly\s+|export\s+|declare\s+)?"""
    r"""([A-Za-z_][A-Za-z0-9_]*)="(?P<d>[^"]*)"\s*$"""
    r"""|^\s*(?:local\s+|readonly\s+|export\s+|declare\s+)?"""
    r"""([A-Za-z_][A-Za-z0-9_]*)='(?P<s>[^']*)'\s*$""")

# ⚠️ ANY `$` MAKES THE VALUE RUNTIME, not just `$(` and `${`. A space between two
# expansions — `x="$1 $2"` — says nothing about how many words result, because either
# may be empty or may itself contain spaces. Treating a space as proof of a multi-word
# literal is how the first draft reported 29 findings across 12 correct scripts, every
# one of them a `x="$(cmd a b)"` yielding a single token.
#
# The cost is real and accepted: `args="$1 --skip-monitoring"` is genuinely the bug this
# file is about and is not reported. Catching it needs to know what `$1` holds, which
# reading cannot. The literal case is the one that bit, and it is the one covered.
DYNAMIC = re.compile(r"[$`]")

# Any assignment at all, in any form — `x=`, `x=$(...)`, `x=(a b)`. Used to CLEAR a
# tracked literal: once a variable has been reassigned to something else, what the
# earlier literal contained says nothing about how the next use expands.
ANY_ASSIGN = re.compile(
    r"""^\s*(?:local\s+|readonly\s+|export\s+|declare\s+)?"""
    r"""([A-Za-z_][A-Za-z0-9_]*)=""")


# `$name` and `${name}`, NOT inside double quotes and not part of a longer identifier.
# ⚠️ BOTH FORMS SPLIT IDENTICALLY, and the first version of this file matched only the
# first — so `cmd ${args}` was the bug it exists to find, going unreported.
#
# Deliberately crude: it does not parse the shell. A quoted `"$name"` is already
# unambiguous, and an unquoted one in a context where splitting cannot happen is a false
# positive this accepts — see the docstring.
def _use(name: str) -> re.Pattern:
    n = re.escape(name)
    return re.compile(r'(?<!["\w$])\$(?:\{' + n + r'\}|' + n + r'\b)(?!["\w])')

MARKER = "word-split-ok:"


def offenders(lines: list) -> list:
    """[(line number, variable, assignment line, value)] for each reliance found.

    ⚠️ ONE PASS, IN SOURCE ORDER, BECAUSE A VARIABLE IS NOT ONE VALUE. The first
    version collected every literal assignment in the file and then scanned every line
    against all of them, which is wrong in three ways a shell script hits routinely:

        cmd $args            # a use BEFORE the assignment was reported
        args="one"           # a later single-word reassignment did not clear
        args="$(cmd a b)"    # nor did a reassignment to a substitution

    Each of those reported a line that expands to one word at runtime. State is now
    tracked as the file is read: a use is judged against what the variable holds at
    that point, and any reassignment in any form drops the tracked literal.
    """
    literal, found = {}, []
    for i, ln in enumerate(lines, 1):
        code = re.sub(r"#.*$", "", ln)

        # Uses are judged BEFORE this line's own assignment is applied, so `args="a b"`
        # is not read as a use of the value it is in the middle of setting.
        if MARKER not in ln:
            for name, (assigned_at, value) in literal.items():
                if _use(name).search(code):
                    found.append((i, name, assigned_at, value))

        m = ANY_ASSIGN.match(ln)
        if not m:
            continue
        name = m.group(1)
        lit = ASSIGN.match(ln)
        val = (lit.group("d") if lit and lit.group("d") is not None
               else lit.group("s") if lit else None)
        if val is not None and " " in val.strip() and not DYNAMIC.search(val):
            literal[name] = (i, val)
        else:
            literal.pop(name, None)
    return found


def tracked() -> list:
    out = subprocess.run(["git", "ls-files", "*.sh"], capture_output=True, text=True,
                         check=True, cwd=ROOT).stdout
    return [ln for ln in out.splitlines() if ln]


def main() -> int:
    bad = []
    files = tracked()
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for line, name, assigned_at, value in offenders(text.splitlines()):
            bad.append(f"{rel}:{line}: ${name} is used unquoted, and was assigned the "
                       f"literal {value!r} at line {assigned_at}")
    if not bad:
        print(f"  ok  word-splitting  {len(files)} tracked shell file(s), no reliance "
              f"on unquoted splitting")
        return 0
    print("code that relies on word-splitting, which zsh and bash do differently:",
          file=sys.stderr)
    for b in bad:
        print(f"  {b}", file=sys.stderr)
    print("", file=sys.stderr)
    print("bash splits these into several arguments; zsh passes ONE. The repair is an "
          "array —", file=sys.stderr)
    print("  args=(local --skip-monitoring)  ...  \"${args[@]}\"", file=sys.stderr)
    print("which means the same thing in both shells. Quoting \"$var\" also silences "
          "this, but it", file=sys.stderr)
    print("CHANGES BEHAVIOUR: one argument instead of several. If the splitting is "
          "deliberate and", file=sys.stderr)
    print(f"an array will not do, mark the line `# {MARKER} <why>`.", file=sys.stderr)
    return 1


def selftest() -> int:
    print("check-word-splitting --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    def hits(src):
        return [f"{n}:{v}" for n, v, _a, _val in offenders(src.strip("\n").splitlines())]

    # 1. ⚠️ THE ACTUAL BUG, 2026-08-04. A test harness built an argument string and
    #    passed it unquoted. bash split it into two; zsh passed one, and a correct
    #    install.sh looked broken.
    check(hits('args="local --skip-monitoring"\n./scripts/install.sh $args\n') == ["2:args"],
          "the 2026-08-04 harness bug is caught")

    # 2. Quoting silences it — and that is why the message says quoting is not the fix.
    check(hits('args="local --skip-monitoring"\n./scripts/install.sh "$args"\n') == [],
          "a quoted use is not reported (it is already unambiguous)")

    # 3. The array, which is the repair the message recommends.
    check(hits('args=(local --skip-monitoring)\n./scripts/install.sh "${args[@]}"\n') == [],
          "the array form is not reported")

    # 4. ⚠️ THE FALSE POSITIVE THAT KILLED THE FIRST DRAFT. `x=$(cmd a b)` has a space
    #    in the source and produces ONE token at runtime. A version of this file without
    #    the DYNAMIC guard reported 29 of these across 12 correct scripts — a check with
    #    a 100% false-positive rate, which is worse than no check because it trains the
    #    reader to ignore it.
    check(hits('ctx="$(kubectl config current-context 2>/dev/null)"\necho $ctx\n') == [],
          "a command substitution is NOT a multi-word literal (the 29-false-positive case)")
    check(hits('n="${1:?usage: verify.sh <eks|gke|local>}"\ncase $n in a) ;; esac\n') == [],
          "...nor is a parameter expansion with a default")

    # 5. Single-word literals are the overwhelming majority and must stay silent.
    check(hits('target="local"\n./scripts/install.sh $target\n') == [],
          "a single-word literal is not reported")

    # 6. The assignment line itself is not a use of the variable.
    check(hits('args="a b"\n') == [], "the assignment alone is not a finding")

    # 7. Comments are not code.
    check(hits('args="a b"\n# run with $args here\n') == [],
          "a mention in a comment is not a use")

    # 8. The escape hatch, for splitting that is deliberate and cannot be an array.
    check(hits(f'args="a b"\ncmd $args  # {MARKER} intentional, see ...\n') == [],
          "an explicitly marked line is allowed through")

    # 9. ⚠️ THE BRACED FORM SPLITS IDENTICALLY, and the first version matched only `$a`.
    #    Expected: caught, exactly as the unbraced form is.
    check(hits('args="local --skip-monitoring"\ncmd ${args}\n') == ["2:args"],
          "the braced ${args} form is caught too")
    #    Expected: [] — quoting is quoting, braces or not.
    check(hits('args="local --skip-monitoring"\ncmd "${args}"\n') == [],
          "...and a quoted ${args} is not")

    # 10. ⚠️ A VARIABLE IS NOT ONE VALUE, and the first version treated it as one: it
    #     gathered every literal assignment in the file and then judged every line
    #     against all of them. Each case below expands to ONE word at runtime and was
    #     reported. Expected: [] for all three.
    check(hits('cmd $args\nargs="a b"\n') == [],
          "a use BEFORE the assignment is not reported")
    check(hits('args="a b"\nargs="one"\ncmd $args\n') == [],
          "a later single-word reassignment clears the tracked literal")
    check(hits('args="a b"\nargs="$(cmd x y)"\ncmd $args\n') == [],
          "...and so does a reassignment to a command substitution")
    #     Expected: caught — the literal is still what it holds at the point of use.
    check(hits('args="one"\nargs="a b"\ncmd $args\n') == ["3:args"],
          "but a reassignment TO a multi-word literal is still tracked")

    # 11. ⚠️ PROVE IT IS NOT INERT. Almost every case above is a negative, and a rule
    #     that matched nothing would pass all of them. Expected: 2 findings.
    check(len(hits('a="x y"\nrun $a\nb="p q"\nrun $b\n')) == 2,
          "two independent reliances are both found (the rule is not inert)")

    # 10. The real tree, which is what CI actually asserts.
    live = []
    for rel in tracked():
        live += offenders((ROOT / rel).read_text(encoding="utf-8",
                                                 errors="replace").splitlines())
    check(live == [], f"the tracked shell files are clean ({len(tracked())} file(s))")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-word-splitting selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
