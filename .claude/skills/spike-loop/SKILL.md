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

## 1. Ask which phase you are in. Do not work it out yourself.

```
python3 .claude/skills/spike-loop/phase.py $ARGUMENTS
```

It prints the phase, why, and the four observations behind it.

⚠️ **THIS IS THE ONE DECIDABLE STEP, SO CODE DECIDES IT.** Reading `git branch` and
grepping the prompt by eye is exactly the habit that produced four wrong answers in one
session on 2026-08-07, each of which exited 0 and looked like an answer. A phase read
wrongly sends the whole loop to the wrong step and nothing downstream notices.

## 2. Read that phase's file, and only that one

| Phase | Read |
|--|--|
| 1 — review | `.claude/skills/spike-loop/phases/1-review.md` |
| 2 — the experiment | `.claude/skills/spike-loop/phases/2-experiment.md` |
| 3 — fold back | `.claude/skills/spike-loop/phases/3-fold-back.md` |
| 4 — route | `.claude/skills/spike-loop/phases/4-route.md` |
| 5 — land | `.claude/skills/spike-loop/phases/5-land.md` |

Say which phase you are in and why before acting. Then do that phase and **stop**. The
loop is re-entered by invoking this skill again, not by running on — which is also why
only one phase is ever loaded.

## What this skill must not do

- **Absorb the cold pass.** It invokes `task prompt-review`; it never does that reading
  in-session. A subagent is briefed by the author, which is the exact channel a cold
  session exists to close.
- **Grade findings.** It reports them, with their lens, and says which it cannot judge.
- **Invent a number**, or write one that no run in this session produced.
- **Launder one run into a fact.** If repeated runs disagree, the disagreement is the
  finding.
- **Decide what to measure.** It runs the experiment; the question is the author's, and
  the framing is where a spike's value actually sits.
- **Grep to decide anything a script can decide.** `phase.py`, `task citations`,
  `task spike-routing`, `task doc-claims`, `task preflight`. An ad-hoc search that
  truncates still exits 0 and still looks like an answer.
