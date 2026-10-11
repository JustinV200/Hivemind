"""Tests for hivemind.cli.llm.local: `hive llm models|pull|keep-alive` and the `ollama` health note.

Every test drives the typer application through `CliRunner`, handing the commands a
`FakeOllamaManager` (or "not Ollama") through `LocalCliDeps` on the typer context, so no server,
port or network is involved. The generic `/models` path runs the real `OpenAICompatProvider` over
`httpx.MockTransport`.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/local.py (codingrules section 3); a separate module from
    test_llm.py because it covers a different command family.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.local for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from builders.cli import fake_manifest
from builders.llm import make_provider_config, make_registry_deps
from typer.testing import CliRunner, Result

import hivemind.cli.llm.local as local_module
from hivemind.cli.app import app
from hivemind.cli.llm.local import LocalCliDeps
from hivemind.cli.llm.rows import provider_rows
from hivemind.llm import FakeLLMProvider, ProviderRegistry
from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.providers.ollama import (
    FakeOllamaManager,
    OllamaModel,
    PullProgress,
    opener,
)
from hivemind.llm.providers.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from hivemind.manifest import load_manifest
from waggle.clock import FakeClock

runner = CliRunner()

_LOCAL_BASE_URL = "http://127.0.0.1:9/v1"  # Loopback on a port nothing serves; never contacted.
# The named binding gives the provider the default model the registry needs to build it.
_LOCAL_PROVIDER = (
    '\n[llm.providers.local]\nkind = "openai_compat"\nbase_url = "{url}"\n'
    '\n[llm.slots.local_worker]\nprovider = "local"\nmodel = "local-small"\n'
)


def _manifest(tmp_path: Path) -> Path:
    """Write the fake manifest plus one OpenAI-compatible provider named `local`."""
    path = fake_manifest(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8") + _LOCAL_PROVIDER.format(url=_LOCAL_BASE_URL),
        encoding="utf-8",
    )
    return path


def _invoke(manager: FakeOllamaManager | None, *args: str) -> Result:
    """Run `hive llm <args>` with `manager` as the one server (None: not an Ollama server)."""
    deps = LocalCliDeps(open_manager=opener(manager))
    return runner.invoke(app, ["llm", *args], obj=deps)


def _models_registry(ids: list[str]) -> ProviderRegistry:
    """Build a registry whose `local` provider is a real OpenAICompatProvider over a mock server."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": name} for name in ids]})

    config = OpenAICompatConfig(
        base_url=_LOCAL_BASE_URL,
        model="local-small",
        timeout_s=5.0,
        capabilities=ProviderCapabilities.full(),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=config.base_url)
    provider = OpenAICompatProvider("local", config, http, FakeClock())
    providers = {"local": make_provider_config(kind="openai_compat", base_url=_LOCAL_BASE_URL)}
    deps = make_registry_deps(factories={"openai_compat": lambda n, c, k, clock: provider})
    return ProviderRegistry(providers, [], offline=False, deps=deps)


# ──────────────────────────────────────────────────────────────────────────────
# hive llm models
# ──────────────────────────────────────────────────────────────────────────────


def test_models_lists_an_ollama_servers_installed_models(tmp_path: Path) -> None:
    fake = FakeOllamaManager(models=(OllamaModel(name="local-small", size=2_000_000_000),))

    result = _invoke(fake, "models", "--provider", "local", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 0
    assert "local-small" in result.output
    assert "2.0 GB" in result.output
    assert fake.closed is True


def test_models_json_carries_the_row_fields(tmp_path: Path) -> None:
    fake = FakeOllamaManager(models=(OllamaModel(name="local-small", size=5),))

    result = _invoke(
        fake, "models", "--provider", "local", "--manifest", str(_manifest(tmp_path)), "--json"
    )

    assert json.loads(result.output) == [
        {"name": "local-small", "size_bytes": 5, "parameters": "", "quantization": ""}
    ]


def test_models_falls_back_to_the_generic_models_probe_for_a_non_ollama_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _models_registry(["served-b", "served-a"])
    monkeypatch.setattr(local_module, "build_registry", lambda manifest, environ, clock: registry)

    result = _invoke(None, "models", "--provider", "local", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert [line.split()[0] for line in lines[1:]] == ["served-a", "served-b"]


def test_models_says_so_when_the_provider_cannot_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = {"local": make_provider_config(kind="openai_compat", base_url=_LOCAL_BASE_URL)}
    deps = make_registry_deps(
        factories={"openai_compat": lambda n, c, k, clock: FakeLLMProvider(name="local")}
    )
    registry = ProviderRegistry(providers, [], offline=False, deps=deps)
    monkeypatch.setattr(local_module, "build_registry", lambda manifest, environ, clock: registry)

    result = _invoke(None, "models", "--provider", "local", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 2
    assert "cannot list models" in result.output


def test_models_exits_1_with_the_typed_error_when_the_server_is_down(tmp_path: Path) -> None:
    fake = FakeOllamaManager(failure=ProviderUnavailableError("local", "down"))

    result = _invoke(fake, "models", "--provider", "local", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 1
    assert "unavailable" in result.output
    assert fake.closed is True


def test_models_rejects_an_unknown_provider(tmp_path: Path) -> None:
    result = _invoke(None, "models", "--provider", "nope", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 2
    assert "unknown provider" in result.output


def test_models_rejects_a_provider_with_no_server_to_reach(tmp_path: Path) -> None:
    # `fake` is an in-process kind with no base_url, so there is no server to ask.
    result = _invoke(None, "models", "--provider", "fake", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 2
    assert "not a model server" in result.output


# ──────────────────────────────────────────────────────────────────────────────
# hive llm pull
# ──────────────────────────────────────────────────────────────────────────────


def test_pull_prints_status_changes_and_ten_percent_steps_then_installs(tmp_path: Path) -> None:
    script = (
        PullProgress("pulling manifest", None, None, None),
        PullProgress("pulling abc", "abc", 100, 3),
        PullProgress("pulling abc", "abc", 100, 7),  # Same 10% bucket as before: not printed.
        PullProgress("pulling abc", "abc", 100, 55),
        PullProgress("pulling abc", "abc", 100, 100),
    )
    fake = FakeOllamaManager(pull_script=script)

    result = _invoke(
        fake, "pull", "local-small", "--provider", "local", "--manifest", str(_manifest(tmp_path))
    )

    assert result.exit_code == 0
    assert result.output.strip().splitlines() == [
        "pulling manifest",
        "pulling abc  3%",
        "pulling abc  55%",
        "pulling abc  100%",
        "success",
    ]
    assert fake.pulled == ["local-small"]
    assert fake.closed is True


def test_pull_refuses_a_server_that_is_not_ollama(tmp_path: Path) -> None:
    result = _invoke(
        None, "pull", "m", "--provider", "local", "--manifest", str(_manifest(tmp_path))
    )

    assert result.exit_code == 2
    assert "only Ollama can pull models" in result.output


def test_pull_exits_1_when_the_server_fails_mid_pull(tmp_path: Path) -> None:
    fake = FakeOllamaManager(failure=ProviderUnavailableError("local", "went away"))

    result = _invoke(
        fake, "pull", "m", "--provider", "local", "--manifest", str(_manifest(tmp_path))
    )

    assert result.exit_code == 1
    assert fake.closed is True


def test_pull_exits_1_when_the_stream_ends_without_success(tmp_path: Path) -> None:
    class _Silent(FakeOllamaManager):
        async def pull(self, model: str) -> AsyncIterator[PullProgress]:
            yield PullProgress("pulling manifest", None, None, None)

    result = _invoke(
        _Silent(), "pull", "m", "--provider", "local", "--manifest", str(_manifest(tmp_path))
    )

    assert result.exit_code == 1
    assert "ended without success" in result.output


# ──────────────────────────────────────────────────────────────────────────────
# hive llm keep-alive
# ──────────────────────────────────────────────────────────────────────────────


def test_keep_alive_asks_the_server_to_hold_the_model(tmp_path: Path) -> None:
    fake = FakeOllamaManager()

    result = _invoke(
        fake,
        "keep-alive", "local-small",
        "--provider", "local",
        "--seconds", "300",
        "--manifest", str(_manifest(tmp_path)),
    )  # fmt: skip

    assert result.exit_code == 0
    assert fake.keep_alive_calls == [("local-small", 300)]
    assert "keep-alive set to 300s" in result.output
    assert fake.closed is True


def test_keep_alive_requires_seconds_and_rejects_below_minus_one(tmp_path: Path) -> None:
    manifest = str(_manifest(tmp_path))

    missing = _invoke(
        FakeOllamaManager(), "keep-alive", "m", "--provider", "local", "--manifest", manifest
    )
    too_low = _invoke(
        FakeOllamaManager(),
        "keep-alive",
        "m",
        "--provider",
        "local",
        "--seconds",
        "-2",
        "--manifest",
        manifest,
    )

    assert missing.exit_code == 2
    assert too_low.exit_code == 2


def test_keep_alive_refuses_a_server_that_is_not_ollama(tmp_path: Path) -> None:
    result = _invoke(
        None,
        "keep-alive", "m",
        "--provider", "local",
        "--seconds", "5",
        "--manifest", str(_manifest(tmp_path)),
    )  # fmt: skip

    assert result.exit_code == 2
    assert "only Ollama can hold models in memory" in result.output


# ──────────────────────────────────────────────────────────────────────────────
# hive llm providers: the `ollama` note on the health column
# ──────────────────────────────────────────────────────────────────────────────


async def test_provider_rows_say_ollama_when_the_server_answers_the_native_probe(
    tmp_path: Path,
) -> None:
    registry = _models_registry([])
    manifest = load_manifest(_manifest(tmp_path))

    rows = await provider_rows(manifest, registry, opener(FakeOllamaManager()))

    local = next(row for row in rows if row.name == "local")
    assert "(ollama " in local.health


async def test_provider_rows_add_nothing_for_a_server_that_is_not_ollama(tmp_path: Path) -> None:
    registry = _models_registry([])
    manifest = load_manifest(_manifest(tmp_path))

    rows = await provider_rows(manifest, registry, opener(None))

    local = next(row for row in rows if row.name == "local")
    assert "ollama" not in local.health


async def test_provider_rows_never_probe_a_provider_offline_mode_refuses(tmp_path: Path) -> None:
    manifest = load_manifest(_manifest(tmp_path))
    providers = {
        "local": make_provider_config(kind="openai_compat", base_url="http://remote.test/v1")
    }
    registry = ProviderRegistry(providers, [], offline=True, deps=make_registry_deps())

    async def forbidden(provider: str, base_url: str, timeout_s: float) -> None:
        raise AssertionError("a refused provider must not be probed")

    rows = await provider_rows(manifest, registry, forbidden)

    assert [row.health for row in rows if row.name == "local"] == ["refused: offline"]


def test_providers_command_uses_the_injected_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _models_registry([])
    monkeypatch.setattr(
        "hivemind.cli.llm.commands.build_registry", lambda manifest, environ, clock: registry
    )

    result = _invoke(FakeOllamaManager(), "providers", "--manifest", str(_manifest(tmp_path)))

    assert result.exit_code == 0
    assert "(ollama " in result.output
