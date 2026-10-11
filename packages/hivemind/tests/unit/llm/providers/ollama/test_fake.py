"""Tests for hivemind.llm.providers.ollama.fake: the in-memory ModelServerManager.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/ollama/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.ollama.fake for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.providers.ollama import FakeOllamaManager, OllamaModel, PullProgress, opener


async def test_fake_pull_replays_the_script_then_succeeds_and_installs_the_model() -> None:
    script = (PullProgress("pulling x", "x", 10, 5),)
    fake = FakeOllamaManager(pull_script=script)

    lines = [line async for line in fake.pull("local-small")]

    assert [line.status for line in lines] == ["pulling x", "success"]
    assert [model.name for model in await fake.list_models()] == ["local-small"]
    assert fake.pulled == ["local-small"]


async def test_fake_pull_of_an_installed_model_does_not_list_it_twice() -> None:
    fake = FakeOllamaManager(models=(OllamaModel(name="local-small"),))

    _ = [line async for line in fake.pull("local-small")]

    assert len(await fake.list_models()) == 1


async def test_fake_keep_alive_is_recorded_and_shows_as_loaded_until_unloaded() -> None:
    fake = FakeOllamaManager()

    await fake.keep_alive("a", 60)
    await fake.keep_alive("b", 60)
    await fake.keep_alive("b", 0)

    assert fake.keep_alive_calls == [("a", 60), ("b", 60), ("b", 0)]
    assert [model.name for model in (await fake.health()).loaded] == ["a"]


async def test_fake_raises_its_scripted_failure_from_every_operation() -> None:
    fake = FakeOllamaManager(failure=ProviderUnavailableError("local", "down"))

    with pytest.raises(ProviderUnavailableError):
        await fake.list_models()
    with pytest.raises(ProviderUnavailableError):
        await fake.keep_alive("a", 1)
    with pytest.raises(ProviderUnavailableError):
        await fake.health()
    with pytest.raises(ProviderUnavailableError):
        async for _ in fake.pull("a"):
            pass


async def test_fake_aclose_is_recorded() -> None:
    fake = FakeOllamaManager()

    await fake.aclose()

    assert fake.closed is True


async def test_opener_hands_back_the_fake_or_none() -> None:
    fake = FakeOllamaManager()

    assert await opener(fake)("p", "http://x", 1.0) is fake
    assert await opener(None)("p", "http://x", 1.0) is None
