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
  * only a LITERAL multi-word assignment later used unquoted. `x=$(cmd a b)` is not a
    finding: the space is inside a substitution and the result is one token at runtime.
    That distinction is the entire difference between this file and a first draft of it
    that reported 29 findings on 12 correct scripts;
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

# `name="a b"` / `local name='a b'`, and the value captured whole.
ASSIGN = re.compile(
    r"""^\s*(?:local\s+|readonly\s+|export\s+|declare\s+)?"""
    r"""([A-Za-z_][A-Za-z0-9_]*)=(["'])(.*?)\2\s*$""")

# Anything whose value is decided at runtime. A space inside one of these says nothing
# about how many words the expansion produces, and treating it as a finding is how the
# first draft of this file reported 29 false positives on correct code.
DYNAMIC = re.compile(r"\$\(|`|\$\{")

# `$name` NOT inside double quotes and not part of a longer identifier. Deliberately
# crude: it does not parse the shell. A quoted `"$name"` is already correct, and an
# unquoted one in a context where splitting cannot happen is a false positive this
# accepts — see the docstring.
def _use(name: str) -> re.Pattern:
    return re.compile(r'(?<!["\w$])\$' + re.escape(name) + r'\b(?!["\w])')

MARKER = "word-split-ok:"


def offenders(lines: list) -> list:
    """[(line number, variable, assignment line, value)] for each reliance found."""
    literal = {}
    for i, ln in enumerate(lines, 1):
        m = ASSIGN.match(ln)
        if not m:
            continue
        value = m.group(3)
        if " " in value.strip() and not DYNAMIC.search(value):
            literal[m.group(1)] = (i, value)

    found = []
    for i, ln in enumerate(lines, 1):
        code = re.sub(r"#.*$", "", ln)
        if MARKER in ln:
            continue
        for name, (assigned_at, value) in literal.items():
            if i != assigned_at and _use(name).search(code):
                found.append((i, name, assigned_at, value))
    return sorted(found)


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

    # 9. ⚠️ PROVE IT IS NOT INERT. Every case above except 1 is a negative, and a rule
    #    that matched nothing would pass all of them.
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
