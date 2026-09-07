# HiveMind Subagents

> How work on this repo splits between models so building all fourteen roadmap phases doesn't
> burn tokens re-deriving context at every step. Read `.claude/roadmap.md` and
> `.claude/codingrules.md` first; this document only says *who* reads and writes what, not what the
> rules are.

## Roles

- **Fable (orchestrator).** Runs the main loop. Decomposes a phase into steps, dispatches each step
  to a Sonnet subagent, reviews what comes back, ticks roadmap boxes, and decides anything that
  needs judgement (ADRs, scope changes, escalations). Fable never writes implementation code, and
  never reads a full source file wholesale — diffs and subagent reports are the interface.
- **Sonnet — implementer.** One subagent per roadmap step (or per file within a step, see
  "Parallelizing" below). Writes the code, the docstrings, the comments and the tests for that step,
  runs `ruff` / `mypy` / `pytest` / the hygiene scripts on its own output before returning, and
  reports back a short structured summary — never a full echo of the files it wrote.
- **Sonnet — tester.** A separate, short-lived subagent spun up to independently re-run the
  Definition of Done checklist (codingrules §17) for a step, or a phase's exit criteria. Independent
  because an implementer grading its own homework is the exact failure mode Capping (codingrules
  §8.12) exists to prevent in the product itself; the same principle applies to building it.

This mirrors the system being built: Fable delegates and never executes, like the Queen; Sonnet
subagents do the work and report up, like Wardens and Workers. Treat that as a mnemonic, not a
design constraint — this file is about Claude Code's own workflow, not HiveMind's runtime, and
nothing here belongs in the bee vocabulary.

## Unit of delegation: one roadmap step

The roadmap already scopes every step to "small enough for one PR" (codingrules §16: under 400
changed lines, one logical change). That is also the right size for one subagent call — small
enough that a Sonnet subagent can hold the whole step in context, self-check it, and return before
drifting off-task.

- One step → one Agent call, by default.
- A step whose file list has no internal imports between the files (e.g. step 1.3's message
  catalogue: ten independent files under `waggle/messages/`) → one Agent call per file, dispatched
  in parallel in a single message.
- Never batch multiple roadmap steps into a single subagent call. If a subagent's output would
  cover more than one checkbox, it took on scope it shouldn't have — split the dispatch next time.

## Context discipline

This is the actual token budget. Rules, not suggestions:

1. **Never paste `roadmap.md` or `codingrules.md` into a subagent prompt.** Both files are huge
   (~1900 and ~1750 lines). Quote only the step's own bullet text verbatim, plus the specific
   coding-rules section numbers that govern it (always §5 size limits, §6 naming, §7 comments; plus
   whichever of §8.1-8.16 covers the subsystem). The subagent has the same repo checked out and can
   open those sections itself if it needs the full wording.
2. **Give exact file paths from codingrules §3**, not descriptions. "Create
   `packages/waggle/src/waggle/envelope.py`" beats "add the envelope module somewhere in waggle." A
   subagent that has to search for where something belongs is spending tokens Fable already spent
   once reading the layout table.
3. **State the step's exit condition explicitly**: the roadmap step's own text, plus Definition of
   Done §17 by reference, so the subagent knows when to stop instead of gold-plating.
4. **Subagents report compactly**: files touched with line counts, tests added, lint/type/test
   status, and any deviation from the step as written. Not file contents — Fable can diff.
5. **Fable reviews diffs, not files.** `git diff --stat` first, then targeted hunks for anything
   that touches a Protocol, a layer boundary, or a security-relevant section (§8.6, §8.7, §8.15,
   §15). A step that only adds a straightforward implementation behind an existing Protocol gets a
   stat-level glance, not a full read.
6. **Trust the hygiene scripts and CI as the primary correctness signal**, not a manual re-read.
   `scripts/check_sizes.py`, `check_no_model_ids.py`, `check_no_kind_branches.py`,
   `check_no_transcripts.py`, `ruff`, `mypy --strict` and `lint-imports` exist precisely so nobody
   has to eyeball every file for these things. Green means green.

## Parallelizing steps

The roadmap already states its own dependency graph: the phase table's `Depends on` column, notes
like "Phases 6, 7 and 10 can proceed in parallel once phase 5 lands," and each step's own numbering
within a phase. Use it directly instead of re-deriving it:

- Steps within a phase are sequential unless the roadmap says otherwise (a later step usually
  consumes an earlier one, e.g. 3.20's Queen kernel builds on 3.19's Warden).
- Independent files *within* one step, and independent phases once their shared dependency has
  landed, dispatch as multiple Agent calls in a single message — see "one Agent call per file"
  above.
- Two subagents that could touch the same file, even across different steps, never run in
  parallel. Reach for worktree isolation only when parallel subagents genuinely write to
  overlapping directories and would otherwise race; it costs setup time and disk, so the default
  for the common case (disjoint files) is no isolation.
- Prefer running implementer dispatches in the background so Fable can review the previous step's
  diff, or draft the next dispatch, instead of idling on the response.
- A large fan-out spanning many independent steps at once (e.g. phases 6, 7 and 10 in parallel) is
  a natural fit for the Workflow tool's deterministic pipeline/parallel primitives — but only when
  the user has actually opted into multi-agent orchestration for this session. Otherwise, dispatch
  the same set of steps as ordinary parallel Agent calls.

## Testing subagents

Spin one up:

- At the end of every step, to independently confirm codingrules §17 (Definition of Done) rather
  than accepting the implementer's self-report at face value.
- At a phase's exit criteria, to run the phase's full acceptance scenario end to end and report
  pass/fail per bullet in that phase's "Exit criteria" list.

A tester subagent's prompt is the checklist item plus the exact command to run
(`uv run pytest -m "not integration and not e2e and not live_llm and not local_llm"`,
`uv run ruff check`, `uv run mypy --strict`, `uv run lint-imports`) and nothing else. Its report is
pass/fail per item plus the failing output only — never a full green log pasted back.

## Escalation

A subagent that hits an ambiguity the roadmap doesn't resolve, or a decision that needs an ADR,
stops and returns the question rather than guessing and writing code on top of the guess. Redoing a
step is cheaper, token-wise, than reviewing and unwinding a step built on a wrong assumption.

## What Fable never delegates

- Ticking the roadmap checkbox and deciding a step is actually done.
- Accepting an ADR (`docs/adr/`) — these record the hard-to-reverse decisions; a Sonnet subagent
  may draft one when a step calls for it, but Fable is the one who accepts it.
- Trivial edits: a typo, a one-line doc fix, a file already open in the current diff. For anything
  that small, dispatching a subagent costs more tokens than it saves — Fable edits it directly.
