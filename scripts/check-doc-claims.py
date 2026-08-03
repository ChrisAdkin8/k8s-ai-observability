#!/usr/bin/env python3
"""check-doc-claims.py — fail when a number in the prose drifts from the code it describes.

    python3 scripts/check-doc-claims.py            # check the working tree
    python3 scripts/check-doc-claims.py --selftest # test the matchers on fixtures

Every check here is a correction commit that already happened, mechanised. The 0.6.0
"Fixed" entry alone corrected SEVEN prose numbers in one release — alert count, emitted
metric count, the L-check range in two files, the PromQL query count, the `${datasource}`
reference count — and the changelog says why it matters: *"every one of them is a number a
reader checks this repo's credibility against."* The ITL caveat wrong by 2x reached a
PUBLISHED board the same way.

The pattern behind all of them: a fact stated in prose in more than one place, with no
machine comparing the copies. This script is the machine. It holds NO copy of any truth —
every expected value is derived at run time from the file that owns it.

WHAT IT CHECKS
  1. Dashboard ids — every grafana.com id in tracked markdown is one the dashboards README
     knows, from its catalog table or its prose. This is the 25619->25620 incident.
  2. Emitted metric count — vs `llm-sim.py --print`. The preemptions removal moved this
     16 -> 15 and two files needed hand-correction.
  3. LLM alert count — vs `- alert:` entries in the rule file. The SLO's two burn alerts
     moved it 4 -> 6.
  4. The `L1-Ln` verify.sh range — vs the highest L actually implemented. L9 shipped while
     two files still said L8. ⚠️ This one scans verify.sh itself as well as markdown: the
     stale range was in that script's own header.
  5. PromQL query count — vs the numbered queries in observability.md. Said eight, had
     nine since `ALERTS{alertstate="firing"}` was added.
  6. `${datasource}` reference count — vs the LLM board. Said 22, "true when that board had
     nine panels, 33 now".

WHAT IT DELIBERATELY DOES NOT CHECK
  * **The drift-check gap count** ("23 upstream metrics this simulator does not emit",
    CONTRIBUTING.md). It is NOT derivable offline: the only local upstream list is
    tests/fixtures/upstream-vllm-metric-names.txt, which its own header says is "a STUBBED
    upstream vLLM metric set — NOT a copy of the real one, and it must never be updated to
    track upstream". Deriving from it would compare the prose against a fiction and pass.
    The real number needs the network, which this script and `task preflight` refuse; it
    belongs to check-vllm-buckets.py's weekly job.
  * The "around 40" upstream series count — same reason, and the prose hedges it on purpose.
  * grafana.com revision currency — needs the network and an account; recorded as prose in
    manifests/dashboards/README.md instead.
  * CHANGELOG.md — it is the historical record. A superseded number THERE is history, not
    drift, so it is excluded from every scan.

⚠️ A CHECKER THAT FINDS NOTHING MUST DIE, NOT PASS. If a claim is reworded so its matcher
stops matching, the check is dead — and a dead check reporting green is the exact failure
genre this repo writes assertions against. Every check therefore treats "no claims found"
as fatal and names itself in the error, so the fix is to teach it the new phrasing rather
than to wonder why it went quiet. --selftest pins every matcher to a committed fixture so a
regex edit cannot quietly retire one.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS_README = "manifests/dashboards/README.md"
EXCLUDED = {"CHANGELOG.md"}  # append-only history; stale numbers there are the record

# Prose spells small numbers as words ("six alerts", "nine PromQL queries") and larger
# ones as digits ("33 of them"). Both forms have to be readable or the matchers miss
# exactly the claims that bit.
WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM = r"(\d+|" + "|".join(WORDS) + r")"


def to_int(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


def die(check: str, msg: str) -> None:
    print(f"check-doc-claims: FAIL [{check}]: {msg}", file=sys.stderr)
    sys.exit(1)


def read(rel: str) -> str:
    p = ROOT / rel
    if not p.is_file():
        die("setup", f"{rel} is missing — it owns a value the prose is checked against")
    return p.read_text()


# ---------------------------------------------------------------- derivations (truth)
# Each returns the single number the prose is compared against, derived from the file
# that owns it. None of them may fall back to a literal.

def derive_emitted() -> int:
    """Distinct vllm: families in one scrape at default settings."""
    out = subprocess.run([sys.executable, "scripts/llm-sim.py", "--print"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        die("emits", f"llm-sim.py --print exited {out.returncode}; cannot derive the "
                     f"metric count\n{out.stderr}")
    n = sum(1 for line in out.stdout.splitlines() if line.startswith("# HELP vllm:"))
    return n or die("emits", "--print rendered no vllm: families — the derivation is dead")


def derive_llm_alerts() -> int:
    n = len(re.findall(r"^\s*- alert:", read("manifests/alerts/llm-prometheusrule.yaml"),
                       re.MULTILINE))
    return n or die("alerts", "no `- alert:` entries in the LLM rule file — dead")


def derive_max_l() -> int:
    """Highest L-check verify.sh actually implements, from its pass/fail/skip strings.

    Read from the CALLS rather than the `# Ln.` section comments: a comment is prose and
    could itself be the thing that drifted. A check that reports a result is the check.
    """
    ns = [int(m) for m in re.findall(r'(?:pass|fail|skip) "L(\d+)', read("scripts/verify.sh"))]
    return max(ns) if ns else die("l-range", "no L-check pass/fail calls in verify.sh — dead")


def derive_promql_queries() -> int:
    """Numbered queries in the 'Queries to start with' block — `# 1.` … `# 9.`."""
    n = len(re.findall(r"^# \d+\.", read("docs/observability.md"), re.MULTILINE))
    return n or die("promql", "no numbered queries in observability.md — dead")


def derive_datasource_refs() -> int:
    n = read("manifests/dashboards/llm-sim-overview.json").count("${datasource}")
    return n or die("datasource", "no ${datasource} references on the LLM board — dead")


# ------------------------------------------------------------------- numeric checks
# pattern  : must capture the claimed number as group 1
# context  : the claim only counts on a line ALSO matching this — so a number about
#            something else is never dragged into the comparison
# extra    : non-markdown files to scan as well (L-range's stale copy was in verify.sh)

NUMERIC_CHECKS = [
    # ⚠️ The context names WHAT IS BEING COUNTED, and must. `upstream|vllm` was too
    # loose: "Real vLLM only ever emits one surface" is a true sentence about metric
    # SURFACES (v0/v1) on a line that mentions vLLM, and it was dragged into a families
    # comparison on this check's first real run. Counted nouns only.
    dict(name="emits", pattern=rf"\bemits {NUM}\b", context=r"\b(metric|series|name)s?\b",
         derive=derive_emitted, unit="vllm: families the simulator emits",
         hint="fix the prose, or the simulator, so they agree"),
    dict(name="alerts", pattern=rf"\b{NUM} alerts?\b", context=r"llm",
         derive=derive_llm_alerts, unit="alerts in the LLM rule file",
         hint="count `- alert:` in manifests/alerts/llm-prometheusrule.yaml"),
    dict(name="l-range", pattern=r"\bL1[-–—]L(\d+)\b", context=r".",
         derive=derive_max_l, unit="highest verify.sh LLM check", extra=["scripts/verify.sh"],
         hint="verify.sh's own header counts too — it was stale last time"),
    dict(name="promql", pattern=rf"\b{NUM} PromQL queries\b", context=r".",
         derive=derive_promql_queries, unit="numbered queries in observability.md",
         hint="they are the `# N.` comments inside the promql block"),
    dict(name="datasource", pattern=rf"\b{NUM} of them\b", context=r"datasource",
         derive=derive_datasource_refs, unit="${datasource} refs on the LLM board",
         hint="count them in manifests/dashboards/llm-sim-overview.json"),
]

URL_ID = re.compile(r"grafana\.com/grafana/dashboards/(\d+)")
# Prose blessings in the dashboards README: "grafana.com id 12239", "board 12239".
# 4+ digits so a bare year ("2026") can never bless an id.
PROSE_ID = re.compile(r"\b(?:board|id)\s+(\d{4,})\b", re.IGNORECASE)


def claims(pattern: str, context: str, path: str, text: str) -> list[tuple[str, int, int]]:
    ctx, pat = re.compile(context, re.I), re.compile(pattern, re.I)
    return [(path, n, to_int(m.group(1)))
            for n, line in enumerate(text.splitlines(), 1) if ctx.search(line)
            for m in pat.finditer(line)]


def blessed_ids(readme_text: str) -> set[str]:
    return set(URL_ID.findall(readme_text)) | set(PROSE_ID.findall(readme_text))


def referenced_ids(path: str, text: str) -> list[tuple[str, int, str]]:
    return [(path, n, m) for n, line in enumerate(text.splitlines(), 1)
            for m in URL_ID.findall(line)]


def tracked(pattern: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", pattern],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p and p not in EXCLUDED]


def main() -> None:
    md = tracked("*.md")
    if not md:
        die("setup", "git ls-files returned no markdown — every scan is dead")
    texts = {p: (ROOT / p).read_text() for p in md}

    # ---- dashboard ids: a set comparison, not a count
    blessed = blessed_ids(read(DASHBOARDS_README))
    if not blessed:
        die("ids", f"no dashboard ids parsed from {DASHBOARDS_README} — the blessing is dead")
    refs = [r for p, t in texts.items() for r in referenced_ids(p, t)]
    if not refs:
        die("ids", "no grafana.com dashboard references in any tracked markdown — dead")
    strays = [(p, n, i) for p, n, i in refs if i not in blessed]
    if strays:
        for p, n, i in strays:
            print(f"  {p}:{n}: dashboard id {i} is not known to {DASHBOARDS_README}",
                  file=sys.stderr)
        die("ids", "stale or undocumented dashboard id(s). If the reference is deliberate "
                   f"(someone else's board), give it a home in {DASHBOARDS_README} first.")
    print(f"  ok  ids        {len(refs):>3} references, all known to the catalog "
          f"({', '.join(sorted(blessed))})")

    # ---- the numeric claims
    for c in NUMERIC_CHECKS:
        scan = dict(texts)
        for rel in c.get("extra", []):
            scan[rel] = read(rel)
        found = [x for p, t in scan.items() for x in claims(c["pattern"], c["context"], p, t)]
        if not found:
            die(c["name"], f'no claim matched /{c["pattern"]}/ anywhere — the check is '
                           f"dead. If the wording changed, teach the pattern the new one.")
        actual = c["derive"]()
        wrong = [(p, n, v) for p, n, v in found if v != actual]
        if wrong:
            for p, n, v in wrong:
                print(f"  {p}:{n}: claims {v}; derived {actual} ({c['unit']})",
                      file=sys.stderr)
            die(c["name"], f"prose drifted from code — {c['hint']}")
        print(f"  ok  {c['name']:<10} {len(found):>3} claim(s) match the derived "
              f"{actual} ({c['unit']})")


# --------------------------------------------------------------------------- selftest
# Committed fixtures, so a regex edit cannot quietly retire a matcher — the same reason
# check-vllm-buckets.py tests its matching rules against a fixture rather than live input.

BLESS_FIXTURE = """\
| GPU board | `gpu-sim-dcgm.json` | [25618](https://grafana.com/grafana/dashboards/25618-gpu-simulation-dcgm-overview/) |
Swapping in the fuller upstream DCGM board (grafana.com id 12239) is supported.
Published in 2026, revision 2.
"""

SCAN_FIXTURE = """\
See [25618](https://grafana.com/grafana/dashboards/25618) for the GPU board,
and [25619](https://grafana.com/grafana/dashboards/25619) for the old typo.
"""

CLAIM_FIXTURE = """\
Upstream declares around 40 `vllm:` metrics and this simulator emits 15 of them.
| `llm-simulation-alerts` | `monitoring` | Recording rules + six alerts |
The GPU rule file carries three alerts.
| the simulators serve the surface | `verify.sh` L1-L9 | `helm test` |
nine PromQL queries that also work against real hardware
repoints every `${datasource}` reference — 33 of them on the LLM board — and drops it.
The exporter emits 3 events per scrape, and 12 of them are dropped.
Real vLLM only ever emits one surface — `both` is a rig affordance, not a fidelity claim.
"""


def selftest() -> None:
    print("check-doc-claims --selftest")

    got = blessed_ids(BLESS_FIXTURE)
    assert got == {"25618", "12239"}, got  # 2026 must NOT be blessed by the year
    print("  ok  ids        catalog URL + prose id bless; a bare year does not")

    refs = referenced_ids("f.md", SCAN_FIXTURE)
    assert [i for _, _, i in refs] == ["25618", "25619"], refs
    assert [i for _, _, i in refs if i not in got] == ["25619"]
    print("  ok  ids        the 25619-class stray is flagged, the known id is not")

    # Every numeric matcher, against one fixture holding all five claim shapes plus
    # three decoys. The decoys are the point, and the third is a REGRESSION TEST: "3
    # events" and "12 of them" must not reach the alert or datasource comparisons, and
    # "emits one surface" must not reach the families comparison — it did, on this
    # check's first real run against the tree, because the context regex named the
    # subject (vLLM) instead of the counted noun.
    expected = {"emits": [15], "alerts": [6], "l-range": [9],
                "promql": [9], "datasource": [33]}
    for c in NUMERIC_CHECKS:
        vals = [v for _, _, v in claims(c["pattern"], c["context"], "f.md", CLAIM_FIXTURE)]
        assert vals == expected[c["name"]], (c["name"], vals)
        print(f"  ok  {c['name']:<10} extracted {vals} from the fixture, decoys ignored")

    assert to_int("six") == 6 and to_int("33") == 33
    assert claims(NUMERIC_CHECKS[1]["pattern"], NUMERIC_CHECKS[1]["context"],
                  "f.md", "The GPU rule file carries three alerts.") == []
    print("  ok  words      word and digit forms both parse; contextless claims ignored")

    for c in NUMERIC_CHECKS:
        assert claims(c["pattern"], c["context"], "f.md", "nothing relevant") == []
    assert blessed_ids("no ids at all") == set()
    print("  ok  empty      no-match is representable — main() treats it as fatal")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
