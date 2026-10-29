# hivemind.llm.prompts

Plain-markdown prompt assets and the loader that reads and assembles them: the templates the
Queen, a Warden, a Drone, a Forager, a Scout and the Attendant's tie-break step turn into an awake
episode's `system` text, before `hivemind.memory.assemble` (a separate roadmap step) appends pins,
hot state and the triggering event as labelled, delimited sections.

## Portability rules (codingrules 8.6, "Prompts are portable")

- Every `<name>.md` file is plain markdown: headings, bullets and paragraphs only. No vendor
  formatting (no `<system>`/`<human>`/`<assistant>`-style tags, no chat-template tokens) and no
  model id, anywhere in the file.
- A prompt never hard-codes the exact JSON shape or tool-call syntax its one decision must be
  returned in. The ladder (`llm/ladders/structured.py`, `llm/ladders/tools.py`) supplies the exact
  schema or tool protocol and appends its own fenced-output preamble on top of the rendered
  prompt; a prompt only names, in plain language, the fields its decision conceptually needs.
- A provider-specific tweak never goes into a shared prompt file. A wire-format detail goes into
  that provider's own `mapping.py`; prompt text that genuinely needs to differ for one kind of
  model goes into an overlay directory (below), never into the shared file.

## Overlays (roadmap step 8.4, ADR-0044)

An overlay is a directory `overlays/<overlay>/` of markdown files that a weak or unusual model
needs on top of the shared prompts. It is named by the manifest, not by the provider's kind or the
model's id (two servers behind the one OpenAI-compatible adapter can need different overlays):
`[llm.providers.<name>] prompt_overlay = "small_local"`. `ProviderRegistry.provider` wraps the
provider it builds in `OverlayingProvider` (`hivemind.llm.registry.overlay`) when the row names
one, so the text is applied once, at the provider door, to every request that provider sees; no
call site knows an overlay exists, and an unknown overlay name fails when the provider is built.

- `_system.md` is appended, after a blank line, to every system prompt.
- `<prompt_name>.md` (a `PromptName` value, e.g. `drone_system.md`) is added only to a system
  prompt that starts with that prompt's shipped body, directly after the body and before the
  labelled state sections, so the static prefix stays contiguous for provider caching.
- Every overlay file opens with an HTML comment saying which model shape it is for and why
  (codingrules 8.6); the loader strips it, so the model never reads it. The same portability rules
  as the shared prompts apply: plain markdown, no vendor tags, no model ids.
- `load_overlay(name)` reads one through `importlib.resources` into a frozen `Overlay`;
  `apply_overlay(overlay, request)` is the pure rewrite. A stray file that is neither `_system.md`
  nor a `PromptName` is refused as a typo.

Shipped: `small_local` (terse answers, one tool call per turn, JSON only when asked, no preamble)
for small instruction-tuned models served locally, with a reminder for `drone_system` and for
`decompose_goal`. Whether an overlay helps is measured, not assumed: run `hive llm eval` with and
without it and compare the reports.

## Labelling rule (codingrules 15, content reaching a prompt is data, not instructions)

`render()` appends every section the caller supplies after the prompt body, each wrapped in a
plain-text delimiter naming its kind: `<<<pins>>> ... <<<end pins>>>`, and likewise for
`hot_state`, `retrieved`, `user` and `event`. Sections always come out in that fixed order
(codingrules 8.9, "stable prefix first") regardless of the order the caller's mapping iterates in,
so provider prompt caching sees the same prefix call after call, and a model reading the assembled
text can always tell durable state and retrieved content apart from an instruction to it.

## Public API (roadmap step 3.9)

- **`PromptName`** (`hivemind.llm.prompts.loader`): one member per shipped `.md` file
  (`QUEEN_SYSTEM`, `DECOMPOSE_GOAL`, `WARDEN_SYSTEM`, `DRONE_SYSTEM`, `ATTENDANT_TRIAGE`,
  `COMPACT_RECORDS` -- roadmap step 4.3, `hivemind.memory.compact`'s one structured call;
  `JUDGE_REVIEW` -- roadmap step 4.10, `hivemind.wardens.judge.ModelJudgeReviewer`'s one
  structured call, built from a tier's rubric and the `JudgeRequest` alone, no proposer transcript
  or hot state; `FORAGER_SYSTEM` -- roadmap step 6.9, `hivemind.workers.roles.forager.Forager`'s
  bounded see/act loop; `SCOUT_SYSTEM` -- roadmap step 6.10, `hivemind.workers.roles.scout.Scout`'s
  strictly budgeted recon loop; `RIPEN_NECTAR` -- roadmap step 7.5,
  `hivemind.honey_store.ripening.summarise`'s one structured call on the `RIPENER` slot, the
  deposit shown only as untrusted EVENT data, labelled for its own content whatever its current
  label; `JUDGE_CLEARANCE` -- ADR-0038, `hivemind.honey_store.lowering.judge.ModelClearanceJudge`'s
  one structured call on the `JUDGE` slot, whether one deposit's title and text may carry a lower
  label, the deposit shown only as untrusted RETRIEVED data and never the proposer's reason or any
  id; `TAINT_REVIEW` -- roadmap step 10.6d, `hivemind.memory.taint.judge.ModelTaintJudge`'s one
  structured call: the taint rubric, and the tainted item's own text, kind, setter and reason
  alone, never the bee that wrote it; `PATROL` -- roadmap step 11.10,
  `hivemind.wardens.awake.patrol.decide_patrol`'s one structured call on the `WARDEN` slot: a
  watching Warden's hourly review of an idle Cell, one of four outcomes, the observations shown
  only as a fenced block of untrusted facts inside the event; `NURSE_REVIEW` -- ADR-0061,
  `hivemind.wardens.nurse.ModelNurseReviewer`'s one structured call on an eligible `JUDGE` link:
  a fresh-context review of an attempt that passed its machine checks, the Warden-built evidence
  shown only as untrusted RETRIEVED data with each touched file in its own block, never the
  Worker's transcript, Handoff or own account; `HELP_ANSWER` -- ADR-0069,
  `hivemind.entrance.help.HelpAnswerer`'s one plain-text call on the cheapest chat binding: an
  answer to a person's question from the Help screen's own pages, the passages shown as numbered
  RETRIEVED data and the question as USER data, no tools, "I don't know" when they do not cover
  it).
- **`SectionLabel`**: `PINS`, `HOT_STATE`, `RETRIEVED`, `USER`, `EVENT` — the five kinds of durable
  state a section may carry.
- **`load_prompt(name)`**: read one prompt's markdown body, via `importlib.resources` so it works
  from an installed wheel, never a filesystem path relative to this module.
- **`render(name, *, sections)`**: the prompt body plus every supplied section, delimited and in
  stable-prefix order.
- **`PromptNotFoundError`**: raised by `load_prompt` when a `PromptName` has no matching shipped
  file. Rooted at `hivemind.common.errors.NotFoundError` for now — `hivemind.llm.errors` is being
  built in a parallel dispatch landing this same phase and will become this error's true root once
  it exists.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm/prompts \
  packages/hivemind/tests/unit/llm/registry/test_overlay.py
```

A snapshot test compares each prompt's rendered output, for a fixed sample of sections, against a
committed `.txt` file under `tests/unit/llm/prompts/snapshots/`; see that directory's README for
how to regenerate them after an intentional prompt change.
