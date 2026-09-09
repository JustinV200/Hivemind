# Prompt snapshots

One `<name>.txt` file per `PromptName`, each the exact text of
`render(name, sections=SAMPLE_SECTIONS)` for the fixed `SAMPLE_SECTIONS` defined in
`../test_prompt_snapshots.py`. `test_prompt_snapshots.py` re-renders every prompt on each run and
compares it against its file here byte for byte (codingrules 14.3, "every LLM-facing prompt has a
snapshot test on the rendered prompt").

## Regenerating after an intentional prompt change

A snapshot going stale means either a `.md` file under `hivemind/llm/prompts/` changed on purpose,
or `render()`'s own formatting changed on purpose. After making that change, rewrite every
snapshot from the repository root:

```bash
uv run --frozen python - <<'PY'
from pathlib import Path

from hivemind.llm.prompts import PromptName, SectionLabel, render

SAMPLE_SECTIONS = {
    SectionLabel.PINS: "Never exceed the manifest spend cap.",
    SectionLabel.HOT_STATE: "Active tasks: 2. Open Alarms: 0. Pending questions: 1.",
    SectionLabel.RETRIEVED: "Prior Handoff: goal in progress, no blockers recorded.",
    SectionLabel.USER: "Ship the phase 3 CLI docs by Friday.",
    SectionLabel.EVENT: "Alarm WORKER_STALLED raised for worker_42, attempt 1.",
}

for name in PromptName:
    text = render(name, sections=SAMPLE_SECTIONS)
    Path(f"packages/hivemind/tests/unit/llm/prompts/snapshots/{name.value}.txt").write_text(
        text, encoding="utf-8"
    )
PY
```

Then run `uv run --frozen pytest packages/hivemind/tests/unit/llm/prompts` and review the diff
with `git diff` — a snapshot diff is itself a prompt-behaviour change worth calling out in the PR.

**Keep `SAMPLE_SECTIONS` above identical to the one in `test_prompt_snapshots.py`.** They are
duplicated on purpose, so this file stands alone as a runnable snippet, but the two must never
drift apart.
