---
name: review-prompt
description: Fact-check a prompt file's citations and numbers against the tree, using one prompt-fact-checker subagent per section. Covers the mechanical half of a prompt review only.
argument-hint: [prompt-file]
disable-model-invocation: true
allowed-tools: Read, Grep, Glob
---

Fact-check the prompt file at `$ARGUMENTS` against the current tree.

This is **half** of the review round described in `docs/development-method.md`. It
covers points 1 and 2, citations and numbers. It does not cover points 3 and 4,
assumptions stated as facts and costs the prompt has not counted, because those
are judgement and this session has the author's framing. Finish with the cold pass.

## Steps

1. If `$ARGUMENTS` is empty or the file does not exist, say so and stop. Do not
   guess which prompt was meant.

2. Read the prompt file and list its `##` sections. Report how many sections and
   roughly how many citations you are about to check, so the cost is visible
   before it is paid.

3. Spawn **one `prompt-fact-checker` subagent per section, all in a single
   message** so they run concurrently. Give each one the prompt file path and its
   section name, and nothing else. Do not tell it what you believe is correct, do
   not point it at the files you think matter, and do not pass on your own
   opinion of any section. Briefing it is how the author's framing leaks in.

4. Collect the findings into one table, ordered `WRONG` and `MISSING` first,
   then `UNSOURCED`, then `MOVED`. Stale line numbers are the common case and
   the least interesting; a wrong claim is the reason this runs at all.

5. **Report. Do not fix.** The author decides what a finding means. A citation
   that has moved may want the line updating, or may reveal that the section was
   written against code that has since changed, which is a different repair.

6. Print the cold pass command, filled in:

   ```
   task prompt-review -- $ARGUMENTS
   ```

   and state plainly that the review is not complete until it has been run,
   because nothing in this session can catch what the author never thought of.

## Notes

- Every finding is checkable. Give the reader the path and line so they can look
  rather than trust you.
- If a section has no citations at all, say so. A background section carrying no
  `file:line` references is itself a finding against the house style.
- Do not summarise the prompt, do not restate what it proposes, and do not praise
  it. The author wrote it and does not need it read back.
