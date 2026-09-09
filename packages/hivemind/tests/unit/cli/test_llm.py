"""Tests for hivemind.cli.llm: `hive llm providers|slots|test`.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm.py (codingrules section 3: tests/unit mirrors src/ one-to-one).
    Drives the typer application through typer.testing.CliRunner, the same way an operator's
    shell would. A hand-written `[llm.providers.fake] kind = "fake"` manifest (`_write_fake_
    manifest`) keeps every test here fully offline: no real provider, no network, no API key
    (codingrules section 8.6's fake-provider convention, phase 3 brief section 2.7's "test the
    fake").

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm for the module under test.
    - hivemind.llm.fake for FakeLLMProvider, the adapter every test here runs against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders.llm import make_provider_config, make_registry_deps
from typer.testing import CliRunner

import hivemind.cli.llm as llm_module
from hivemind.cli.app import app
from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import Effort
from hivemind.llm import FakeLLMProvider, LLMError, LLMResponse, ProviderRegistry, text_response
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

runner = CliRunner()

# A hand-built manifest with one kind="fake" provider serving every ModelSlot: every test in this
# module loads it through --manifest, so no test ever needs a real provider or the network. The
# id fields are filled in by _write_fake_manifest, minted fresh from a FakeClock so they are
# well-formed ULIDs (Crockford base32, never I/L/O/U) rather than a hand-typed lookalike.
_FAKE_MANIFEST_TEMPLATE = """
[hive]
id = "{hive_id}"
node_id = "{node_id}"
name = "Fake Test Hive"

[llm.providers.fake]
kind = "fake"

[llm.slots.queen]
provider = "fake"
model = "test-model"

[llm.slots.judge]
provider = "fake"
model = "test-model"

[llm.slots.attendant]
provider = "fake"
model = "test-model"

[llm.slots.ripener]
provider = "fake"
model = "test-model"

[llm.slots.warden]
provider = "fake"
model = "test-model"

[llm.slots.worker]
provider = "fake"
model = "test-model"

[llm.slots.scaffolder]
provider = "fake"
model = "test-model"

[llm.slots.embedder]
provider = "fake"
model = "test-model"

[llm.slots.transcriber]
provider = "fake"
model = "test-model"

[forage.roles.drone]
cpu_cores = 0.5
memory_bytes = 268435456
token_rate_per_minute = 20000
"""


def _write_fake_manifest(tmp_path: Path) -> Path:
    """Render `_FAKE_MANIFEST_TEMPLATE` with fresh ids, write it to `tmp_path`, return its path."""
    clock = FakeClock()
    text = _FAKE_MANIFEST_TEMPLATE.format(hive_id=new_hive_id(clock), node_id=new_node_id(clock))
    path = tmp_path / "hive.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _scripted_registry(*responses: LLMResponse | LLMError) -> ProviderRegistry:
    """Build a ProviderRegistry whose 'fake' provider always returns the same pre-scripted one.

    Used to control `hive llm test`'s reply deterministically: the CLI's own `build_registry`
    would otherwise hand back a fresh, unscripted FakeLLMProvider every invocation.
    """
    provider = FakeLLMProvider(name="fake")
    provider.script(*responses)
    bindings = [
        SlotBinding(key="worker", provider="fake", model="test-model", effort=Effort.MEDIUM)
    ]
    providers = {"fake": make_provider_config(kind="fake")}
    deps = make_registry_deps(factories={"fake": lambda name, config, api_key, clock: provider})
    return ProviderRegistry(providers, bindings, offline=False, deps=deps)


# ──────────────────────────────────────────────────────────────────────────────
# hive llm providers
# ──────────────────────────────────────────────────────────────────────────────


def test_providers_table_lists_the_configured_provider_and_its_health(tmp_path: Path) -> None:
    manifest = _write_fake_manifest(tmp_path)

    result = runner.invoke(app, ["llm", "providers", "--manifest", str(manifest)])

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("NAME")
    assert len(lines) == 2
    assert "fake" in lines[1]
    assert "(vendor default)" in lines[1]  # base_url is unset for this provider.
    assert "no" in lines[1]  # no HIVEMIND_FAKE_API_KEY is set.
    assert "healthy" in lines[1]


def test_providers_json_matches_the_table_fields(tmp_path: Path) -> None:
    manifest = _write_fake_manifest(tmp_path)

    result = runner.invoke(app, ["llm", "providers", "--manifest", str(manifest), "--json"])

    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert rows == [
        {
            "name": "fake",
            "kind": "fake",
            "base_url": "(vendor default)",
            "seats": 4,
            "has_api_key": False,
            "health": "healthy: ok",
        }
    ]


def test_providers_shows_yes_when_the_api_key_env_var_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_fake_manifest(tmp_path)
    monkeypatch.setenv("HIVEMIND_FAKE_API_KEY", "unused-by-fake-but-present")

    result = runner.invoke(app, ["llm", "providers", "--manifest", str(manifest)])

    assert result.exit_code == 0
    assert "yes" in result.output.strip().splitlines()[1]


def test_providers_reports_refused_offline_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_fake_manifest(tmp_path)
    # A registry offline=True with a non-loopback (empty) base_url: registry.provider() raises
    # OfflineViolationError on construction (hivemind.llm.registry._check_offline).
    providers = {"fake": make_provider_config(kind="fake", base_url="")}
    registry = ProviderRegistry(providers, [], offline=True, deps=make_registry_deps())
    monkeypatch.setattr(llm_module, "build_registry", lambda manifest, environ, clock: registry)

    result = runner.invoke(app, ["llm", "providers", "--manifest", str(manifest)])

    assert result.exit_code == 0
    assert "refused: offline" in result.output


# ──────────────────────────────────────────────────────────────────────────────
# hive llm slots
# ──────────────────────────────────────────────────────────────────────────────


def test_slots_table_lists_all_nine_slots_bound_to_the_fake_provider(tmp_path: Path) -> None:
    manifest = _write_fake_manifest(tmp_path)

    result = runner.invoke(app, ["llm", "slots", "--manifest", str(manifest)])

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("SLOT")
    assert len(lines) == 10  # header + 9 ModelSlot members
    assert all("fake" in line and "test-model" in line for line in lines[1:])


def test_slots_json_shows_no_fallback_and_no_price(tmp_path: Path) -> None:
    manifest = _write_fake_manifest(tmp_path)

    result = runner.invoke(app, ["llm", "slots", "--manifest", str(manifest), "--json"])

    assert result.exit_code == 0
    rows = {row["slot"]: row for row in json.loads(result.output)}
    assert rows["worker"]["binding"] == "worker"
    assert rows["worker"]["fallback_chain"] == "worker"  # No fallback: chain is just itself.
    assert rows["worker"]["price"] == "-"  # No [forage.map] entry for this manifest.


# ──────────────────────────────────────────────────────────────────────────────
# hive llm test
# ──────────────────────────────────────────────────────────────────────────────


def test_test_command_prints_latency_usage_and_reply_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_fake_manifest(tmp_path)
    registry = _scripted_registry(text_response("ready"))
    monkeypatch.setattr(llm_module, "build_registry", lambda manifest, environ, clock: registry)

    result = runner.invoke(app, ["llm", "test", "worker", "--manifest", str(manifest)])

    assert result.exit_code == 0
    assert "latency:" in result.output
    assert "usage: input=0 output=0" in result.output
    assert "reply: ready" in result.output


def test_test_command_exits_1_with_the_typed_errors_message_on_failure(tmp_path: Path) -> None:
    # A fresh registry's FakeLLMProvider has nothing scripted: complete() raises
    # ProviderUnavailableError ("the scripted response queue ran dry"), a typed LLMError.
    manifest = _write_fake_manifest(tmp_path)

    result = runner.invoke(app, ["llm", "test", "worker", "--manifest", str(manifest)])

    assert result.exit_code == 1
    assert "unavailable" in result.output


def test_test_command_rejects_an_unknown_slot(tmp_path: Path) -> None:
    manifest = _write_fake_manifest(tmp_path)

    result = runner.invoke(app, ["llm", "test", "not-a-slot", "--manifest", str(manifest)])

    assert result.exit_code == 2
