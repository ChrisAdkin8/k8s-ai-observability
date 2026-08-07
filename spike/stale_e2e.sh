#!/usr/bin/env bash
# Does LLMMetricsStale fire against a REAL frozen simulator scraped by a REAL
# Prometheus -- rather than against a hand-written promtool series?
#
# This is the question ROADMAP.md item 1 exists to ask, aimed at the one alert
# this work adds. spike/spike_test.yaml already proves the EXPRESSION in promtool.
# promtool feeds it a series shaped the way we assumed a frozen tenant looks. It
# cannot tell us whether a frozen tenant actually looks like that, which is the
# whole distinction the item is built on.
#
# Three simulators, one Prometheus, one freeze delivered through the profile file
# exactly as a drill would deliver it. No Kubernetes and no compose plugin.
#
# ⚠️ REQUIRES W3's freeze knob and W0's faults block, which do not exist yet.
# This is stage-3 evidence for prompt-fault-injection.md, kept because every
# number that prompt now quotes comes from here and a reader who cannot rerun
# it has to take them on trust. It ran green against the spike implementation
# on 2026-08-07; the spike branch was deleted, as stage 3 says it should be.
# Re-point it at the real implementation when W0 and W3 land.
#
# ⚠️ bash, and tested with `bash -c` (rule 17). Run from the repo root:
#     bash spike/stale_e2e.sh
set -euo pipefail

NET=spike-fi
PROM_PORT=19090
# ⚠️ NOT mktemp. colima mounts $HOME into the VM and does NOT mount macOS's
# /var/folders temp root, so a bind mount from mktemp -d fails at container
# start with a "not a directory" error that reads like a file-vs-directory bug
# and is really a mount-namespace one. Staying under the repo keeps the host path
# inside the VM's view on both colima and Docker Desktop.
WORK="$(pwd)/spike/.e2e-work"
rm -rf "$WORK"
SIM="$(pwd)/scripts/llm-sim.py"
FAILURES=0

cleanup() {
  docker rm -f prom-spike sim-steady sim-saturated sim-driven sim-idle >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# ⚠️ ASSIGN, THEN TEST -- never `[ "$(qn "q with \"quotes\"")" = x ]`.
# bash parses the escaped quotes inside a command substitution inside a test's
# quoted argument as argument separators and dies with "[: too many arguments";
# zsh accepts the same line and returns the right answer. CI is bash (rule 17).
# Every expectation here is scoped by model_name, so quoted label matchers inside
# a query inside a substitution is THE shape this harness is made of, and
# verify.sh's habit of assigning to a variable first turns out to be load-bearing
# rather than stylistic. shellcheck is silent on it at every severity.
nq() { local v; v="$(qn "$1")"; printf '%s\n' "${v:-0}"; }

check() {  # check <ok> <label> [detail]
  if [ "$1" = "1" ]; then printf '  PASS  %s%s\n' "$2" "${3:+ -- $3}"
  else printf '  FAIL  %s%s\n' "$2" "${3:+ -- $3}"; FAILURES=$((FAILURES + 1)); fi
}

# ⚠️ EXACTLY ONE LINE OUT, ALWAYS, and that is harder than it looks under
# `set -o pipefail`. Written first as `curl | python3 || echo '[]'`, this emitted
# python's output AND the fallback whenever pipefail failed the pipeline -- two
# lines into a `[ "$(qn ...)" != "0" ]`, which becomes `[: too many arguments`.
# The test then neither passed nor failed: it errored past, every poll returned
# "not yet", and the measurement loop ran to its deadline while the alert had
# been firing for four minutes. A poll helper that can return two lines silently
# disables its own caller, and prints nothing to say so.
q() {  # q <promql> -> the instant-query result array ([] on any failure)
  local out=""
  out="$(curl -sG "http://localhost:${PROM_PORT}/api/v1/query" \
           --data-urlencode "query=$1" 2>/dev/null \
         | python3 -c 'import sys,json
try:    print(json.dumps(json.load(sys.stdin)["data"]["result"]))
except Exception: print("[]")' 2>/dev/null)" || out=""
  printf '%s\n' "${out:-[]}"
}
qn() {  # q <promql> -> the number of series, as one integer, 0 on any failure
  local out=""
  out="$(q "$1" | python3 -c 'import sys,json
try:    print(len(json.loads(sys.stdin.read() or "[]")))
except Exception: print(0)' 2>/dev/null)" || out=""
  printf '%s\n' "${out:-0}"
}

echo "== staging profiles and rules =="
mkdir -p "$WORK/profiles" "$WORK/rules"
./scripts/extract.sh profiles "$WORK/profiles" >/dev/null
./scripts/extract.sh rules    "$WORK/rules"    >/dev/null

# ⚠️ A fourth tenant, IDLE rather than frozen, and the negative case needs it.
# "Dropping the guard over-matches" is only demonstrable against a tenant that is
# quiet and healthy. Three busy tenants and one frozen one cannot show it: the
# guarded and guardless expressions return the same single series and the check
# passes vacuously, which is exactly what the first run of this script reported.
python3 - "$WORK/profiles" <<'PYIDLE'
import json, pathlib, sys
d = json.load(open(pathlib.Path(sys.argv[1]) / "driven.json"))
d.update({"model_name": "sim-llama-3-8b-idle", "arrival_rate_rps": 0.001})
json.dump(d, open(pathlib.Path(sys.argv[1]) / "idle.json", "w"))
print("  staged an IDLE tenant (0.001 rps) as the negative case")
PYIDLE

# The shipped rule carries for: 5m, which is correct and unwatchable in a spike.
# Evaluate a 30s-`for:` twin ALONGSIDE it, under a different alertname, so the
# shipped rule is still loaded and still parsed -- only the patience changes.
python3 - "$WORK/rules/llm-rules.yaml" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
i = s.index("- alert: LLMMetricsStale")
j = s.index("- alert: ", i + 10)
twin = (s[i:j].replace("LLMMetricsStale", "LLMMetricsStaleFast")
                .replace("for: 5m", "for: 30s")
                .replace("[10m]", "[1m]"))
open(p, "w").write(s[:j] + twin + s[j:])
print("  staged LLMMetricsStaleFast ([1m] window, for: 30s) beside the shipped rule")
print("  ⚠️ BOTH numbers are scaled, not just the `for:`. The rate() window is the")
print("     other half of the latency and the reason this spike exists.")
PY

cat > "$WORK/prometheus.yml" <<EOF
global:
  scrape_interval: 5s
  evaluation_interval: 5s
rule_files:
  - /etc/prometheus/rules/*.yaml
scrape_configs:
  - job_name: llm-sim
    static_configs:
      - targets: ["sim-steady:9401", "sim-saturated:9401", "sim-driven:9401", "sim-idle:9401"]
EOF

echo "== bringing up four simulators and one Prometheus =="
docker network create "$NET" >/dev/null 2>&1 || true
for t in steady saturated driven idle; do
  docker run -d --name "sim-$t" --network "$NET" \
    -v "$SIM:/opt/llm-sim/llm_sim.py:ro" -v "$WORK/profiles:/etc/llm-sim:ro" \
    -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.12-slim python3 /opt/llm-sim/llm_sim.py \
      --profile "/etc/llm-sim/$t.json" --poll-seconds 5 >/dev/null
done
docker run -d --name prom-spike --network "$NET" -p "${PROM_PORT}:9090" \
  -v "$WORK/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$WORK/rules:/etc/prometheus/rules:ro" \
  prom/prometheus:v3.7.3 --config.file=/etc/prometheus/prometheus.yml >/dev/null

# ⚠️ POLL, never single-shot (rule 5). A budget in SECONDS, not attempts.
printf '  waiting for four targets up'
deadline=$((SECONDS + 120))
n_up="$(nq 'up{job="llm-sim"} == 1')"
while [ "$n_up" != "4" ]; do
  [ $SECONDS -lt $deadline ] || { echo; echo "  targets never came up"; exit 1; }
  printf '.'; sleep 3; n_up="$(nq 'up{job="llm-sim"} == 1')"
done
echo " ok"

echo
echo "== EXPECTATIONS, written before the freeze (rule 6) =="
cat <<'EOF'
  1. before the freeze, the stale expression matches NOTHING for any tenant
  2. an IDLE tenant must not match either -- the running > 0 guard is what
     separates "wedged" from "quiet", and it is the reason the rule has two halves
  3. after the freeze:  up{driven} stays 1        (the target is healthy)
  4.                    LLMMetricsAbsent silent   (absence cannot see this)
  5.                    rate(generation_tokens) 0 (the counter is frozen)
  6.                    num_requests_running > 0  (the engine still claims work)
  7.                    LLMMetricsStaleFast fires for the DRIVEN tenant ONLY
EOF

DRIVEN='model_name="sim-llama-3-8b-driven"'
STALE='vllm:num_requests_running > 0 and rate(vllm:generation_tokens_total[1m]) == 0'
echo
echo "== before the freeze =="
sleep 20
n_stale="$(nq "$STALE")"
check "$([ "$n_stale" = "0" ] && echo 1 || echo 0)" \
  "the stale expression matches nothing while everything is healthy" \
  "$n_stale series"

# ⚠️ THE DRILL HAS TO RAISE THE ARRIVAL RATE BEFORE IT FREEZES, and finding that
# out is what this run cost. llm-driven ships at 0.4 rps, where mean concurrency
# is about two and the population hits ZERO regularly. Freeze it on one of those
# moments and num_requests_running is 0, so the detector's `running > 0` guard
# excludes the tenant -- correctly, by the rule's own logic, because a tenant with
# nothing in flight is idle rather than wedged. The drill then produces no
# detectable state and reports "the alert did not fire", which is a true statement
# about a state that was never created. It is a COIN FLIP at the shipped rate:
# an earlier run of this same script passed with 6 requests held.
#
# W6 already says the KV drill must set the arrival rate. W3 needs the same
# sentence for the same reason, and the prompt does not carry it.
echo
echo "== raising the driven tenant to 1.8 rps BEFORE freezing =="
python3 - "$WORK/profiles/driven.json" <<'PYRATE'
import json, sys
d = json.load(open(sys.argv[1]))
d["arrival_rate_rps"] = 1.8          # a merge: every other key untouched
json.dump(d, open(sys.argv[1], "w"))
print("  arrival_rate_rps 0.4 -> 1.8 (the shipped rate cannot hold a population)")
PYRATE

# ⚠️ And then WAIT for the population, rather than assuming it. This is the
# precondition the drill has to check before it injects anything.
printf '  waiting for a non-zero request population'
deadline=$((SECONDS + 180))
n_run="$(nq "vllm:num_requests_running{$DRIVEN} > 0")"
while [ "$n_run" = "0" ]; do
  [ $SECONDS -lt $deadline ] || { echo; echo "  population never established"; exit 1; }
  printf '.'; sleep 5; n_run="$(nq "vllm:num_requests_running{$DRIVEN} > 0")"
done
echo " ok"

echo
echo "== freezing the driven tenant through its profile file =="
python3 - "$WORK/profiles/driven.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("faults", {})["freeze"] = True     # a MERGE, never a whole-object rewrite
json.dump(d, open(p, "w"))
print(f"  faults.freeze = true on {d['model_name']} (other keys untouched)")
PY

printf '  waiting for the freeze to reach the exposition'
deadline=$((SECONDS + 120))
n_f="$(nq 'llmsim_fault_active{fault="freeze"}')"
while [ "$n_f" = "0" ]; do
  [ $SECONDS -lt $deadline ] || { echo; echo "  freeze never landed"; exit 1; }
  printf '.'; sleep 3; n_f="$(nq 'llmsim_fault_active{fault="freeze"}')"
done
echo " ok"

echo
echo "== after the freeze =="
n_up="$(nq 'up{job="llm-sim"} == 1')"
n_abs="$(nq 'ALERTS{alertname="LLMMetricsAbsent"}')"
n_run="$(nq "vllm:num_requests_running{$DRIVEN} > 0")"
check "$([ "$n_up" = "4" ] && echo 1 || echo 0)" \
  "the target stays UP -- Kubernetes and absence alerting both see health" \
  "$n_up/4 targets up"
check "$([ "$n_abs" = "0" ] && echo 1 || echo 0)" \
  "LLMMetricsAbsent stays silent -- absent() cannot see a stale-but-up tenant"
check "$([ "$n_run" -ge 1 ] && echo 1 || echo 0)" \
  "num_requests_running stays > 0 -- the engine still claims work" \
  "$(q "vllm:num_requests_running{$DRIVEN}" | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r[0]["value"][1] if r else "none")') running"

# ⚠️ THE MEASUREMENT THIS SPIKE EXISTS FOR. Time from freeze to firing is NOT the
# `for:` -- rate() over a window only reaches zero once the WHOLE window contains
# a flat counter, so the window drains first and the `for:` starts after it.
echo
echo "  measuring freeze -> firing, against a [1m] window and a 30s \`for:\`"
froze_at=$SECONDS
window_zero_at=""
printf '    '
deadline=$((SECONDS + 300))
while :; do
  n_rate="$(nq "rate(vllm:generation_tokens_total{$DRIVEN}[1m]) == 0")"
  n_fire="$(nq 'ALERTS{alertname="LLMMetricsStaleFast",alertstate="firing"}')"
  if [ -z "$window_zero_at" ] && [ "$n_rate" != "0" ]; then
    window_zero_at=$((SECONDS - froze_at))
    printf ' [rate hit 0 at %ss] ' "$window_zero_at"
  fi
  [ "$n_fire" != "0" ] && break
  [ $SECONDS -lt $deadline ] && { printf '.'; sleep 5; continue; }
  echo " (never fired)"; break
done
fired_at=$((SECONDS - froze_at))
echo
echo "    rate() reached zero   : ${window_zero_at:-never}s after the freeze  (a [1m] window)"
echo "    alert reached firing  : ${fired_at}s after the freeze  (+ a 30s \`for:\`)"
echo "    ⚠️ SHIPPED VALUES ARE [10m] AND for: 5m, so the real drill waits the"
echo "       window AND the for:, one after the other, not the for: alone."

fired_driven="$(nq "ALERTS{alertname=\"LLMMetricsStaleFast\",alertstate=\"firing\",$DRIVEN}")"
fired_total="$(nq 'ALERTS{alertname="LLMMetricsStaleFast",alertstate="firing"}')"
check "$([ "$fired_driven" = "1" ] && echo 1 || echo 0)" \
  "LLMMetricsStale FIRES against a real frozen simulator, not a written series"
check "$([ "$fired_total" = "1" ] && echo 1 || echo 0)" \
  "and it fires for the DRIVEN tenant ONLY -- the fixtures are unaffected (rule 1)" \
  "$fired_total firing in total"

echo
echo "== the negative case: does an IDLE tenant trip it? (rule 18) =="
echo "  dropping the running > 0 guard and re-asking, against the same live data:"
GUARDLESS='rate(vllm:generation_tokens_total[1m]) == 0'
guardless_n="$(nq "$GUARDLESS")"
guarded_n="$(nq "$STALE")"
echo "    with the guard : $guarded_n series"
echo "    without it     : $guardless_n series"
check "$([ "$guardless_n" -gt "$guarded_n" ] && echo 1 || echo 0)" \
  "the guard is doing real work -- without it the IDLE tenant matches too" \
  "guardless $guardless_n (frozen + idle) vs guarded $guarded_n (frozen only)"

echo
echo "== restoring (a drill must leave the rig as it found it) =="
python3 - "$WORK/profiles/driven.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["faults"]["freeze"] = False
json.dump(d, open(sys.argv[1], "w"))
PY
printf '  waiting for the thaw'
deadline=$((SECONDS + 120))
n_f="$(nq 'llmsim_fault_active{fault="freeze"}')"
while [ "$n_f" != "0" ]; do
  [ $SECONDS -lt $deadline ] || { echo " (never thawed)"; FAILURES=$((FAILURES+1)); break; }
  printf '.'; sleep 3; n_f="$(nq 'llmsim_fault_active{fault="freeze"}')"
done
echo " ok"
sleep 15
n_stale="$(nq "$STALE")"
check "$([ "$n_stale" = "0" ] && echo 1 || echo 0)" \
  "after the thaw the expression is clean again -- the drill is repeatable"

echo
[ "$FAILURES" -eq 0 ] && echo "all questions answered" || echo "FAILURES: $FAILURES"
exit "$FAILURES"
