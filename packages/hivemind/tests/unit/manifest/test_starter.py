"""Test the starter Hive Manifest `hive init` writes: it validates on every path, or refuses.

Fits into the Hive:
    Unit tests for `hivemind.manifest.starter` (roadmap 14.2).

Key invariants:
    - None.

See Also:
    - hivemind.manifest.starter.render for what is under test.
"""

from __future__ import annotations

import tomllib

import pytest

from hivemind.manifest import HiveManifest, ManifestError
from hivemind.manifest.starter import (
    OLLAMA_BASE_URL,
    StarterChoice,
    StarterProvider,
    render_starter,
)
from waggle.clock import FakeClock

# A name that would break out of a TOML string if spliced in unquoted.
TRICKY_NAME = 'Bees "R" Us \\ hive'


@pytest.mark.parametrize(
    ("choice", "base_url", "offline"),
    [
        (StarterChoice(StarterProvider.ANTHROPIC, TRICKY_NAME), "", False),
        (StarterChoice(StarterProvider.OLLAMA, TRICKY_NAME, model="m1"), OLLAMA_BASE_URL, True),
        (
            StarterChoice(
                StarterProvider.OPENAI_COMPATIBLE,
                TRICKY_NAME,
                model="m1",
                embed_model="e1",
                base_url="https://models.example.net/v1",
            ),
            "https://models.example.net/v1",
            False,
        ),
    ],
)
def test_render_starter_validates_with_the_hive_stand_enabled(
    choice: StarterChoice, base_url: str, offline: bool
) -> None:
    clock = FakeClock()

    first = HiveManifest.model_validate(tomllib.loads(render_starter(choice, clock)))
    second = HiveManifest.model_validate(tomllib.loads(render_starter(choice, clock)))

    assert first.hive_stand.enabled
    assert first.hive.name == TRICKY_NAME
    assert first.hive.id != second.hive.id
    assert first.llm.offline is offline
    providers = list(first.llm.providers.values())
    assert providers[0].base_url == base_url
    # A local path binds every chat slot to the chosen model and the embedder to its own.
    if choice.model is not None:
        assert first.llm.slots["worker"].model == choice.model
        assert first.llm.slots["embedder"].model == (choice.embed_model or choice.model)


@pytest.mark.parametrize(
    "choice",
    [
        StarterChoice(StarterProvider.OLLAMA),
        StarterChoice(StarterProvider.OPENAI_COMPATIBLE, model="m1"),
        StarterChoice(StarterProvider.ANTHROPIC, model="m1"),
        StarterChoice(StarterProvider.OLLAMA, model="m" * 10_000),  # Past the schema's cap.
    ],
)
def test_render_starter_refuses_a_choice_it_cannot_honour(choice: StarterChoice) -> None:
    with pytest.raises(ManifestError):
        render_starter(choice, FakeClock())
