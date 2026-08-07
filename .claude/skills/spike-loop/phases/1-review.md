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
