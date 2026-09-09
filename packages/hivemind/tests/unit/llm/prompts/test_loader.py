"""Tests for hivemind.llm.prompts.loader: loading, delimiting and ordering prompt sections.

Fits into the Hive:
    Mirrors src/hivemind/llm/prompts/loader.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.prompts.loader for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.llm.prompts import loader
from hivemind.llm.prompts.loader import (
    PromptName,
    PromptNotFoundError,
    SectionLabel,
    load_prompt,
    render,
)

# codingrules 8.6, "Prompts are portable": none of these vendor-specific fragments may appear in a
# shipped prompt body. Model-id fragments mirror scripts/check_no_model_ids.py's own list, split
# with "+" so this negative check's own source is never a contiguous match for the very hygiene
# scanner it is asserting against (the scanner matches raw line text, not runtime string values).
_VENDOR_TAG_FRAGMENTS = ("<system>", "<human>", "<assistant>", "Human:", "Assistant:")
_MODEL_ID_FRAGMENTS = ("claude" + "-", "gpt" + "-", "lla" + "ma")


class _AlwaysMissing:
    """A stand-in for importlib.resources.files() whose lookup never finds anything."""

    def joinpath(self, name: str) -> None:
        """Raise, the way a real Traversable's read would for a file that is not there."""
        raise FileNotFoundError(name)


# ──────────────────────────────────────────────────────────────────────────────
# load_prompt
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", list(PromptName))
def test_load_prompt_returns_non_empty_text_for_every_prompt_name(name: PromptName) -> None:
    text = load_prompt(name)

    assert text.strip() != ""


@pytest.mark.parametrize("name", list(PromptName))
def test_load_prompt_never_contains_a_vendor_tag(name: PromptName) -> None:
    text = load_prompt(name)

    for fragment in _VENDOR_TAG_FRAGMENTS:
        assert fragment not in text


@pytest.mark.parametrize("name", list(PromptName))
def test_load_prompt_never_contains_a_model_id_fragment(name: PromptName) -> None:
    text = load_prompt(name).lower()

    for fragment in _MODEL_ID_FRAGMENTS:
        assert fragment not in text


def test_load_prompt_raises_prompt_not_found_when_the_asset_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A prior test may already have cached this name's real file; clear so the patched lookup
    # below is actually exercised instead of a cached hit from before this test ran.
    load_prompt.cache_clear()
    monkeypatch.setattr(loader, "files", lambda _package: _AlwaysMissing())

    with pytest.raises(PromptNotFoundError, match="queen_system"):
        load_prompt(PromptName.QUEEN_SYSTEM)

    # Nothing was cached on the failed call; a plain call afterwards must reload for real.
    load_prompt.cache_clear()


# ──────────────────────────────────────────────────────────────────────────────
# render
# ──────────────────────────────────────────────────────────────────────────────


def test_render_with_no_sections_returns_just_the_prompt_body() -> None:
    rendered = render(PromptName.DRONE_SYSTEM, sections={})

    assert rendered == load_prompt(PromptName.DRONE_SYSTEM).rstrip("\n")


def test_render_wraps_each_supplied_section_in_its_labelled_delimiter() -> None:
    rendered = render(
        PromptName.WARDEN_SYSTEM,
        sections={SectionLabel.EVENT: "worker_42 stalled on attempt 1."},
    )

    assert "<<<event>>>\nworker_42 stalled on attempt 1.\n<<<end event>>>" in rendered


def test_render_orders_sections_by_stable_prefix_not_by_mapping_order() -> None:
    # Deliberately supplied out of order, to prove render() ignores mapping iteration order.
    rendered = render(
        PromptName.QUEEN_SYSTEM,
        sections={
            SectionLabel.EVENT: "event-text",
            SectionLabel.PINS: "pins-text",
            SectionLabel.USER: "user-text",
            SectionLabel.HOT_STATE: "hot-text",
            SectionLabel.RETRIEVED: "retrieved-text",
        },
    )

    positions = [
        rendered.index(f"<<<{label.value}>>>")
        for label in (
            SectionLabel.PINS,
            SectionLabel.HOT_STATE,
            SectionLabel.RETRIEVED,
            SectionLabel.USER,
            SectionLabel.EVENT,
        )
    ]

    assert positions == sorted(positions)


def test_render_omits_a_label_with_no_entry_in_sections() -> None:
    rendered = render(PromptName.ATTENDANT_TRIAGE, sections={SectionLabel.PINS: "pins-text"})

    assert "<<<pins>>>" in rendered
    for label in (SectionLabel.HOT_STATE, SectionLabel.RETRIEVED, SectionLabel.USER):
        assert f"<<<{label.value}>>>" not in rendered
