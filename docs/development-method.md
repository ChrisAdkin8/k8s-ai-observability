# How this code is developed

The simulator here is the instrument, and **verification is the product**. Work is finished
not when something runs, but when a check exists that would go red if it stopped being true.
That standard covers how work is specified too, because **an invented number presented as a
modelled one is the exact failure this repo exists to prevent**. The specification is a
**prompt file**, and it gets more scrutiny than the code written from it: a defect there
becomes a defect in everything downstream.

## The loop

| Stage | What happens | Where |
|---|---|---|
| 1. Cut the prompt | The work is specified before any code is written | `prompts/prompt-<subject>.md` |
| 2. Review | Claims and numbers checked against the tree; assumptions challenged | same file, revised |
| 3. Spike | A throwaway experiment settles what reading cannot | a branch, not merged |
| 4. Fold back | What the spike found rewrites the prompt | same file, revised again |
| 5. Implement | The revised prompt drives the change | feature branch, landed by PR |

⚠️ **Scale the loop to the change.** A typo, a log line or a version bump does not get a
prompt file. If you could describe the diff in one sentence, skip the plan. The full loop
earns its cost when the approach is uncertain, when several files move together, or when the
change touches something that fails silently.

Stages 1 and 2 are long standing. Stage 3 was added on 2026-08-06 with one application so
far, so it is a convention being set rather than described.

## Stage 1: cut the prompt

**Start by being interviewed, not by writing.** Ask the model to interview you about
implementation, edge cases and tradeoffs first. It raises what you had not considered, which
is the same defect class stage 2 catches later and more expensively.

Prompts are cut with **Claude Opus 5 at `xhigh` effort** (as of 2026-08-06), in a **fresh
session**, for two independent reasons:

- **Anchoring.** A window that just implemented something defends its own decisions.
  `CLAUDE.md` records that same author re-review has demonstrated this.
- **Context.** Performance degrades as a context window fills. A session that has been
  exploring for an hour is a worse author than one that has not.

`CLAUDE.md` under "Prompt files" owns the house style.

Prompts are written **one item ahead**, because implementing the current item is what makes
the next one's prompt honest.

They live in `prompts/` and are **tracked**, since context worth having is context you commit.
That also puts them inside `check-doc-claims.py`, which scans tracked markdown, so a prompt
is now held to the same prose against code checks as the docs. Its first run on them found
three citing a dashboard id this repo has never had.

## Stage 2: review

Four things to find:

1. **Citations that do not say what the prompt claims.** Line numbers drift, and a confident
   citation to the wrong line is worse than none.
2. **Numbers inherited rather than derived.** A figure copied from another document is a fork
   waiting to disagree with its source.
3. **Assumptions presented as facts.** Anything asserted about behaviour nobody has observed
   is marked as an assumption or moved into the spike.
4. **Costs not counted.** A change touching five files should say so, and a check that will
   go red as a consequence should be expected rather than discovered.

⚠️ **Grade the findings, and expect some to be noise.** A reviewer asked to find gaps will
find some whether or not any exist, because that is the instruction. Chasing all of them
produces defensive over-engineering. A finding counts if it affects correctness or a stated
requirement; saying which do not is part of the review.

⚠️ And when a new check disagrees with old code, **suspect the instrument before the world.**
The check is the least tested thing present.

### Who reviews what

The halves want different instruments, and the difference is who supplies the criteria.

| Reviewing | Instrument | Why |
|---|---|---|
| Points 1 and 2: citations, numbers | **Subagents**, one per section | Objective and parallel. Supplying criteria cannot bias "does line 332 say this" |
| Points 3 and 4: assumptions, costs | **A cold session**, outside this one | Here the author's framing is the suspect, and a subagent is briefed by the author |

The rule generalises. **Reviewing a diff against stated criteria is a subagent's job**, since
the implementing session gets the gaps directly. **Reviewing the specification is not**,
because nothing checks a plan except judgement, and the author cannot supply that without
contaminating it.

The cold read is a test rather than a cost. The prompt is meant to be the context: its
Background carries every fact with a citation and a date, so a reader who never saw the
original exploration can still check it. **If a cold reader cannot work through the prompt
without redoing the digging behind it, the prompt is not finished.** A subagent hides that,
because the parent still holds the context papering over the gap.

**One round, then build.** After a single adversarial round the remaining risk is empirical:
a timing, a default, a command's exact syntax. A desk cannot settle those. The spike can.

## Stage 3: the spike

**A spike is a short, throwaway experiment whose only purpose is to answer questions that
reading cannot.**

- **Its own branch**, not intended to merge.
- **Throwaway by design.** The output is knowledge. Anything worth keeping is rewritten
  properly in its real place in the tree, as part of the implementation.
- **Bounded before it starts.** If it turns into implementation, it has stopped being a spike.
- **Cheapest empirical version first.** A question settleable offline in seconds is not
  settled on a cluster in minutes. This repo has been wrong about that: a question priced at
  a day was answered in twenty minutes by running one container.
- **Evidence, not opinions**, reproducible by rerunning a script, and driven to failure
  deliberately before being trusted.

## Stage 4: fold the findings back

Whatever the spike found rewrites the prompt, including whatever it contradicted: a design
confirmed with its numbers, a design refuted with the evidence, an estimate moved, or a claim
elsewhere found wrong, so correcting it joins the work. A prompt that survives its spike
unchanged is slightly suspicious.

## Stage 5: implement

`task preflight` is the gate before anything lands. It runs every check needing no cluster,
no cloud and no Docker, and exists because a meaningful share of this repo's early commits
were corrections of work already on `main`. Landing by pull request is preferred, which also
draws a CodeRabbit review, the practical source of non author eyes on a one author repo.

⚠️ **The arrow points both ways.** Implementation that contradicts the prompt is information,
not insubordination: correct the prompt. If the same problem needs correcting twice, the
context is polluted with failed approaches, and a fresh session with a better prompt beats
continuing.

## The disciplines underneath

Each is a rule in `CLAUDE.md`, which owns the detail.

| | The rule, in one line |
|---|---|
| **6** | The expectation is written before the observation. A threshold chosen after seeing the data is not a test, and acceptance criteria written first are the same rule at a larger size |
| **18** | Break it before you trust it. An assertion that only ever passes is not an assertion, so whatever a check watches gets broken deliberately and confirmed red first |
| **11** | One logical change per commit. The subject states the change and the body carries the reasoning, because the commit log is documentation here rather than an afterthought |

## What the harness does for you

| Stage | Mechanism |
|---|---|
| Every stage, one step per invocation | `/spike-loop prompts/prompt-<subject>.md`, which reads the tree to decide the phase and then does only that one. `.claude/skills/spike-loop/phase.py` owns the decision, because working it out by eye is how four confident wrong answers happened in one session |
| The mechanical half of stage 2 | `.claude/agents/prompt-fact-checker.md`, fanned out by `/review-prompt` |
| The cold half of stage 2 | `task prompt-review -- prompts/prompt-<subject>.md`, a separate `claude -p` |
| Standing context | `CLAUDE.md`, loaded every session, kept short so its rules are not lost in noise |

## A note on em dashes

One check keeps em dashes out of the pages a stranger reads first, listed in `EM_DASH_FREE`
in `check-doc-claims.py`. **This is purely the repository owner's preference for conventional
dashes**, mechanised only because it had to be applied by hand three times first, which is
how everything here ends up in a check.
