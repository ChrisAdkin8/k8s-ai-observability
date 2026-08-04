#!/usr/bin/env python3
"""check-sigpipe.py — find pipes whose consumer exits before the producer finishes.

⚠️ THIS CLASS HAS COST THIS REPO TWICE IN ONE DAY.

Under `set -o pipefail`, a consumer that stops reading early (`head`, `grep -q`,
`grep -m1`) closes the pipe while the producer is still writing. The producer takes
SIGPIPE, exits 141, and pipefail promotes that to the status of the whole pipeline
— so the pipeline "fails" on input that was perfectly correct, or, worse, an `if`
guarding an assertion evaluates FALSE exactly when the thing it looks for is
present.

Both bites were real:

  * `tar tzf "$tgz" | head -40` failed the first chart publish with exit 141, on an
    archive that contained everything it was asked to prove.
  * `echo "$changed" | grep -qvE '\\.md$'` in the CI changes filter returned 141 on a
    large diff, which reads as "markdown only" and SKIPS THE CLUSTER JOBS ON A CODE
    CHANGE — the opposite of the fail-open posture stated in the same file.

⚠️ IT DOES NOT REPRODUCE UNDER zsh, which is why it survives local testing. The
second case above was verified to return 0 under zsh and `code=FALSE` under bash.
CI runs bash. Test this class with `bash -c`, never with an interactive shell.

Not every hit is a bug: if the producer's entire output fits in the pipe buffer it
finishes writing before the consumer exits, and nothing is signalled. That depends
on the data, not the code, which is why this refuses to guess. Every hit must be
either rewritten without the pipe, or marked:

    # sigpipe-ok: <why the producer's output cannot fill the pipe buffer>

The marker goes on the line itself or on any of the three lines above it. Writing
one is meant to be slightly annoying: the justification is the point, and "it works
on my machine" is not one.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Consumers that may stop reading before the producer is done.
CONSUMER = re.compile(
    r"\|\s*(?:head\b|tail\s+-\d|grep\s+(?:-\w*q\w*|-m\s*\d)|sed\s+-n\s+['\"]?\d*[qp]|sed\s+['\"]?\d*q)"
)
MARKER = re.compile(r"sigpipe-ok:")
LOOKBACK = 3


def tracked(pattern: str) -> list:
    out = subprocess.run(["git", "ls-files", pattern], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"check-sigpipe: git ls-files exited {out.returncode} — the scan "
                 f"cannot enumerate tracked files")
    return [p for p in out.stdout.splitlines() if p]


def main() -> int:
    files = tracked("*.sh") + tracked("*.yml") + tracked("*.yaml")
    hits, marked = [], 0
    for rel in files:
        path = ROOT / rel
        try:
            lines = path.read_text().split("\n")
        except (OSError, UnicodeDecodeError):
            continue
        # Only files that actually turn pipefail on can be bitten by it.
        if "pipefail" not in "\n".join(lines):
            continue
        for n, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or not CONSUMER.search(line):
                continue
            window = lines[max(0, n - 1 - LOOKBACK):n]
            if any(MARKER.search(w) for w in window):
                marked += 1
                continue
            hits.append((rel, n, stripped[:88]))

    if not files:
        sys.exit("check-sigpipe: no tracked shell or workflow files — the scan is dead")

    for rel, n, text in hits:
        print(f"  {rel}:{n}: {text}", file=sys.stderr)
    if hits:
        print(f"\ncheck-sigpipe: FAIL — {len(hits)} pipe(s) into an early-exiting "
              f"consumer, in a file with pipefail on, with no justification.\n"
              f"       Rewrite without the pipe (read a file, or use one command), or "
              f"add\n"
              f"         # sigpipe-ok: <why the producer cannot fill the pipe buffer>\n"
              f"       on the line or up to {LOOKBACK} lines above it.", file=sys.stderr)
        return 1
    print(f"  ok  sigpipe    no unjustified early-exit pipes ({marked} justified)")
    return 0


def selftest() -> int:
    print("check-sigpipe --selftest")
    # ⚠️ The consumer patterns, and the shapes that must NOT trip it. `grep -q` after
    # a pipe is the bite; `grep -q` on a FILE is the fix, and flagging the fix would
    # make the check impossible to satisfy.
    must = [
        "tar tzf x.tgz | head -40",
        'echo "$c" | grep -qvE "\\.md$"',
        "cmd | grep -q foo",
        "cmd | grep -m1 foo",
        "cmd | head -1",
        "cmd | tail -1",
    ]
    must_not = [
        "grep -q foo file.txt",              # no pipe: the fix
        "grep -qE '^v.*s' /tmp/scrape.txt",  # no pipe: the fix
        "cmd | grep foo",                    # reads to EOF
        "cmd | sort -rn",                    # reads to EOF
        "cmd | wc -l",                       # reads to EOF
    ]
    for s in must:
        assert CONSUMER.search(s), f"should have matched: {s}"
    for s in must_not:
        assert not CONSUMER.search(s), f"should NOT have matched: {s}"
    print(f"  ok  patterns   {len(must)} early-exit shapes matched, "
          f"{len(must_not)} safe shapes ignored")
    assert MARKER.search("# sigpipe-ok: kind lists at most a few clusters")
    assert not MARKER.search("# this is fine, honestly")
    print("  ok  marker     only an explicit sigpipe-ok: justification counts")
    print("\nSELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
