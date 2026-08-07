## Phase 2 — the experiment

**2.1 Open the branch.** `spike/<subject>`, from `main`. Seed a `spike/README.md`
survives-list entry for each artefact as it is created, so `task spike-routing` stays
green as you go rather than being reconciled at the end.

**2.2 Cheapest empirical version first.** A question settleable offline in seconds is not
settled on a cluster in minutes. This repo has been wrong about that: a question priced
at a day was answered in twenty minutes by running one container.

**2.3 RUN IT. This skill executes experiments.** That is the whole point of the phase.
Run the scripts, drive the harnesses, capture the output.

⚠️ **RUN IT MORE THAN ONCE whenever the result could vary** — anything touching timing,
concurrency, scheduling, a network, or a random seed. Default to three runs.

⚠️ **IF THE RUNS DISAGREE, THE DISAGREEMENT IS THE FINDING.** Report the spread. Do not
average it, do not pick the representative one, and do not write a single number into the
prompt. This is not hypothetical: the fault-injection spike found the driven tenant held
**6 requests on one run and 0 on the next, minutes apart**, and that instability was
worth more than either number. A skill that had run it once would have recorded a
confident figure that was right half the time.

⚠️ **A SURPRISING FIRST RESULT IS A QUESTION ABOUT THE INSTRUMENT.** Suspect the
instrument before the world. Re-run it, read the harness, and confirm the tool is
measuring what you think before recording anything. On 2026-08-07 three surprising
results in one session were all broken tooling: a draft check reporting 29 findings
against 12 correct scripts, a `720s` bound derived from a truncated `grep`, and two false
gaps from a bad regex. Every one would have been recorded as a finding by a loop that
trusted its own output.

**2.4 Record provenance beside every number**: the exact command, how many runs, and the
raw output. A number a reader cannot re-run is a number they must take on trust, which is
the failure this repo exists to prevent.

**2.5 Never write a number that was not produced by a run in this session.** Not from
memory, not from the diff, not by arithmetic over other numbers.
