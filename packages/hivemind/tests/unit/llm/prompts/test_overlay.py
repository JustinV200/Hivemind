"""Tests for hivemind.llm.prompts.overlay: loading and applying a prompt overlay.

Fits into the Hive:
    Mirrors src/hivemind/llm/prompts/overlay.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.prompts.overlay for the module under test.
    - test_loader.py for the portability checks on the shared prompts that these repeat.
"""

from __future__ import annotations

from importlib.resources import files
from types import MappingProxyType

import pytest
from builders.llm import make_request

from hivemind.llm.prompts import (
    Overlay,
    OverlayNotFoundError,
    PromptName,
    SectionLabel,
    apply_overlay,
    load_overlay,
    load_prompt,
    render,
)
from hivemind.llm.prompts.overlay import available_overlays

# The same portability fragments test_loader.py forbids in a shared prompt body; split so this
# file's own source never matches the hygiene scanner it asserts against.
_VENDOR_TAG_FRAGMENTS = ("<system>", "<human>", "<assistant>", "Human:", "Assistant:")
_MODEL_ID_FRAGMENTS = ("claude" + "-", "gpt" + "-", "lla" + "ma")


def _overlay(system: str = "", **keyed: str) -> Overlay:
    """Build an in-memory overlay, keyed by PromptName value."""
    suffixes = {PromptName(name): text for name, text in keyed.items()}
    return Overlay("test", system, MappingProxyType(suffixes))


# ──────────────────────────────────────────────────────────────────────────────
# apply_overlay
# ──────────────────────────────────────────────────────────────────────────────


def test_apply_overlay_appends_the_system_suffix_to_every_system_prompt() -> None:
    request = make_request(system="Any system text.")

    result = apply_overlay(_overlay("Be terse."), request)

    assert result.system == "Any system text.\n\nBe terse."


def test_apply_overlay_leaves_a_request_without_a_system_prompt_untouched() -> None:
    request = make_request(system=None)

    assert apply_overlay(_overlay("Be terse."), request) is request


def test_apply_overlay_with_nothing_to_add_returns_the_same_request() -> None:
    request = make_request(system=load_prompt(PromptName.DRONE_SYSTEM))

    assert apply_overlay(_overlay(), request) is request


def test_apply_overlay_adds_a_keyed_suffix_only_to_the_matching_prompt() -> None:
    overlay = _overlay(drone_system="Drone extra.")
    drone = make_request(system=load_prompt(PromptName.DRONE_SYSTEM))
    queen = make_request(system=load_prompt(PromptName.QUEEN_SYSTEM))

    drone_result = apply_overlay(overlay, drone)
    queen_result = apply_overlay(overlay, queen)

    assert drone_result.system is not None
    assert drone_result.system.endswith("\n\nDrone extra.")
    assert queen_result is queen


def test_apply_overlay_places_the_keyed_suffix_right_after_the_body_before_sections() -> None:
    overlay = _overlay("Global.", drone_system="Drone extra.")
    system = render(PromptName.DRONE_SYSTEM, sections={SectionLabel.EVENT: "the task"})
    body = load_prompt(PromptName.DRONE_SYSTEM).rstrip()

    result = apply_overlay(overlay, make_request(system=system))

    assert (
        result.system
        == f"{body}\n\nDrone extra.\n\n<<<event>>>\nthe task\n<<<end event>>>\n\nGlobal."
    )


def test_apply_overlay_does_not_mutate_the_original_request() -> None:
    request = make_request(system="Text.")

    apply_overlay(_overlay("Extra."), request)

    assert request.system == "Text."


# ──────────────────────────────────────────────────────────────────────────────
# load_overlay
# ──────────────────────────────────────────────────────────────────────────────


def test_load_overlay_reads_the_shipped_small_local_overlay() -> None:
    overlay = load_overlay("small_local")

    assert overlay.name == "small_local"
    assert overlay.system_suffix != ""
    assert PromptName.DRONE_SYSTEM in overlay.prompt_suffixes


def test_load_overlay_strips_the_header_comment() -> None:
    overlay = load_overlay("small_local")

    texts = [overlay.system_suffix, *overlay.prompt_suffixes.values()]
    assert all("<!--" not in text and "-->" not in text for text in texts)


@pytest.mark.parametrize("name", ["nope", "../prompts", "Small_Local", "", "small_local/x"])
def test_load_overlay_refuses_an_unknown_or_unsafe_name(name: str) -> None:
    with pytest.raises(OverlayNotFoundError):
        load_overlay(name)


def test_overlay_not_found_error_lists_the_shipped_overlays() -> None:
    with pytest.raises(OverlayNotFoundError, match="small_local"):
        load_overlay("missing")


def test_available_overlays_includes_small_local() -> None:
    assert "small_local" in available_overlays()


# ──────────────────────────────────────────────────────────────────────────────
# Shipped overlay files: header and portability (codingrules 8.6)
# ──────────────────────────────────────────────────────────────────────────────


def _shipped_files() -> list[tuple[str, str]]:
    """Return (overlay/file, raw text) for every markdown file of every shipped overlay."""
    found: list[tuple[str, str]] = []
    for name in available_overlays():
        directory = files("hivemind.llm.prompts").joinpath("overlays", name)
        for entry in directory.iterdir():
            if entry.name.endswith(".md"):
                found.append((f"{name}/{entry.name}", entry.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize(("label", "text"), _shipped_files())
def test_every_shipped_overlay_file_opens_with_a_header_saying_why(label: str, text: str) -> None:
    assert text.lstrip().startswith("<!--"), label
    header = text.split("-->", 1)[0]
    assert "Why" in header, label


@pytest.mark.parametrize(("label", "text"), _shipped_files())
def test_every_shipped_overlay_file_is_portable(label: str, text: str) -> None:
    lowered = text.lower()

    for fragment in _VENDOR_TAG_FRAGMENTS:
        assert fragment not in text, label
    for fragment in _MODEL_ID_FRAGMENTS:
        assert fragment not in lowered, label


@pytest.mark.parametrize("name", available_overlays())
def test_every_shipped_overlay_loads_without_error(name: str) -> None:
    overlay = load_overlay(name)

    assert overlay.system_suffix.strip() != "" or overlay.prompt_suffixes
