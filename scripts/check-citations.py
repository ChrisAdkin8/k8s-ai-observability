#!/usr/bin/env python3
"""check-citations.py — every `path:line` in tracked markdown must resolve.

    python3 scripts/check-citations.py            # scan tracked *.md
    python3 scripts/check-citations.py --selftest  # unit-test the rules, then the tree

⚠️ THE CHEAPEST HALF OF A PROMPT REVIEW, DONE DETERMINISTICALLY. Briefs cite the tree
by `file:line` and those citations rot: a file is renamed, a spike moves, a function
grows and every line below it shifts. On 2026-08-07 a single review round of one brief
found four dead pointers, including `spike/kv_isolation.py`, which has never existed in
this repository at all. That one is decidable without reading anything, and this decides
it in milliseconds on every push.

WHAT IT CANNOT DO, and the boundary matters more than the feature: it cannot tell you
whether the cited line SAYS what the prose claims. `check-doc-claims.py:118` was cited
for a call that is forty lines further down; the path resolved and the line existed and
the claim was still wrong. That half needs a reader, and `.claude/agents/prompt-fact-checker.md`
owns it. This one exists so the reader is not spent on the half a regex can settle.

⚠️ BASENAMES RESOLVE, AND AMBIGUOUS ONES ARE A FINDING. The house style cites
`llm-sim.py:445` as often as `scripts/llm-sim.py:878`, and both are unambiguous today
because each basename is unique in the tree. Add a second `config.sh` anywhere and every
bare citation to it becomes a coin toss — reported here rather than silently picking one.
"""
import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The extensions a citation may carry. Requiring one is what keeps run ids
# (`31177224542`), clock times (`04:17`), image tags (`grafana/grafana:11.6.0`) and
# version strings out of the match entirely, rather than filtering them afterwards.
EXT = r"(?:py|sh|yaml|yml|json|tf|txt|md|tpl)"

# ⚠️ A CITATION IS REPO-RELATIVE, SO IT CANNOT BEGIN WITH `/`. That single constraint is
# what excludes URLs, and it took two wrong attempts to find because the engine retries
# at EVERY offset. Excluding `/` and `\w` before the match still let
# `https://example.com/x.py:80` through at `com/x.py` (preceded by a dot); excluding `.`
# as well still let it through at the `//` in `https://` (preceded by a colon). Both were
# reasoned about and both were wrong; printing the actual match is what settled it.
#
# The lookbehind keeps the other half — a path preceded by a word character, slash or dot
# is part of something longer and not a citation this repo writes.
#
# ⚠️ AND A HYPHEN, WHICH IS THE THIRD WRONG ATTEMPT. `\w/.` still let
# `https://github.com/vllm/blob/main/scripts/llm-sim.py:445` through, matching at
# `sim.py:445` — preceded by `-`, which none of the three excluded characters covers. The
# comment above claimed the `/` rule "is what excludes URLs" and it does not, for any
# hyphenated filename, which is most of this tree. That reported a dead citation in
# correct prose and turned `task preflight` red, so the failure was loud rather than
# silent, but it was still the check being wrong about the tree. Selftest case 8's
# fixture could not catch it: `example.com/x.py:80` has no hyphen in it.
CITE = re.compile(
    rf"(?<![\w/.-])([A-Za-z0-9_.-][A-Za-z0-9_./-]*\.{EXT}):(\d+)(?:-(\d+))?")

# ⚠️ A SHIPPED PROMPT IS A RECORD, AND ITS CITATIONS WERE TRUE WHEN IT WAS WRITTEN.
# `check-doc-claims.py` holds the same banner for the same reason and says it there;
# this is the second reader of that decision, not a second decision. Nine tracked
# prompts carry it and hold 71 citations between them, so without this carve-out any
# unrelated rename forces a choice between rewriting a record and adding an exception.
# The selftest asserts the two files still spell it identically, because a fork here is
# silent in one direction: a reworded banner puts records back into the scan.
RECORD_BANNER = "SHIPPED — this is a RECORD"

# Files cited by this repo that live in somebody else's. These are upstream vLLM source,
# read at a version pinned in docs/versions.md and deliberately not vendored — rule 4
# turns on transcribing upstream rather than copying it.
#
# ⚠️ AN ENTRY HERE IS A PROMISE THAT THE PATH IS EXTERNAL, not a way to silence a dead
# link. A repo path that stops resolving must be fixed, not listed: `spike/kv_isolation.py`
# is exactly the shape this check exists to catch, and adding it here would have hidden it.
# ⚠️ A BARE NAME MATCHES ONLY A BARE CITATION. `loggers.py:468` is upstream; but
# `scripts/loggers.py:10` is a repo-relative path that does not exist, and matching it
# by basename exempted it silently — the exact silencing this comment forbids, done by
# the code beneath the comment. Path-shaped entries match exactly; bare names only
# where the citation is also bare.
UPSTREAM_BARE = {"loggers.py"}
UPSTREAM_PATHS = {"vllm/v1/metrics/stats.py"}


def citations(text: str) -> list:
    """[(line number, cited path, first line, last line)] for one file's text."""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        for m in CITE.finditer(ln):
            lo = int(m.group(2))
            hi = int(m.group(3)) if m.group(3) else lo
            out.append((i, m.group(1), lo, hi))
    return out


def resolve(path: str, tracked: set, by_base: dict) -> tuple:
    """(resolved path or None, reason). Reason is '' when it resolved."""
    if path in UPSTREAM_PATHS or ("/" not in path and path in UPSTREAM_BARE):
        return None, ""                      # external, and declared so
    if path in tracked:
        return path, ""
    # ⚠️ THE BASENAME FALLBACK IS FOR BARE CITATIONS ONLY, and applying it to path-shaped
    # ones defeated the single thing this check exists to catch. `docs/llm-sim.py:445`
    # resolved to `scripts/llm-sim.py` and passed — a citation naming the WRONG DIRECTORY
    # is precisely "a file moved and the pointer did not", and it was answered by looking
    # up the basename and finding the file at its new home. Worse than silent: the range
    # check then validated the line number against a file the citation does not name.
    # UPSTREAM above already draws this bare-vs-path distinction, for the same reason,
    # eight lines earlier.
    if "/" in path:
        return None, "no such tracked file"
    cands = by_base.get(path, [])
    if len(cands) == 1:
        return cands[0], ""
    if not cands:
        return None, "no such tracked file"
    return None, f"ambiguous — {len(cands)} tracked files share that name: {cands}"


def offenders(files: dict, tracked: set, by_base: dict, lengths: dict) -> list:
    """[(citing file, citing line, cited path, detail)] for every citation that fails.

    `files` is already filtered to what gets scanned — `main()` drops shipped RECORDs
    before calling, so the carve-out is one decision in one place rather than a
    condition threaded through here.
    """
    bad = []
    for name, text in files.items():
        for at, path, lo, hi in citations(text):
            target, why = resolve(path, tracked, by_base)
            if why:
                bad.append((name, at, path, why))
            elif target is not None:
                # ⚠️ REVERSED FIRST, because the bound test cannot see it: `:99999-2`
                # has hi=2, which is in range for every file, so only lo was ever wrong
                # and lo is tested against 1 alone. A typo'd range was the one citation
                # shape that resolved, passed, and pointed nowhere.
                if hi < lo:
                    bad.append((name, at, path,
                                f"reversed line range {lo}-{hi}"))
                    continue
                n = lengths.get(target)
                if n is None:
                    continue      # tracked but unreadable; main() reports it separately
                if lo < 1 or hi > n:
                    bad.append((name, at, path,
                                f"cites line {hi} of a {n}-line file"))
    return bad


def _tree():
    # ⚠️ `-z` AND NUL, NOT `.split()` — a tracked path containing a space would
    # otherwise become two paths that do not exist, and every citation to it would
    # report as dead.
    raw = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True,
                         check=True, cwd=ROOT).stdout
    out = [p for p in raw.split("\0") if p]
    tracked = set(out)
    by_base = defaultdict(list)
    for p in out:
        by_base[os.path.basename(p)].append(p)
    return tracked, by_base


def _read(rel):
    """Text of a tracked file, or None when it cannot be read.

    ⚠️ TRACKED IS NOT THE SAME AS PRESENT, and an unguarded `open()` here took the whole
    gate down with a traceback rather than reporting anything. `git ls-files` lists the
    INDEX: a file deleted from the working tree without `git rm` — an in-progress rename,
    which is exactly the state that rots citations — raises FileNotFoundError, and a
    submodule gitlink raises IsADirectoryError. Because `preflight` stops at the first
    red task, that traceback also hid every check ordered after this one.
    """
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _scan(tracked) -> tuple:
    """(text by path, unreadable paths). ONE read of the tree, where there were two.

    `main()` read every tracked file for its line count and then every tracked markdown
    file again for its text, so each run walked the tree twice — and `--selftest` ran
    both passes a second time on top of that.
    """
    texts, unreadable = {}, []
    for p in sorted(tracked):
        t = _read(p)
        if t is None:
            unreadable.append(p)
        else:
            texts[p] = t
    return texts, unreadable


def scannable(texts: dict) -> dict:
    """The markdown this check scans: tracked `*.md`, minus shipped RECORDs."""
    return {p: t for p, t in texts.items()
            if p.endswith(".md") and RECORD_BANNER not in t}


def main() -> int:
    tracked, by_base = _tree()
    texts, unreadable = _scan(tracked)
    lengths = {p: len(t.splitlines()) for p, t in texts.items()}
    files = scannable(texts)
    records = sum(1 for p, t in texts.items()
                  if p.endswith(".md") and RECORD_BANNER in t)
    bad = offenders(files, tracked, by_base, lengths)
    total = sum(len(citations(t)) for t in files.values())
    note = ""
    if unreadable:
        shown = ", ".join(unreadable[:3]) + (" …" if len(unreadable) > 3 else "")
        note = (f"\n      note: {len(unreadable)} tracked file(s) are not readable in the "
                f"working tree, so no line count was checked against them: {shown}")
    if not bad:
        print(f"  ok  citations  {total} `path:line` reference(s) across {len(files)} "
              f"tracked markdown file(s) all resolve "
              f"({records} shipped record(s) held out){note}")
        return 0
    print("citations that do not resolve:", file=sys.stderr)
    for name, at, path, why in bad:
        print(f"  {name}:{at}: `{path}` — {why}", file=sys.stderr)
    print("", file=sys.stderr)
    print("A stale pointer is worse than none: it reads as evidence and sends the next",
          file=sys.stderr)
    print("reader somewhere that does not exist. Fix the path, or — only if the file is",
          file=sys.stderr)
    print("genuinely in another repository — add it to UPSTREAM_BARE or UPSTREAM_PATHS and say so.",
          file=sys.stderr)
    return 1


def selftest() -> int:
    print("check-citations --selftest")
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    tracked = {"scripts/llm-sim.py", "scripts/config.sh", "docs/ci.md",
               "spike/kv_profile.py"}
    by_base = defaultdict(list)
    for p in tracked:
        by_base[os.path.basename(p)].append(p)
    lengths = {"scripts/llm-sim.py": 900, "scripts/config.sh": 300,
               "docs/ci.md": 500, "spike/kv_profile.py": 120}

    def bad(md):
        return [(a, c, d) for _f, a, c, d in
                offenders({"t.md": md}, tracked, by_base, lengths)]

    # 1. A full path in range. Expected: [].
    check(bad("see `scripts/llm-sim.py:445` for it") == [], "a resolving full path passes")
    # 2. A bare basename, unique in the tree. Expected: [].
    check(bad("see `llm-sim.py:445`") == [], "a unique basename resolves")
    # 3. A range. Expected: [].
    check(bad("`scripts/llm-sim.py:445-460`") == [], "a line range resolves")

    # 4. ⚠️ THE 2026-08-07 DEFECT. A path that has never existed. Expected: caught.
    got = bad("evidence in `spike/kv_isolation.py:88`")
    check(len(got) == 1 and "no such tracked file" in got[0][2],
          "a path that has never existed is caught (the kv_isolation.py case)")

    # 5. Out of range — the shape a citation rots into when code moves. Expected: caught.
    got = bad("`spike/kv_profile.py:400`")
    check(len(got) == 1 and "400 of a 120-line file" in got[0][2],
          "a line past the end of the file is caught")

    # 6. ⚠️ AMBIGUITY IS A FINDING, NOT A COIN TOSS. Two tracked `config.sh` makes every
    #    bare citation undecidable. Expected: caught.
    amb = dict(by_base)
    amb["config.sh"] = ["scripts/config.sh", "other/config.sh"]
    got = [(a, c, d) for _f, a, c, d in
           offenders({"t.md": "`config.sh:12`"}, tracked, amb, lengths)]
    check(len(got) == 1 and "ambiguous" in got[0][2],
          "a basename matching two tracked files is reported, not guessed")

    # 7. Declared upstream, bare. Expected: [] — external, and the allowlist says so.
    check(bad("upstream does it at `loggers.py:468`") == [],
          "a declared bare UPSTREAM name is not a finding")
    #    ...and a path-shaped upstream entry. Expected: [].
    check(bad("`vllm/v1/metrics/stats.py:393-395`") == [],
          "a declared UPSTREAM path is not a finding")
    # 7b. ⚠️ THE SILENCING CASE THE DOCSTRING FORBIDS AND THE CODE ALLOWED. A dead
    #     REPO path whose basename collides with an upstream name was exempted by
    #     basename, so it was never reported. Expected: caught.
    got = bad("`scripts/loggers.py:10`")
    check(len(got) == 1 and "no such tracked file" in got[0][2],
          "a dead repo path is NOT excused by an upstream basename collision")

    # 8. ⚠️ THE FALSE-POSITIVE CLASSES, all of which appear in this repo's prose today.
    #    Expected: [] for every one, and none should even match the pattern.
    for text, label in [
            ("run `31177224542` produced it", "a run id"),
            ("cron fires at 04:17 UTC", "a clock time"),
            ("image: grafana/grafana:11.6.0", "an image tag"),
            ("tagged v0.10.0 on main", "a version string"),
            ("see https://example.com/x.py:80 online", "a URL with a port")]:
        check(bad(text) == [], f"{label} is not read as a citation")

    # 9. ⚠️ PROVE IT IS NOT INERT. Cases 1-3 and 7-8 are negatives; a pattern matching
    #    nothing would pass all of them. Expected: 2 findings.
    check(len(bad("`spike/kv_isolation.py:1` and `spike/kv_profile.py:999`")) == 2,
          "two independent bad citations are both found (the rule is not inert)")

    # 10. ⚠️ THE WRONG-DIRECTORY CASE, WHICH IS THE ONE THIS CHECK EXISTS FOR AND THE ONE
    #     IT PASSED. `docs/llm-sim.py` is not tracked; the basename fallback found
    #     `scripts/llm-sim.py` and called it resolved, so "a file moved and the pointer
    #     did not" was answered by silently following the file to its new home. Every
    #     fixture above cites either a correct path or a basename that exists nowhere,
    #     so none of them could see it. Expected: caught.
    got = bad("see `docs/llm-sim.py:445` for it")
    check(len(got) == 1 and "no such tracked file" in got[0][2],
          "a path-shaped citation is NOT rescued by its basename living elsewhere")
    #     ...and the bare form still resolves, which is the behaviour being preserved.
    check(bad("see `llm-sim.py:445`") == [],
          "...while a BARE basename still resolves (the fix is narrow)")

    # 11. ⚠️ THE HYPHENATED URL. Case 8's URL fixture has no hyphen, so it could not fail;
    #     this repo's filenames are mostly hyphenated. Expected: [] for both.
    check(bad("see https://github.com/vllm/blob/main/scripts/llm-sim.py:445 online") == [],
          "a URL ending in a hyphenated filename is not read as a citation")
    check(bad("at https://example.com/foo-bar.py:80 today") == [],
          "...and neither is one whose basename is not in the tree at all")

    # 12. Reversed and impossible ranges. `hi` is in range in both, so only the reversal
    #     itself distinguishes them. Expected: caught, both.
    got = bad("`scripts/llm-sim.py:99999-2`")
    check(len(got) == 1 and "reversed" in got[0][2], "a reversed line range is caught")
    check(len(bad("`scripts/llm-sim.py:900-5`")) == 1,
          "...including one whose start is inside the file")

    # 13. Shipped RECORDs are held out of the scan, as check-doc-claims.py holds them out
    #     of the numeric one. Expected: only the non-record file is scanned.
    recs = {"prompts/shipped.md": f"{RECORD_BANNER}\n\nsee `spike/kv_isolation.py:1`\n",
            "docs/live.md": "see `scripts/llm-sim.py:1`\n"}
    check(list(scannable(recs)) == ["docs/live.md"],
          "a shipped RECORD is held out of the citation scan")
    #     ...and the same text without the banner IS scanned, so the carve-out is keyed on
    #     the banner and not on the path. Expected: both scanned.
    check(len(scannable({k: v.replace(RECORD_BANNER, "draft") for k, v in recs.items()}))
          == 2, "...and only the banner holds it out, not the directory it sits in")
    #     ⚠️ The banner is check-doc-claims.py's decision; this file is its second reader.
    #     A fork is silent in one direction, so assert the two spellings still agree.
    other = _read("scripts/check-doc-claims.py") or ""
    check(f'RECORD_BANNER = "{RECORD_BANNER}"' in other,
          "RECORD_BANNER is spelled identically in check-doc-claims.py")

    # 14. An unreadable tracked path is a None, not a traceback — and a citation to a file
    #     whose length is unknown is skipped rather than crashing on a missing key.
    check(_read("no/such/path/deleted-mid-rename.py") is None,
          "an unreadable tracked path reads as None rather than raising")
    check(offenders({"t.md": "`scripts/llm-sim.py:999999`"}, tracked, by_base, {}) == [],
          "a citation into a file with no known length is skipped, not a KeyError")

    # 15. The real tree, which is what CI asserts.
    t, b = _tree()
    texts, unreadable = _scan(t)
    lengths_live = {p: len(x.splitlines()) for p, x in texts.items()}
    files = scannable(texts)
    live = offenders(files, t, b, lengths_live)
    total = sum(len(citations(x)) for x in files.values())
    check(live == [], f"the tracked tree is clean ({total} citations, {len(files)} "
                      f"scanned file(s), {len(unreadable)} unreadable)")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-citations selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
