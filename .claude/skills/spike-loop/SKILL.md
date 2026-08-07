---
name: spike-loop
description: Drive one prompt file through the loop in docs/development-method.md — review, spike, run the experiment, fold the findings back, land. Phase-aware, so it does the next step rather than all of them.
argument-hint: [prompt-file]
disable-model-invocation: true
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
---

Drive `$ARGUMENTS` through the loop in `docs/development-method.md`.

If `$ARGUMENTS` is empty or the file does not exist, say so, list `prompts/*.md`, and
stop. Do not guess which prompt was meant.

## Work out which phase you are in, and do only that one

Read the tree, not the user's description of it. `<subject>` is the prompt's filename
without `prompt-` or `.md`.

| State | Phase |
|---|---|
| No `spike/<subject>` branch exists | **1 — review** |
| Branch exists, no commits on it | **2 — the experiment** |
| Branch has commits; the prompt cites none of its artefacts | **3 — fold back** |
| Prompt cites them; `task spike-routing` is red | **4 — route** |
| All green | **5 — land** |

Say which phase you are in and why, before doing anything. Then do that phase and stop.
The loop is re-entered by invoking this skill again, not by running on.

---

## Phase 1 — review

**1.1 The deterministic half first, because it is free.** Run `task citations` and
`task doc-claims`. Both resolve `path:line` references and prose-versus-code claims
mechanically. Anything they catch needs no subagent and no judgement.

**1.2 Facts — unbriefed, one subagent per section.** Invoke `/review-prompt` on the
file, or spawn `prompt-fact-checker` per `##` section plus the headingless preamble.

⚠️ **Give each one the file path and its section name and nothing else.** No persona, no
hint about what you believe, no pointer to the files you think matter. "Does line 332 say
this" is objective; a lens cannot improve it and can only bias it.

**1.3 Judgement — lensed, and COLD.** Run `task prompt-review -- $ARGUMENTS` once per
lens, each a separate `claude -p` process. Four lenses, derived from what has actually
found defects in this repo rather than from job titles:

| Lens | The question it asks |
|--|--|
| **operator** | What does this look like at 3am, and how long until I know? |
| **next implementer** | Can I follow this without redoing the digging behind it? |
| **adversary** | What if it runs twice, gets cancelled, or the network blips? |
| **stranger** | Does this claim transfer off this rig, or does it only hold here? |

⚠️ **A separate process is the point, not an optimisation.** A subagent is briefed by the
author, which is the exact channel a cold session exists to close (`Taskfile.yml`, the
`prompt-review` comment). Never run these in-session.

⚠️ **"Nothing found" is a valid result and must be said so in the lens prompt.** A
reviewer asked to find gaps will find some whether or not any exist. Four lenses each
obliged to produce something is that failure multiplied by four, and the grading lands on
a human, serially, while the running is parallel and cheap.

**1.4 Report, do not fix.** One table, `WRONG` and `MISSING` first, then `UNSOURCED`,
then `MOVED`, then the lens findings with their lens named. The author decides what a
finding means. Print `task prompt-review` for any lens that did not run.

---

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

---

## Phase 3 — fold back

**3.1 Read the whole branch diff**, not the commit messages. `git diff main...spike/<subject>`.
The findings are in the ⚠️ comments and the measured values, and a commit subject rarely
carries either.

**3.2 Draft the findings table in the shape the prompt already uses** — *question /
answer / W-item*, one row per finding, each routed to the item it changes. Match the
existing section's format exactly; `prompts/prompt-fault-injection.md` is the reference.

**3.3 Mark what contradicts the prompt.** A design confirmed with its numbers, a design
refuted with the evidence, an estimate moved, or a claim elsewhere found wrong so
correcting it joins the work. **A prompt that survives its spike unchanged is slightly
suspicious.**

**3.4 It is a DRAFT.** Present it for correction. You read the diff; the author ran the
experiment and knows which findings are load-bearing.

**3.5 Re-run `task citations` after inserting.** The fold-back adds citations, and a
freshly-written stale pointer is the most common defect in this class — one review round
found four in a single brief.

---

## Phase 4 — route

Run `task spike-routing`. Every tracked `spike/` artefact needs a stated heir in
`spike/README.md`: where it goes, or why it stays.

⚠️ **"No obvious heir" is a legitimate entry and often the honest one.** Say so
explicitly rather than inventing a destination. Acceptance evidence that nothing else
reproduces is kept on purpose, and writing a fake retirement plan hides that decision.

---

## Phase 5 — land

`task preflight`, then one logical change per commit with the reasoning in the body, then
a PR whose body is assembled from those bodies. Prefer landing by PR: it draws a
non-author review, which has found real defects in every prompt this loop has produced —
including two introduced by the previous phase an hour earlier.

State plainly in the PR body anything the run could not verify.

---

## What this skill must not do

- **Absorb the cold pass.** It invokes `task prompt-review`; it never does that reading
  in-session.
- **Grade findings.** It reports them, with their lens, and says which it cannot judge.
- **Invent a number.** See 2.4 and 2.5.
- **Launder one run into a fact.** See 2.3.
- **Decide what to measure.** It runs the experiment; the question is the author's, and
  the framing is where a spike's value actually sits.
- **Grep to decide anything a script can decide.** Use `task citations`,
  `task spike-routing`, `task doc-claims`, `task preflight`. An ad-hoc search that
  truncates still exits 0 and still looks like an answer.
