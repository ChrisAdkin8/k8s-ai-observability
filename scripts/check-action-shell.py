#!/usr/bin/env python3
"""check-action-shell.py — shellcheck the bash inside composite actions.

    python3 scripts/check-action-shell.py            # lint .github/actions/*/action.yml
    python3 scripts/check-action-shell.py --selftest  # prove it fails on bad shell

⚠️ THIS EXISTS BECAUSE actionlint REFUSES TO READ A COMPOSITE ACTION. The `fast` job
runs actionlint over .github/workflows/, which hands every `run:` body to shellcheck
and covers the ~690 lines of bash living in the three workflow files. Pointed at
.github/actions/verify-chart/action.yml it reports, verbatim (v1.7.7, 2026-08-06):

    "on" section is missing in workflow [syntax-check]
    "jobs" section is missing in workflow [syntax-check]
    unexpected key "runs" for "workflow" section [syntax-check]

— it only models workflow files, and the action is not one. That left the composite
action as the single unlinted shell file in the tree, which is the exact opposite of
what "one implementation, two subjects" is meant to buy: the file shared by the
`chart-cluster` job and the release path had the least coverage of any shell here.

Three layers, and this is the third:

    scripts/*.sh                 shellcheck -S warning          (`fast`)
    .github/workflows/*.yml      actionlint -> shellcheck       (`fast`)
    .github/actions/*/action.yml THIS                           (`fast`)

⚠️ WHY THE `${{ }}` SUBSTITUTION IS NOT A SHORTCUT. A GitHub expression is replaced
as TEXT before bash ever sees the script, so it is not shell syntax and shellcheck
cannot parse it — `${{ inputs.x }}` reads as an unterminated `${` to a shell. Every
expression is replaced with a single opaque identifier so the SURROUNDING shell still
parses. That also means this cannot detect quoting bugs INSIDE an expression, which
is fine: interpolating one into a `run:` body is a pattern the action's own header
refuses outright, and CodeQL's `actions` queries are what watch for it.

⚠️ SEVERITY MATCHES scripts/*.sh DELIBERATELY. `-S warning` rather than the default,
so this repo has one shell standard rather than three, and SC2154 is excluded because
a `run:` body legitimately reads variables the step's `env:` block supplies and
shellcheck has no way to see them.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SHELLCHECK = ["shellcheck", "-S", "warning", "-e", "SC2154", "--shell=bash", "-"]

# A `run: |` (or `run: >`) block scalar and everything indented under it.
RUN_START = re.compile(r"^(\s*)run:\s*[|>]-?\s*$")
# ⚠️ AND THE SINGLE-LINE FORM, which the first version of this file did not match at
# all. `run: cd /tmp && rm -rf $HOME/x` produced ZERO blocks and was therefore never
# linted — a step that looks scanned and is not. That is the vacuous-pass failure this
# script's own docstring warns about, committed inside the script that warns about it.
RUN_INLINE = re.compile(r"^(\s*)run:[ \t]+(?![|>])(.+?)\s*$")
# Every GitHub expression, replaced with one identifier so the rest still parses.
GH_EXPR = re.compile(r"\$\{\{.*?\}\}", re.S)
EXPR_STUB = "GH_EXPRESSION"

# ⚠️ WHICH INTERPRETER THE BODY IS FOR. `shell:` is required on every composite step,
# and shellchecking a `shell: python` body reports SC1073 on perfectly good Python —
# a check that fails on correct input, which is worse than one that misses.
SHELL_KEY = re.compile(r"^\s*shell:\s*(\S+)\s*$")
SHELLS = {"bash", "sh"}

# ⚠️ CONTEXTS THAT ARE INERT INSIDE A COMPOSITE ACTION. `job.status` is NOT updated
# here: it reads `success` even after a step in this action has exited 1
# (actions/runner#1682). A guard written on it is constant-false, which is invisible —
# it looks exactly like a guard whose condition has not been met.
#
# This exists because that is not hypothetical. The diagnostics step in
# verify-chart/action.yml was added with `if: always() && job.status != 'success'` and
# collected nothing, in the same change that fixed the identical bug in the `compose`
# job. actionlint cannot parse a composite action, so nothing was watching.
# `steps.<id>.outcome` is the construct that works.
BAD_CONTEXT = re.compile(r"\$\{\{[^}]*\bjob\s*\.|(?<![\w.])job\s*\.\s*(status|container|services)\b")
IF_KEY = re.compile(r"^\s*if:\s*(.*)$")
BLOCK_SCALAR = (">", ">-", ">+", "|", "|-", "|+", "")

# ⚠️ THE GENERAL FORM OF THE `job.status` BUG. That one was a context that does nothing
# here; this is a step id that does not exist. `steps.clustr.outcome` — one letter out —
# evaluates to the empty string, compares unequal to everything, and the guard is
# constant-false again. Identical symptom, identical silence, and BAD_CONTEXT above
# catches only the specific instance that has already bitten.
#
# There is no runtime error for either: GitHub resolves an unknown context member to
# empty rather than failing the run, which is precisely why both are invisible.
STEP_REF = re.compile(r"\bsteps\.([A-Za-z_][\w-]*)")
ID_DECL = re.compile(r"^\s*id:\s*([A-Za-z_][\w-]*)\s*$")
GH_EXPR_SPAN = re.compile(r"\$\{\{.*?\}\}", re.S)


def actions() -> list:
    """Tracked composite-action definitions, via git so untracked scratch is ignored."""
    out = subprocess.run(["git", "ls-files", ".github/actions/*/action.yml"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"check-action-shell: git ls-files exited {out.returncode} — the scan "
                 f"cannot enumerate tracked files")
    return [p for p in out.stdout.splitlines() if p]


def step_shell(lines: list, run_line: int) -> str:
    """The `shell:` of the step owning the `run:` at `run_line` (0-based).

    ⚠️ BOTH DIRECTIONS, BECAUSE YAML MAPPING KEYS HAVE NO ORDER. A first cut scanned
    only backwards, on the assumption that `shell:` precedes `run:` — it does in every
    step in this repo, and it is in no way required to. A step written `run:` first
    would have yielded '' and been skipped as "not a shell step": silently unlinted,
    which is the same vacuous pass this file has now produced twice. The step's extent
    is bounded by the `- ` list items either side of it.
    """
    indent = len(lines[run_line]) - len(lines[run_line].lstrip())

    def bound(rng):
        for k in rng:
            line = lines[k]
            if not line.strip():
                continue
            cur = len(line) - len(line.lstrip())
            if line.lstrip().startswith("- ") and cur <= indent:
                return k
            if cur < indent:      # left the step's mapping entirely
                return k
        return None

    start = bound(range(run_line, -1, -1))
    end = bound(range(run_line + 1, len(lines)))
    lo = 0 if start is None else start
    hi = len(lines) if end is None else end
    for line in lines[lo:hi]:
        m = SHELL_KEY.match(line)
        if m:
            return m.group(1)
    return ""


def dedent(body: list) -> str:
    """Strip the body's OWN minimum indent, not an assumed two spaces.

    ⚠️ A YAML BLOCK SCALAR MAY BE INDENTED ANY AMOUNT past its key, and the first
    version of this assumed exactly `indent + 2`. A body indented further kept residual
    leading whitespace — harmless for most lines, fatal for a HEREDOC, whose terminator
    must sit at column 0. It turned a correct composite action into SC1073/SC1039 parse
    errors: a check that reddens on valid input, which invites someone to "fix" the YAML
    to satisfy it.
    """
    widths = [len(ln) - len(ln.lstrip()) for ln in body if ln.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(ln[cut:] if ln.strip() else "" for ln in body)


def blocks(text: str) -> list:
    """Every shell `run:` body in `text`, as (first_source_line, script).

    Line numbers are 1-based and point at the line the body starts on, so a shellcheck
    finding can be reported against the action.yml a reader is actually editing. Bodies
    belonging to a non-shell `shell:` are skipped rather than linted as bash.
    """
    lines = text.split("\n")
    found, i = [], 0
    while i < len(lines):
        inline = RUN_INLINE.match(lines[i])
        if inline:
            if step_shell(lines, i) in SHELLS:
                found.append((i + 1, inline.group(2)))
            i += 1
            continue
        m = RUN_START.match(lines[i])
        if not m:
            i += 1
            continue
        indent, body, j = len(m.group(1)), [], i + 1
        while j < len(lines):
            line = lines[j]
            if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                break
            body.append(line)
            j += 1
        # Trailing blank lines belong to the YAML, not the script.
        while body and not body[-1].strip():
            body.pop()
        if body and step_shell(lines, i) in SHELLS:
            found.append((i + 2, dedent(body)))
        i = j
    return found


def if_expressions(text: str) -> list:
    """Every `if:` value in `text`, as (line, whole expression).

    ⚠️ FOLDED EXPRESSIONS ARE JOINED, AND THE FIRST VERSION OF THIS DID NOT DO THAT.
    `bad_contexts` matched `^\\s*if:` per line, so a multi-line `if: >-` block hid
    everything on its continuation lines — including, in this very repository, the six
    `steps.<id>.outcome` terms of the guard these checks exist to protect. A checker
    blind to exactly the construct it was written for is the shape this file keeps
    producing; it gets its own function so both checks below read the same thing.
    """
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        m = IF_KEY.match(lines[i])
        if not m or lines[i].lstrip().startswith("#"):
            i += 1
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        val, j = m.group(1).strip(), i + 1
        if val in BLOCK_SCALAR:
            body = []
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                if nxt.strip() and not nxt.lstrip().startswith("#"):
                    body.append(nxt.strip())
                j += 1
            val = " ".join(body)
        out.append((i + 1, val))
        i = j
    return out


def bad_contexts(text: str) -> list:
    """Every `if:` in `text` that leans on a context inert in a composite action."""
    return [(n, expr) for n, expr in if_expressions(text) if BAD_CONTEXT.search(expr)]


def declared_ids(text: str) -> set:
    """Every `id:` declared in `text`, ignoring commented-out ones."""
    return {m.group(1) for line in text.split("\n")
            if not line.lstrip().startswith("#")
            for m in [ID_DECL.match(line)] if m}


def unknown_step_refs(text: str) -> list:
    """Every `steps.<id>` in `text` naming an id the file never declares.

    Scanned in the two places a context is actually evaluated: an `if:` value, and
    anything inside `${{ }}`. Bare text elsewhere — a shell body echoing the word
    "steps.foo", a prose comment — is not an expression and is deliberately not read,
    because a checker that reddens on documentation is a checker people delete.
    """
    known, hits, seen = declared_ids(text), [], set()
    for n, expr in if_expressions(text):
        for ref in STEP_REF.findall(expr):
            if ref not in known and (n, ref) not in seen:
                seen.add((n, ref))
                hits.append((n, ref, expr.strip()[:70]))
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("#"):
            continue
        for span in GH_EXPR_SPAN.findall(line):
            for ref in STEP_REF.findall(span):
                if ref not in known and (n, ref) not in seen:
                    seen.add((n, ref))
                    hits.append((n, ref, span.strip()[:70]))
    return hits


def lint(script: str) -> tuple:
    """(ok, shellcheck output) for one extracted body."""
    stub = GH_EXPR.sub(EXPR_STUB, script)
    proc = subprocess.run(SHELLCHECK, input="#!/usr/bin/env bash\n" + stub + "\n",
                          capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def main() -> int:
    if not subprocess.run(["which", "shellcheck"], capture_output=True).returncode == 0:
        print("::error::shellcheck is not installed, so this check did nothing. "
              "'we did not look' and 'we looked and it was fine' are not the same "
              "result.", file=sys.stderr)
        return 2

    files = actions()
    if not files:
        # ⚠️ A vacuous pass is the failure mode this whole repo is written against:
        # move or rename .github/actions/ and an empty scan reports "all clean".
        print("::error::no tracked .github/actions/*/action.yml — the scan is dead",
              file=sys.stderr)
        return 2

    total, bad, inert = 0, 0, 0
    for rel in files:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        found = blocks(text)
        if not found:
            print(f"::error::{rel} has no shell `run:` blocks — either it stopped being "
                  f"a composite action or the extractor is broken.", file=sys.stderr)
            return 2
        for line, hit in bad_contexts(text):
            inert += 1
            print(f"  {rel}:{line}: {hit}", file=sys.stderr)
            print(f"    the `job` context is NOT updated inside a composite action — "
                  f"job.status reads 'success' even after a step here has failed "
                  f"(actions/runner#1682), so this condition is constant. Give the "
                  f"steps `id:`s and test `steps.<id>.outcome` instead.",
                  file=sys.stderr)
        for line, ref, expr in unknown_step_refs(text):
            inert += 1
            print(f"  {rel}:{line}: steps.{ref} — no step declares `id: {ref}`",
                  file=sys.stderr)
            print(f"    in: {expr}", file=sys.stderr)
            print(f"    GitHub resolves an unknown context member to the empty string "
                  f"rather than failing, so this comparison is constant and the guard "
                  f"it belongs to never fires. Declared ids here: "
                  f"{sorted(declared_ids(text)) or '(none)'}", file=sys.stderr)
        for line, script in found:
            total += 1
            ok, out = lint(script)
            if not ok:
                bad += 1
                print(f"  {rel}:{line}: shellcheck", file=sys.stderr)
                for ln in out.split("\n"):
                    print(f"    {ln}", file=sys.stderr)

    if bad or inert:
        print(f"\ncheck-action-shell: FAIL — {bad} of {total} `run:` block(s) have "
              f"shellcheck findings at -S warning, and {inert} `if:` expression(s) use "
              f"a context that does nothing here.", file=sys.stderr)
        return 1
    print(f"  ok  action-shell {total} shell `run:` block(s) in {len(files)} composite "
          f"action(s) are clean, and no `if:` leans on an inert context")
    return 0


# ---------------------------------------------------------------------------
# ⚠️ EVERY ASSERTION HERE DRIVES THE CHECK TO ITS FAILURE. Iron rule 18: a check
# that has never failed is a guess, and this one is easy to get subtly wrong in the
# direction that always passes — an extractor that finds nothing, or a `${{ }}`
# substitution that turns every script into something shellcheck declines to parse,
# both report "all clean" forever.
# ---------------------------------------------------------------------------
def selftest() -> int:
    print("check-action-shell --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    if subprocess.run(["which", "shellcheck"], capture_output=True).returncode != 0:
        print("  FAIL  shellcheck is not installed — the selftest cannot run")
        return 1

    sample = (
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - name: one\n"
        "      shell: bash\n"
        "      run: |\n"
        "        set -euo pipefail\n"
        "        echo \"${{ inputs.thing }}\"\n"
        "    - name: two\n"
        "      shell: bash\n"
        "      run: |\n"
        "        echo done\n"
    )
    got = blocks(sample)
    check(len(got) == 2, "extracts both `run:` blocks, not one and not zero")
    check(got[0][0] == 7, "reports the first body's line number in the action file")
    check("set -euo pipefail" in got[0][1] and "inputs.thing" in got[0][1],
          "dedents the body rather than leaving the YAML indent on it")
    check("run:" not in got[0][1], "stops at the block, not at the next key")

    # ---- THE FOUR REGRESSIONS. Every one of these shipped in the first version of
    # this file and was found by re-reviewing it, not by running it. Each is the same
    # shape: the check reported "all clean" or reddened on valid input, and either way
    # nobody would have looked twice.

    # 1. A single-line `run:` extracted ZERO blocks — silently unlinted.
    # `cd /tmp; ls`, NOT `cd /tmp && ls`: shellcheck is right not to warn on the second
    # — a failed `cd` short-circuits the `&&` — so it would have passed for the wrong
    # reason and proved nothing about whether the body was linted at all.
    inline = ("runs:\n  steps:\n    - name: one\n      shell: bash\n"
              "      run: cd /tmp; ls\n")
    got = blocks(inline)
    check(len(got) == 1, "a single-line `run:` is extracted, not skipped")
    check(got and not lint(got[0][1])[0], "...and its shell is actually linted (SC2164)")

    # 2. A body indented deeper than key+2 kept residual whitespace, which breaks a
    #    heredoc terminator and reported SC1073/SC1039 on correct shell.
    deep = ("runs:\n  steps:\n    - name: one\n      shell: bash\n      run: |\n"
            "          set -euo pipefail\n"
            "          python3 - <<'PY'\n"
            "          print(\"hi\")\n"
            "          PY\n")
    got = blocks(deep)
    check(got and not got[0][1].startswith(" "),
          "a deeper-indented body is dedented to its OWN margin")
    check(got and lint(got[0][1])[0],
          "...so a heredoc in it still parses, rather than failing valid shell")

    # 3. A `shell: python` body was linted as bash, reporting SC1073 on good Python.
    pysh = ("runs:\n  steps:\n    - name: one\n      shell: python\n      run: |\n"
            "        import os\n        print(os.getcwd())\n")
    check(blocks(pysh) == [], "a `shell: python` body is not linted as bash")
    check(step_shell(pysh.split("\n"), 4) == "python", "...because shell: is read")

    # 3b. YAML mapping keys have no order, and a backwards-only scan missed this —
    #     the body would have been skipped as "not a shell step", unlinted and silent.
    after = ("runs:\n  steps:\n    - name: one\n      run: |\n        cd /tmp\n"
             "        ls\n      shell: bash\n    - name: two\n      shell: python\n"
             "      run: |\n        import os\n")
    got = blocks(after)
    check(len(got) == 1, "`shell:` written AFTER `run:` is still found")
    check(got and not lint(got[0][1])[0], "...and that body is linted (SC2164)")

    # 4. `job.status` inside a composite action is constant, so a guard on it never
    #    fires. This is the bug that made the chart diagnostics inert.
    inert = ("runs:\n  steps:\n    - name: diag\n"
             "      if: always() && job.status != 'success'\n      shell: bash\n"
             "      run: echo hi\n")
    check(len(bad_contexts(inert)) == 1, "a `job.status` guard is reported as inert")
    good = ("runs:\n  steps:\n    - name: a\n      id: a\n      shell: bash\n"
            "      run: echo one\n    - name: diag\n"
            "      if: always() && steps.a.outcome == 'failure'\n"
            "      shell: bash\n      run: echo hi\n")
    check(bad_contexts(good) == [], "...and steps.<id>.outcome is not flagged")

    # 4b. ⚠️ A FOLDED `if:` HID ITS OWN CONTINUATION LINES. bad_contexts matched
    #     `^\s*if:` per line, so everything after the first line of an `if: >-` block
    #     was invisible — and the guard this whole check exists to protect is written
    #     exactly that way, six terms over six lines.
    folded = ("runs:\n  steps:\n    - name: diag\n      if: >-\n"
              "        always()\n        && job.status != 'success'\n"
              "      shell: bash\n      run: echo hi\n")
    check(len(bad_contexts(folded)) == 1,
          "a folded `if:` is joined, so its continuation lines are read too")

    # 5. THE GENERAL FORM: a step id that does not exist. One letter out and the
    #    comparison is constant-false, with no runtime error to notice.
    typo = ("runs:\n  steps:\n    - name: a\n      id: cluster\n      shell: bash\n"
            "      run: echo one\n    - name: diag\n"
            "      if: always() && steps.clustr.outcome == 'failure'\n"
            "      shell: bash\n      run: echo hi\n")
    hits = unknown_step_refs(typo)
    check(len(hits) == 1 and hits[0][1] == "clustr", "a typo'd step id is caught")
    check(unknown_step_refs(good) == [], "...and a declared one is not")
    check(declared_ids(typo) == {"cluster"}, "...ids are read from `id:` declarations")

    # A typo inside `${{ }}` anywhere, not only in an `if:` — an output reference is
    # just as silently empty as a guard is.
    out_ref = ("runs:\n  steps:\n    - name: a\n      id: pkg\n      shell: bash\n"
               "      run: echo one\n    - name: b\n      shell: bash\n"
               "      env:\n        T: ${{ steps.pkgg.outputs.tgz }}\n"
               "      run: echo hi\n")
    check(len(unknown_step_refs(out_ref)) == 1,
          "a typo'd id inside ${{ }} is caught, not just in an `if:`")

    # And prose must NOT trip it, or the check gets deleted the first time someone
    # documents the construct it protects — which this repository does, at length.
    prose = ("runs:\n  steps:\n    # steps.whatever.outcome is the construct to use\n"
             "    - name: a\n      id: a\n      shell: bash\n      run: echo one\n")
    check(unknown_step_refs(prose) == [], "a comment mentioning steps.x is not a hit")

    # THE SUBSTITUTION. Without it shellcheck cannot parse `${{ }}` at all and every
    # block would fail — which looks like a strict check and is a broken one.
    ok, out = lint('echo "${{ inputs.thing }}"')
    check(ok, f"a `${{{{ }}}}` expression does not itself trip shellcheck ({out[:60]})")

    # THE BUGS. Each is a real class from this repo's history.
    ok, _ = lint('tar tzf x.tgz > /tmp/c.txt\nfor f in $(ls *.json | xargs -n1 basename); do :; done')
    check(not ok, "SC2011: parsing `ls` output is caught")

    ok, _ = lint('set -euo pipefail\nfor i in $(seq 1 5); do sleep 1; done')
    check(not ok, "SC2034: an unused loop variable is caught")

    ok, _ = lint('cd /tmp\nls')
    check(not ok, "SC2164: an unchecked `cd` is caught")

    ok, _ = lint('set -euo pipefail\necho "clean"\nfor _ in $(seq 1 3); do :; done')
    check(ok, "and clean shell still passes, or the four above prove nothing")

    # ⚠️ THE LIMIT, ASSERTED RATHER THAN LEFT TO BE DISCOVERED. SC2086 (an unquoted
    # expansion) is `info` severity, so `-S warning` does NOT report it — the level is
    # matched to scripts/*.sh so this repo has one shell standard, and that choice has
    # a cost. Pinning it here means lowering the severity is a deliberate act that
    # turns this line red, rather than something nobody notices either way.
    ok, _ = lint('rm -rf $HOME/x')
    check(ok, "SC2086 is NOT reported at -S warning — it is `info`, by design")

    # The real files, so a passing selftest cannot coexist with a dead scan.
    check(len(actions()) > 0, "at least one tracked composite action exists to scan")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-action-shell selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
