#!/usr/bin/env python3
"""check-vllm-buckets.py — fail when upstream vLLM's histogram buckets move.

    python3 scripts/check-vllm-buckets.py

WHY THIS EXISTS. Releases 0.1.0 and 0.2.0 shipped the v0.6.x bucket layout while
the V1 engine had replaced TTFT's entire tail. Nothing failed. Every test in this
repo passed, because every test reads the simulator, and the simulator was
internally consistent with itself — it was consistent with the wrong thing. The
saturated tenant sits at ~58s, squarely inside the tail that had diverged, so the
one number this rig exists to teach you to read was the one number it got wrong.

A drifted bucket boundary is the worst failure mode this repo has: it does not
error, it does not blank a panel, it returns a confident and plausible percentile
that will not match real hardware. Nothing in a self-contained test suite can
catch it, because the fault is in the relationship to something OUTSIDE the
suite. So this points at upstream, and CI runs it weekly beside the existing
Helm-chart drift detection — same pattern, same reasoning.

HOW IT CHECKS. It does not try to model upstream's file structure, which would
itself be a thing that drifts: an ast walk pulls out every numeric list literal
in loggers.py, and each of our three bucket lists must appear among them
verbatim. That survives variable renames and code being moved into or out of
functions, and only fires when the NUMBERS actually change.

EXIT CODES:  0 in sync  ·  1 drift found  ·  2 could not check (network, layout)
"""
from __future__ import annotations

import ast
import os
import sys
import urllib.error
import urllib.request

UPSTREAM = ("https://raw.githubusercontent.com/vllm-project/vllm/main/"
            "vllm/v1/metrics/loggers.py")
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm-sim.py")
OURS = ("TTFT_BUCKETS", "TPOT_BUCKETS", "E2E_BUCKETS")
TIMEOUT = 30


def numeric_lists(source, path):
    """Every list-of-numbers literal in `source`, as tuples of float."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        sys.exit(f"ERROR: could not parse {path}: {exc}")
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        vals = []
        for el in node.elts:
            # ast.Constant covers ints and floats; anything else (a name, a call,
            # a nested list) disqualifies the literal rather than being coerced.
            if isinstance(el, ast.Constant) and isinstance(el.value, (int, float)) \
                    and not isinstance(el.value, bool):
                vals.append(float(el.value))
            else:
                vals = None
                break
        if vals:
            out.append(tuple(vals))
    return out


def our_buckets():
    """The three bucket constants, read from llm-sim.py WITHOUT importing it."""
    with open(LOCAL, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in OURS:
            found[node.targets[0].id] = tuple(
                float(e.value) for e in node.value.elts)
    missing = [n for n in OURS if n not in found]
    if missing:
        sys.exit(f"ERROR: {', '.join(missing)} not found in {LOCAL} — has it been "
                 f"restructured? This check reads them as module-level literals.")
    return found


def closest(needle, haystack):
    """The upstream list sharing the longest prefix with `needle`."""
    best, best_len = None, -1
    for cand in haystack:
        n = 0
        for a, b in zip(needle, cand):
            if a != b:
                break
            n += 1
        if n > best_len:
            best, best_len = cand, n
    return best, best_len


def fmt(seq):
    return "[" + ", ".join(f"{v:g}" for v in seq) + "]"


def main():
    print(f"vLLM bucket drift check\n  upstream: {UPSTREAM}\n  local:    {LOCAL}\n")
    try:
        with urllib.request.urlopen(UPSTREAM, timeout=TIMEOUT) as resp:
            upstream_src = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"COULD NOT CHECK: fetching upstream failed: {exc}", file=sys.stderr)
        print("This is not a drift result — it is an absence of one.", file=sys.stderr)
        return 2

    upstream_lists = numeric_lists(upstream_src, UPSTREAM)
    if not upstream_lists:
        print("COULD NOT CHECK: no numeric list literals found upstream. The file "
              "has probably been restructured; re-derive this check by hand.",
              file=sys.stderr)
        return 2
    print(f"  {len(upstream_lists)} numeric list literals found upstream\n")

    drift = 0
    for name, ours in our_buckets().items():
        if ours in upstream_lists:
            print(f"  ok    {name:14} {len(ours):2d} boundaries, present upstream verbatim")
            continue
        drift += 1
        cand, shared = closest(ours, upstream_lists)
        print(f"  DRIFT {name:14} no upstream list matches these {len(ours)} boundaries")
        print(f"        ours:     {fmt(ours)}")
        if cand:
            print(f"        nearest:  {fmt(cand)}")
            print(f"        first {shared} boundaries agree, then they diverge"
                  if shared else "        they diverge immediately")

    print()
    if drift:
        print(f"{drift} bucket list(s) have drifted from upstream vLLM.")
        print("Percentile panels and SLOs built here will NOT transfer until this is")
        print("reconciled. Update scripts/llm-sim.py, then re-derive the expected")
        print("values in tests/rules/llm-rules_test.yaml — they are pinned to specific")
        print("boundaries on purpose, so they will fail until you do.")
        return 1
    print("All bucket boundaries match upstream vLLM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
