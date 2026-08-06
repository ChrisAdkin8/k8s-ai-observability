# How this code is developed

This repository is a GPU free test bench for AI observability. The simulator is the
instrument, and **verification is the product**. That distinction sets the method: the work
is not finished when something runs, it is finished when there is a check that would go red
if it stopped being true.

Everything here is built the same way: a written specification first, an adversarial review
of that specification, a throwaway spike to settle whatever the review could not, a revision
of the specification with what the spike found, and only then the implementation that lands
on `main`.

The specification is called a **prompt file**. It is the unit of work here, and it is the
artefact that gets the most scrutiny, because a defect in a prompt file becomes a defect in
everything written from it.

## The loop

| Stage | What happens | Where it lives |
|---|---|---|
| 1. Cut the prompt | The work is specified in full before any code is written | `prompt-<subject>.md` in the repo root |
| 2. Critical review | Every claim, citation and number in the prompt is checked against the tree | same file, revised in place |
| 3. Spike | A time boxed experiment on a dedicated branch settles the empirical questions | `spike/<subject>` branch |
| 4. Fold findings back | What the spike learned rewrites the prompt, including anything it proved wrong | same file, revised again |
| 5. Implement | The revised prompt drives the real change, on its own branch | feature branch, landed by PR |

A prompt is not considered good enough to build from until stages 2, 3 and 4 have happened.
The measure of a good prompt is not that it reads well. It is that an implementer following
it does not discover, three hours in, that a load bearing assumption in it was wrong.

## Stage 1: cutting the prompt

Prompt files are produced with **Claude Opus 5 at the `xhigh` reasoning effort setting**, and
each one is written in a **fresh session with a clean context window**.

The fresh session is deliberate rather than incidental. A model that has just spent a long
session implementing something carries that work as context, and reviewing or respecifying
inside the same window anchors on decisions already made. The repository has seen this
concretely: `CLAUDE.md`'s review discipline records that same author re-review has
demonstrated anchoring, which is also why landing through a pull request is preferred, so
that non-author eyes see the change.

Every freshly cut prompt follows the **house style** set out in `CLAUDE.md` under "Prompt
files", which owns the definitive list. In summary, a prompt carries:

- numbered **W items**, one per piece of work;
- a **Background of verified facts**, each with a `file:line` citation and the date it was
  read, so a stale fact can be spotted rather than inherited;
- an **effort table**, with the standing caveat that the ordering is firmer than the numbers
  and that the largest line should be re derived before anyone plans around it;
- **Non goals**, which are as load bearing as the goals;
- **acceptance criteria written before the work**, not after it;
- instruments priced as code plus 25 to 35 percent verification, because the selftest is
  reliably where these estimates overrun.

Prompts are written **one work item ahead**. Implementing the current item is what makes the
next item's prompt honest, because the current item is where the assumptions get tested.

Prompt files are currently gitignored. `CLAUDE.md` carries the open question about whether
that should remain so, and owns it; it is not restated here.

## Stage 2: the critical review

The prompt is then reviewed critically and thoroughly, against the tree rather than against
itself. In practice this round is looking for four things:

1. **Citations that do not say what the prompt claims they say.** Every `file:line` is opened
   and read. Line numbers drift, and a confident citation to the wrong line is worse than no
   citation.
2. **Numbers that were inherited rather than derived.** A figure copied from another document
   is a fork waiting to disagree with its source.
3. **Assumptions presented as facts.** Anything the prompt asserts about behaviour that has
   not been observed is marked as an assumption or moved into the spike.
4. **Costs the prompt has not counted.** A change that touches five files should say so, and
   a check that will go red as a consequence should be expected rather than discovered.

One round is the standard. `CLAUDE.md` states why: after a single adversarial round the
remaining risk is empirical, such as a timing, a default, or a command's exact syntax, and a
desk cannot settle those. The first hours of implementation can, which is what the spike is
for.

## Stage 3: the spike

**A spike is a short, time boxed, throwaway experiment whose only purpose is to answer
questions that reading cannot.** It is named after the woodworking sense: you drive something
through the material to find out what is on the other side, and you do not expect to keep it.

What defines a spike here:

- **It runs on its own branch**, never on `main`, and it is not expected to merge.
- **It is throwaway by design.** The output is knowledge, not code. Anything from a spike that
  turns out to be worth keeping is rewritten properly as part of the real implementation, in
  its proper place in the tree.
- **It is bounded before it starts.** A spike answers a listed set of questions and stops. If
  it turns into implementation, it has stopped being a spike.
- **It prefers the cheapest empirical version of each question.** Where a question can be
  settled offline in seconds, it is not settled on a cluster in minutes. This repository has
  been wrong about that at least once: a question priced at a day was answered in twenty
  minutes by running one container.
- **It produces evidence, not opinions.** A spike result is a measured number or an observed
  behaviour, ideally reproducible by rerunning a script.

The spike also gives the prompt's assertions their first real test. A check that has never
failed is a guess, so spike work drives its own assertions to failure deliberately before
trusting them, exactly as the rest of the repository does.

## Stage 4: folding the findings back

Whatever the spike found rewrites the prompt, and this includes findings that contradict it.
A prompt that survives its spike unchanged is unusual and slightly suspicious. Typical
outcomes:

- a design is confirmed, and the prompt gains the measured numbers that confirm it;
- a design is refuted, and the prompt gains the corrected approach plus the evidence;
- an estimate moves, and the effort table records the new figure;
- a claim in another document turns out to be wrong, and correcting it becomes part of the
  work the prompt specifies.

Only after this revision is the prompt considered good enough to develop from.

## Stage 5: implementation

The implementation runs from the revised prompt on its own branch, in commits of one logical
change each, with the subject stating the change and the body carrying the reasoning. The
commit log is part of the documentation here, not an afterthought.

`task preflight` is the gate before anything lands: it runs every check that needs no
cluster, no cloud and no Docker, and it exists because a meaningful share of this
repository's early commits were corrections of work already on `main`, most of which those
checks would have caught.

Landing by pull request is preferred, for the non author review reason given above. A pull
request also draws an automated review from CodeRabbit, which is the practical source of non
author eyes on a repository with one author.

## Four disciplines that run through every stage

These are not stages. They apply at every scale, from a single assertion to a whole prompt,
and each one is stated as a numbered rule in `CLAUDE.md`, which owns them.

### The expectation is written before the observation

At the smallest scale this is rule 6: the expected value of a check goes in its comment
before the check is run, because a threshold chosen after seeing the data is not a test. At
the largest scale it is the same idea, which is why a prompt's acceptance criteria are
written before the work rather than after it. A drill's expected result, a check's bound and
a work item's definition of done are the same discipline at three sizes.

### Break it before you trust it

Rule 18: an assertion that only ever passes is not an assertion. Before a new check is
trusted, whatever it watches is broken deliberately and the check is confirmed to go red,
then repaired. This is not a formality here. The simulator's selftests pin bugs that were
reintroduced on purpose to prove the selftest fails on them, and CI drives the chart's render
time assertions and the negative case of `helm test` to failure by design.

Rules 5 and 6 are the two specific cases of it: poll rather than single shot, because a check
that races its producer passes for the wrong reason, and write the expected value first.

### Reference, do not restate

A number or a fact stated in two places is a fork waiting to disagree. It is stated once, in
the file that owns it, and everything else points there. `check-doc-claims.py` exists because
prose kept forking from code anyway, and it mechanises the cases that recur, such as counts
in documentation and identifiers quoted in more than one page.

This page is written to that rule. It names rules by number and points at `CLAUDE.md` rather
than reproducing them, so that a rule changing does not leave a stale copy here.

### Open items are marked where they live, and struck rather than deleted

There is no TODO file, and adding one would be a mistake. Open work carries a marker in the
file that owns it, and `task outstanding` lists them by matching a curated set of phrases
kept in `Taskfile.yml`. The tool matches phrasing rather than the warning glyph, so an item
worded in new language is silently missed, and matching an existing phrasing or extending the
list is part of marking one.

When an item is finished it is struck through and annotated with what happened and when. It
is not deleted, because the reasoning outlives the action, and the record of why something
was open is usually worth more later than the item itself was.

## A note on em dashes

Several checks and conventions in this repository remove em dashes from particular files, and
`check-doc-claims.py` enforces it so that the rule does not have to be applied by hand.

**This is purely the repository owner's preference for conventional dashes.** It carries no
other meaning, and nothing should be inferred from it beyond a house style choice about
punctuation, in the same category as a preference for a particular quote style or line
length. It is mechanised only because it had to be applied by hand several times before it
was, which is the same reason any other recurring correction here ends up in a check.

This page follows that preference throughout.

## Why the method is shaped like this

Two mottoes in `CLAUDE.md` explain most of it.

**When a reading is surprising, suspect the instrument before the world.** A new check, a new
drill or a new script is the least tested thing in the room. When it disagrees with something
that has been running for months, the new thing is the likely defect, and the first response
is to doubt it rather than to publish what it said.

**An invented number presented as a modelled one is the exact failure this repository exists
to prevent.** This is the reason for nearly every convention above: the citations with dates
in a prompt's Background, the requirement that a spike produce measured values rather than
estimates, the effort tables that say which figures are derived and which are guesses, and
the checks that compare prose against the code it describes. A test bench that cannot tell
its own invented numbers from its modelled ones has no business grading anyone else's.
