"""Snapshot-test every prompt's rendered output against a committed .txt file per PromptName.

Codingrules section 14.3: "Every LLM-facing prompt has a snapshot test on the rendered prompt."
This module is that test, parametrised once per `PromptName` over a single fixed sample of
sections shared by every prompt, so a change to a prompt body or to `render()`'s own formatting
shows up as an exact-text diff instead of a hand-rolled assertion per prompt.

Fits into the Hive:
    Exercises hivemind.llm.prompts.loader.render() against the committed files under
    ./snapshots/, one per hivemind.llm.prompts.loader.PromptName.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.prompts.loader for render(), the function under test.
    - snapshots/README.md for how to regenerate these files after an intentional prompt change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.llm.prompts import PromptName, SectionLabel, render

# A fixed, representative sample for each section kind, shared by every prompt's snapshot: a real
# assembled prompt would carry much more, but the snapshot only needs to prove render()'s own
# ordering and delimiting, not any one subsystem's real hot state. Kept identical, on purpose, to
# the copy embedded in snapshots/README.md's regeneration one-liner -- see that file.
SAMPLE_SECTIONS = {
    SectionLabel.PINS: "Never exceed the manifest spend cap.",
    SectionLabel.HOT_STATE: "Active tasks: 2. Open Alarms: 0. Pending questions: 1.",
    SectionLabel.RETRIEVED: "Prior Handoff: goal in progress, no blockers recorded.",
    SectionLabel.USER: "Ship the phase 3 CLI docs by Friday.",
    SectionLabel.EVENT: "Alarm WORKER_STALLED raised for worker_42, attempt 1.",
}

_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


@pytest.mark.parametrize("name", list(PromptName))
def test_render_matches_committed_snapshot(name: PromptName) -> None:
    rendered = render(name, sections=SAMPLE_SECTIONS)

    snapshot_path = _SNAPSHOT_DIR / f"{name.value}.txt"
    expected = snapshot_path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"{snapshot_path} is stale; see snapshots/README.md to regenerate it if this prompt "
        "change was intentional."
    )
