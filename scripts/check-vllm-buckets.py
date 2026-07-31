#!/usr/bin/env python3
"""check-vllm-buckets.py — fail when this repo's vLLM surface drifts from upstream's.

    python3 scripts/check-vllm-buckets.py            # check against upstream
    python3 scripts/check-vllm-buckets.py --selftest # unit-test the matching, no network

Two checks, one fetch:

  1. HISTOGRAM BUCKETS. Each of our three bucket lists must still appear in
     upstream's loggers.py verbatim.
  2. THE METRIC SET. Every `vllm:` name we emit must still be declared upstream,
     and every name upstream declares that we do not emit is reported as a gap.

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

WHY THE METRIC SET TOO. The same two releases also shipped two RENAMED metric
names, and for the same reason nothing caught it. Buckets were only ever the
narrower half of the risk that produced this file: a name we emit and upstream
has dropped is a panel that goes blank against a real deployment, which is the
identical failure mode one level up. The set check is what keeps the gap between
the ~10 series this simulator emits and the ~40 upstream declares VISIBLE rather
than silent — it is not there to close that gap, only to stop it widening
unnoticed.

⚠️ THE FILE NAME IS NOW NARROWER THAN THE CHECK. Kept anyway: the name is cited
by Taskfile.yml, ci.yml, CONTRIBUTING.md, docs/versions.md and
docs/llm-simulation.md, and renaming it to touch five files buys a better noun
and nothing else. This docstring is the specification; the name is a label.

HOW IT CHECKS. It does not try to model upstream's file structure, which would
itself be a thing that drifts. An ast walk pulls out every numeric list literal
in loggers.py, and each of our three bucket lists must appear among them
verbatim; a second walk pulls out every string literal beginning `vllm:`, from
both files, and compares the sets. That survives variable renames and code being
moved into or out of functions, and only fires when the NUMBERS or the NAMES
actually change.

⚠️ THE `_total` RULE, which is the one subtle thing here. Upstream declares
counters WITHOUT the `_total` suffix and the Prometheus client appends it at
exposition time, so our `vllm:prompt_tokens_total` and upstream's
`vllm:prompt_tokens` are the same metric. But upstream is not uniform about it:
`vllm:iteration_tokens_total` is declared WITH the suffix, as a histogram. A
blanket strip would be wrong, so the match runs in order — see `match_names()`.

EXIT CODES:  0 in sync  ·  1 drift found  ·  2 could not check (network, layout)

A gap — upstream declaring something we do not emit — is NOT drift and exits 0.
Upstream adds metrics regularly, and reddening a weekly scheduled run for each
one trains people to ignore it. Only the reverse direction, a name we emit that
upstream no longer declares, is a correctness problem: that is the rename case
that cost two releases.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import urllib.error
import urllib.request

UPSTREAM = ("https://raw.githubusercontent.com/vllm-project/vllm/main/"
            "vllm/v1/metrics/loggers.py")
HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, "llm-sim.py")
FIXTURE = os.path.join(HERE, os.pardir, "tests", "fixtures",
                       "upstream-vllm-metric-names.txt")
OURS = ("TTFT_BUCKETS", "TPOT_BUCKETS", "E2E_BUCKETS")

# The two tables in llm-sim.py that map a logical metric to the names it takes on
# each engine surface. Both are keyed logical-name -> (v0, v1), v0 FIRST, and
# that positional convention is what lets one reader handle both: METRIC_SURFACES
# holds 1:1 renames, METRIC_RESHAPES holds the cases where the shape changed and
# the two sides have different lengths.
SURFACE_TABLES = ("METRIC_SURFACES", "METRIC_RESHAPES")

# A metric name at the START of a string literal. Anchored, and tokenised rather
# than taken whole, because upstream embeds names in prose:
#   "vllm:lora_requests_info prometheus metrics may be inaccurate ..."
# is a warning message, not a declaration, and the name it opens with is declared
# properly elsewhere in the same file.
NAME_RE = re.compile(r"vllm:[A-Za-z0-9_]+")

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


def metric_names(tree):
    """Every `vllm:` metric name that a string literal in `tree` begins with.

    Deliberately blind to HOW the name is used. Upstream passes it as
    `name="vllm:foo"` to a metric constructor; this repo writes it into an
    exposition line, sometimes as the constant head of an f-string. Both are
    string literals starting with the name, and neither structure is one this
    check should depend on — that is the same reasoning the bucket walk uses.
    """
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = NAME_RE.match(node.value)
            if match:
                found.add(match.group(0))
    return found


def parse_local():
    """llm-sim.py as an AST, read WITHOUT importing it."""
    with open(LOCAL, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read())


def our_buckets(tree):
    """The three bucket constants, as module-level literals."""
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


def our_v0_aliases(tree):
    """Names emitted ONLY under the superseded `--vllm-surface v0`.

    These must never be reported as drift: they are deliberately not upstream's
    current surface, which is the entire point of emitting them. Read from the
    surface tables by position — v0 is the first element of every entry — rather
    than kept as a second list here, because a hand-kept list of names in a repo
    that already holds them is exactly the kind of copy that goes stale.
    """
    tables = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in SURFACE_TABLES:
            tables[node.targets[0].id] = node.value
    if not tables:
        sys.exit(f"ERROR: none of {', '.join(SURFACE_TABLES)} found in {LOCAL} — "
                 f"has the surface mechanism been restructured? Without it this "
                 f"check cannot tell a deliberate v0 alias from real drift.")

    aliases = set()
    for table in tables.values():
        if not isinstance(table, ast.Dict):
            continue
        for value in table.values:
            if isinstance(value, (ast.Tuple, ast.List)) and value.elts:
                aliases |= metric_names(value.elts[0])       # elts[0] is the v0 side
    return aliases


def match_names(ours, upstream):
    """Match what we emit against what upstream declares.

    Returns (matched, ours_only), where `matched` maps each of our names to the
    upstream name it corresponds to. Order matters — see the `_total` note in the
    module docstring:

        exact match against upstream                              -> matched
        else, ends "_total", unsuffixed form IS upstream
              and the suffixed form is NOT upstream               -> matched
        else                                                      -> ours-only

    The second clause is the Prometheus client appending `_total` to a counter at
    exposition time. Its third condition is what keeps `vllm:iteration_tokens_total`
    — declared upstream WITH the suffix, and a histogram — from being matched
    against a `vllm:iteration_tokens` that does not exist.
    """
    matched, ours_only = {}, []
    for name in sorted(ours):
        if name in upstream:
            matched[name] = name
            continue
        base = name[:-len("_total")] if name.endswith("_total") else None
        if base and base in upstream and name not in upstream:
            matched[name] = base
            continue
        ours_only.append(name)
    return matched, ours_only


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


def read_fixture(path):
    """A stubbed upstream metric set: one name per line, `#` comments ignored."""
    with open(path, "r", encoding="utf-8") as fh:
        return {ln.strip() for ln in fh
                if ln.strip() and not ln.lstrip().startswith("#")}


def selftest():
    """Unit-test the matching rules against a stubbed upstream set.

    NO NETWORK. The `_total` rule is the one piece of judgement in this file and
    the one that fails silently when it is wrong — a blanket strip reports no
    drift where there is some, and no strip at all reports drift on every counter
    we emit. Neither shows up in the real run, which prints a plausible answer
    either way. So it is checked here, on every push, against a fixture that
    cannot move under us.
    """
    failures = []

    def check(ok, label):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)

    print("check-vllm-buckets --selftest")

    try:
        upstream = read_fixture(FIXTURE)
    except OSError as exc:
        print(f"  FAIL  fixture unreadable: {exc}")
        return 1
    print(f"  ({len(upstream)} stubbed upstream names from "
          f"{os.path.relpath(FIXTURE, os.path.join(HERE, os.pardir))})")

    # A stubbed set of OUR names too, so this tests the matching rules and not
    # whatever scripts/llm-sim.py happens to emit today.
    ours = {
        "vllm:num_requests_running",        # exact match
        "vllm:prompt_tokens_total",         # client suffix: upstream has vllm:prompt_tokens
        "vllm:iteration_tokens_total",      # upstream declares the suffix ITSELF
        "vllm:renamed_away_total",          # upstream declares neither form -> drift
        "vllm:gone_without_trace",          # upstream declares it no longer  -> drift
    }
    matched, ours_only = match_names(ours, upstream)

    check(matched.get("vllm:num_requests_running") == "vllm:num_requests_running",
          "an exactly-matching name matches")
    check(matched.get("vllm:prompt_tokens_total") == "vllm:prompt_tokens",
          "vllm:prompt_tokens_total matches upstream's vllm:prompt_tokens "
          "(client appends the suffix)")
    check(matched.get("vllm:iteration_tokens_total") == "vllm:iteration_tokens_total",
          "vllm:iteration_tokens_total matches ITSELF, not a stripped form "
          "(declared upstream with the suffix)")
    check(sorted(ours_only) == ["vllm:gone_without_trace", "vllm:renamed_away_total"],
          "names upstream declares in neither form are reported as drift")

    # The gap direction: upstream declares it, we do not emit it. Not drift.
    gap = sorted(upstream - set(matched.values()))
    check(gap == ["vllm:not_emitted_here", "vllm:some_new_metric"],
          "names upstream declares and we do not emit are reported as a gap")

    # ⚠️ The trap the third condition exists for. Strip `_total` blindly and this
    # one is matched against a metric that does not exist upstream; the run then
    # reports "in sync" while a name we emit has no upstream counterpart at all.
    blind = {n[:-6] if n.endswith("_total") else n for n in ours}
    check("vllm:iteration_tokens" not in upstream and
          "vllm:iteration_tokens" in blind,
          "a blanket _total strip WOULD mis-match it — the ordering is load-bearing")

    # The v0 aliases must survive being read out of llm-sim.py's surface tables,
    # or every one of them is reported as drift on the next weekly run.
    aliases = our_v0_aliases(parse_local())
    check(len(aliases) >= 2 and all(n.startswith("vllm:") for n in aliases),
          f"v0 aliases read from the surface tables: {', '.join(sorted(aliases))}")

    print()
    if failures:
        print(f"SELFTEST FAILED ({len(failures)} check(s))")
        return 1
    print("SELFTEST PASSED")
    return 0


def check_buckets(local_tree, upstream_lists):
    """Report bucket drift. Returns the number of lists that have moved."""
    drift = 0
    for name, ours in our_buckets(local_tree).items():
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
    return drift


def check_metric_set(local_tree, upstream_names):
    """Report metric-set drift. Returns the number of names that have moved.

    A gap is printed but not counted: see the exit-code note in the docstring.
    """
    aliases = our_v0_aliases(local_tree)
    ours = metric_names(local_tree) - aliases
    matched, ours_only = match_names(ours, upstream_names)

    suffixed = sorted(n for n, up in matched.items() if n != up)
    print(f"  ok    {len(matched):2d} of the {len(ours)} names we emit are declared upstream")
    if suffixed:
        print(f"        {len(suffixed)} matched on the client's `_total` suffix: "
              f"{', '.join(n.rsplit(':', 1)[1] for n in suffixed)}")
    if aliases:
        print(f"  ok    {len(aliases):2d} v0 alias(es) excluded, emitted only under "
              f"--vllm-surface v0")
        for name in sorted(aliases):
            print(f"        {name}")

    for name in ours_only:
        print(f"  DRIFT {name} is emitted here and declared NOWHERE upstream")

    gap = sorted(upstream_names - set(matched.values()))
    if gap:
        print(f"\n  gap   {len(gap)} upstream metric(s) this simulator does not emit.")
        print("        Not drift, and not a failure — upstream adds metrics regularly.")
        print("        This is the list to pick from when closing one:")
        for name in gap:
            print(f"          {name}")
    return len(ours_only)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true",
                    help="unit-test the matching rules against a committed "
                         "fixture and exit (no network)")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    print(f"vLLM upstream drift check\n  upstream: {UPSTREAM}\n  local:    {LOCAL}\n")
    try:
        with urllib.request.urlopen(UPSTREAM, timeout=TIMEOUT) as resp:
            upstream_src = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"COULD NOT CHECK: fetching upstream failed: {exc}", file=sys.stderr)
        print("This is not a drift result — it is an absence of one.", file=sys.stderr)
        return 2

    upstream_lists = numeric_lists(upstream_src, UPSTREAM)
    upstream_names = metric_names(ast.parse(upstream_src))
    if not upstream_lists or not upstream_names:
        print("COULD NOT CHECK: no numeric list literals and/or no vllm: metric "
              "names found upstream. The file has probably been restructured; "
              "re-derive this check by hand.", file=sys.stderr)
        return 2
    print(f"  {len(upstream_lists)} numeric list literals and "
          f"{len(upstream_names)} vllm: metric names found upstream\n")

    local_tree = parse_local()

    print("histogram buckets:")
    drift = check_buckets(local_tree, upstream_lists)
    print("\nmetric set:")
    drift += check_metric_set(local_tree, upstream_names)

    print()
    if drift:
        print(f"{drift} bucket list(s) and/or metric name(s) have drifted from "
              f"upstream vLLM.")
        print("Percentile panels and SLOs built here will NOT transfer until this is")
        print("reconciled. Update scripts/llm-sim.py, then re-derive the expected")
        print("values in tests/rules/llm-rules_test.yaml — they are pinned to specific")
        print("boundaries on purpose, so they will fail until you do.")
        return 1
    print("All bucket boundaries and emitted metric names match upstream vLLM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
