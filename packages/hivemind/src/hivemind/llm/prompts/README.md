# hivemind.llm.prompts

Plain-markdown prompt assets and the loader that reads and assembles them: the templates the
Queen, a Warden, a Drone and the Attendant's tie-break step turn into an awake episode's `system`
text, before `hivemind.memory.assemble` (a separate roadmap step) appends pins, hot state and the
triggering event as labelled, delimited sections.

## Portability rules (codingrules 8.6, "Prompts are portable")

- Every `<name>.md` file is plain markdown: headings, bullets and paragraphs only. No vendor
  formatting (no `<system>`/`<human>`/`<assistant>`-style tags, no chat-template tokens) and no
  model id, anywhere in the file.
- A prompt never hard-codes the exact JSON shape or tool-call syntax its one decision must be
  returned in. The ladder (`llm/ladders/structured.py`, `llm/ladders/tools.py`) supplies the exact
  schema or tool protocol and appends its own fenced-output preamble on top of the rendered
  prompt; a prompt only names, in plain language, the fields its decision conceptually needs.
- A provider-specific tweak never goes into a shared prompt file. A wire-format detail goes into
  that provider's own `mapping.py`; prompt text that genuinely needs to differ per provider goes
  into a per-provider overlay file under `llm/prompts/overlays/<provider>/<name>.md`. Overlays are
  not built this step — `load_prompt` always returns this package's own shared file for now; a
  later step wires overlay lookup in ahead of the shared file, on top of this same loader.

## Labelling rule (codingrules 15, content reaching a prompt is data, not instructions)

`render()` appends every section the caller supplies after the prompt body, each wrapped in a
plain-text delimiter naming its kind: `<<<pins>>> ... <<<end pins>>>`, and likewise for
`hot_state`, `retrieved`, `user` and `event`. Sections always come out in that fixed order
(codingrules 8.9, "stable prefix first") regardless of the order the caller's mapping iterates in,
so provider prompt caching sees the same prefix call after call, and a model reading the assembled
text can always tell durable state and retrieved content apart from an instruction to it.

## Public API (roadmap step 3.9)

- **`PromptName`** (`hivemind.llm.prompts.loader`): one member per shipped `.md` file
  (`QUEEN_SYSTEM`, `DECOMPOSE_GOAL`, `WARDEN_SYSTEM`, `DRONE_SYSTEM`, `ATTENDANT_TRIAGE`).
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
uv run --frozen pytest packages/hivemind/tests/unit/llm/prompts
```

A snapshot test compares each prompt's rendered output, for a fixed sample of sections, against a
committed `.txt` file under `tests/unit/llm/prompts/snapshots/`; see that directory's README for
how to regenerate them after an intentional prompt change.
