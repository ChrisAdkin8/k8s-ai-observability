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
