"""Tests for hivemind.manifest.schema.llm.servers: ServerSpec and its two cross-table checks.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/llm/servers.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.llm.servers for the module under test.
    - hivemind.manifest.schema.manifest for the HiveManifest check that calls check_server_sources.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.forage import ModelSlot
from hivemind.manifest.schema import HiveManifest
from hivemind.manifest.schema.llm import LlmSection, ServerSpec, check_server_sources
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_PORT = 18080  # Any loopback port: the tests never open it.
_BASE_URL = f"http://127.0.0.1:{_PORT}/v1"


def _server(**overrides: object) -> dict[str, object]:
    """The smallest valid [llm.servers] row, with `overrides` applied."""
    row: dict[str, object] = {
        "provider": "local",
        "argv": ["scripted-server", "--port", "{port}", "--scratch", "{scratch}"],
        "port": _PORT,
        "sources": ["small_model"],
    }
    row.update(overrides)
    return row


def _llm(servers: dict[str, object], **providers: object) -> dict[str, object]:
    """An [llm] table with one loopback openai_compat provider `local`, plus `servers`."""
    table: dict[str, object] = {
        "local": {"kind": "openai_compat", "base_url": _BASE_URL},
        "hosted": {"kind": "anthropic"},
    }
    table.update(providers)
    return {
        "providers": table,
        "slots": {slot.manifest_key: {"provider": "local", "model": "m"} for slot in ModelSlot},
        "servers": servers,
    }


def test_a_server_spec_defaults_its_health_path_timeout_and_start() -> None:
    spec = ServerSpec.model_validate(_server())

    assert spec.health_path == "/models"
    assert spec.startup_timeout_s == 60.0
    assert spec.load_on_start is True
    assert spec.keep_alive_s is None


def test_a_server_spec_refuses_an_unknown_argv_placeholder() -> None:
    with pytest.raises(ValidationError, match=r"\{prot\}"):
        ServerSpec.model_validate(_server(argv=["scripted-server", "--port", "{prot}"]))


def test_a_server_spec_keeps_braces_that_are_not_placeholders_words() -> None:
    # A JSON argument is not a word between braces, so it must pass untouched.
    spec = ServerSpec.model_validate(_server(argv=["scripted-server", '--opts={"a": 1}']))

    assert spec.argv[1] == '--opts={"a": 1}'


@pytest.mark.parametrize("bad", [{"health_path": "models"}, {"port": 0}, {"sources": []}])
def test_a_server_spec_refuses_a_malformed_field(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ServerSpec.model_validate(_server(**bad))


def test_an_llm_section_accepts_a_server_on_its_providers_loopback_port() -> None:
    section = LlmSection.model_validate(_llm({"local": _server()}))

    assert section.servers["local"].port == _PORT


def test_an_llm_section_refuses_a_server_whose_provider_is_undeclared() -> None:
    with pytest.raises(ValidationError, match=r"no \[llm.providers.ghost\]"):
        LlmSection.model_validate(_llm({"local": _server(provider="ghost")}))


def test_an_llm_section_refuses_a_server_on_a_provider_of_another_kind() -> None:
    with pytest.raises(ValidationError, match="openai_compat"):
        LlmSection.model_validate(_llm({"local": _server(provider="hosted")}))


def test_an_llm_section_refuses_a_server_whose_provider_is_not_on_loopback() -> None:
    remote = {"remote": {"kind": "openai_compat", "base_url": f"http://example.org:{_PORT}/v1"}}

    with pytest.raises(ValidationError, match="loopback"):
        LlmSection.model_validate(_llm({"local": _server(provider="remote")}, **remote))


def test_an_llm_section_refuses_a_server_port_the_provider_does_not_carry() -> None:
    with pytest.raises(ValidationError, match="port"):
        LlmSection.model_validate(_llm({"local": _server(port=_PORT + 1)}))


def _manifest(source_provider: str, sources: tuple[str, ...]) -> dict[str, object]:
    clock = FakeClock()
    return {
        "hive": {"id": new_hive_id(clock), "node_id": new_node_id(clock)},
        "llm": _llm({"local": _server(sources=list(sources))}),
        "forage": {
            "roles": {
                "drone": {"cpu_cores": 0.5, "memory_bytes": 1024, "token_rate_per_minute": 100.0}
            },
            "map": {
                "small_model": {
                    "provider": source_provider,
                    "model": "m",
                    "grade": 2,
                    "context_window": 4096,
                }
            },
        },
    }


def test_a_hive_manifest_accepts_a_server_source_served_by_its_provider() -> None:
    manifest = HiveManifest.model_validate(_manifest("local", ("small_model",)))

    assert manifest.llm.servers["local"].sources == ("small_model",)


def test_a_hive_manifest_refuses_a_server_source_missing_from_the_map() -> None:
    with pytest.raises(ValidationError, match=r"no \[forage.map.absent\]"):
        HiveManifest.model_validate(_manifest("local", ("absent",)))


def test_a_hive_manifest_refuses_a_server_source_served_by_another_provider() -> None:
    with pytest.raises(ValidationError, match="not 'local'"):
        HiveManifest.model_validate(_manifest("hosted", ("small_model",)))


def test_check_server_sources_passes_with_no_servers() -> None:
    check_server_sources({}, {})  # Nothing declared, nothing to refuse.
