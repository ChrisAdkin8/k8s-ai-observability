#!/usr/bin/env python3
"""check-citations.py — every `path:line` in tracked markdown must resolve.

    python3 scripts/check-citations.py            # scan tracked *.md
    python3 scripts/check-citations.py --selftest  # unit-test the rules, no tree

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
CITE = re.compile(
    rf"(?<![\w/.])([A-Za-z0-9_.-][A-Za-z0-9_./-]*\.{EXT}):(\d+)(?:-(\d+))?")

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
    cands = by_base.get(os.path.basename(path), [])
    if len(cands) == 1:
        return cands[0], ""
    if not cands:
        return None, "no such tracked file"
    return None, f"ambiguous — {len(cands)} tracked files share that name: {cands}"


def offenders(files: dict, tracked: set, by_base: dict, lengths: dict) -> list:
    """[(citing file, citing line, cited path, detail)] for every citation that fails."""
    bad = []
    for name, text in files.items():
        for at, path, lo, hi in citations(text):
            target, why = resolve(path, tracked, by_base)
            if why:
                bad.append((name, at, path, why))
            elif target is not None:
                n = lengths[target]
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
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main() -> int:
    tracked, by_base = _tree()
    md = sorted(p for p in tracked if p.endswith(".md"))
    files = {p: _read(p) for p in md}
    lengths = {p: len(_read(p).splitlines()) for p in tracked}
    bad = offenders(files, tracked, by_base, lengths)
    total = sum(len(citations(t)) for t in files.values())
    if not bad:
        print(f"  ok  citations  {total} `path:line` reference(s) across {len(md)} "
              f"tracked markdown file(s) all resolve")
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

    # 10. The real tree, which is what CI asserts.
    t, b = _tree()
    md = sorted(p for p in t if p.endswith(".md"))
    files = {p: _read(p) for p in md}
    lengths_live = {p: len(_read(p).splitlines()) for p in t}
    live = offenders(files, t, b, lengths_live)
    total = sum(len(citations(x)) for x in files.values())
    check(live == [], f"the tracked tree is clean ({total} citations, {len(md)} files)")

    print(f"\n{'FAIL' if failures else 'PASS'}  check-citations selftest "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
