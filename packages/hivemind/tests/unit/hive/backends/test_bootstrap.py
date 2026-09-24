"""Unit tests for hivemind.hive.backends.bootstrap: CellBootstrap, QueenEndpoint and its minter.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/backends/bootstrap.py
    (codingrules section 3). ReadinessGate itself is a Protocol with no logic of its own; its
    fake (`FakeReadinessGate`) is tested in test_fake.py beside `FakeCellBackend`, and the shared
    seam both backends provision through is exercised end to end by
    packages/hivemind/tests/contracts/test_cell_backend_contract.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.bootstrap for CellBootstrap, QueenEndpoint, CellReadyInfo and
      mint_cell_bootstrap, under test.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from hivemind.hive.backends.bootstrap import QueenEndpoint, mint_cell_bootstrap
from hivemind.hive.backends.provider_table import CellProviderSpec, CellSlotSpec
from waggle.clock import FakeClock
from waggle.ids import CellId, NodeId, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer, public_key_hex

_A_PROVIDER = CellProviderSpec(
    name="local",
    kind="openai_compat",
    base_url="http://host.docker.internal:1234/v1",
    default_model="local-test-model",
    capabilities={"vision": False},
    api_key_env="HIVEMIND_LOCAL_API_KEY",
)
_A_SLOT = CellSlotSpec(
    key="warden",
    provider="local",
    model="local-test-model",
    fallback=None,
    effort="MEDIUM",
    max_output_tokens=None,
)


@dataclasses.dataclass(frozen=True, slots=True)
class _EndpointFields:
    """Every `QueenEndpoint` field a test may override.

    codingrules 5.1's own argument-group escape hatch: `_make_endpoint`'s own parameter count
    would otherwise exceed the hard limit.
    """

    waggle_url: str = "wss://queen.example.org:8443/waggle"
    queen_node_id: NodeId | None = None  # None: _make_endpoint mints a fresh one.
    queen_verify_key_hex: str = "ab" * 32
    socks_proxy_url: str | None = None
    providers: tuple[CellProviderSpec, ...] = ()
    slots: tuple[CellSlotSpec, ...] = ()
    provider_api_keys: Mapping[str, SecretStr] = dataclasses.field(default_factory=dict)
    llm_offline: bool = False


def _make_endpoint(fields: _EndpointFields | None = None) -> QueenEndpoint:
    """Build a valid QueenEndpoint, with sensible defaults for every field a test ignores."""
    fields = fields if fields is not None else _EndpointFields()
    queen_node_id = fields.queen_node_id
    if queen_node_id is None:
        queen_node_id = new_node_id(FakeClock())
    return QueenEndpoint(
        waggle_url=fields.waggle_url,
        queen_node_id=queen_node_id,
        queen_verify_key_hex=fields.queen_verify_key_hex,
        socks_proxy_url=fields.socks_proxy_url,
        providers=fields.providers,
        slots=fields.slots,
        provider_api_keys=fields.provider_api_keys,
        llm_offline=fields.llm_offline,
    )


def test_mint_cell_bootstrap_mints_a_fresh_cell_id_and_keypair_each_call() -> None:
    clock = FakeClock()
    hive_id = new_hive_id(clock)
    endpoint = _make_endpoint()

    first = mint_cell_bootstrap(hive_id, endpoint, clock)
    second = mint_cell_bootstrap(hive_id, endpoint, clock)

    assert first.cell_id != second.cell_id
    assert first.public_key_hex != second.public_key_hex
    assert first.private_key_hex.get_secret_value() != second.private_key_hex.get_secret_value()


def test_mint_cell_bootstrap_public_key_matches_the_minted_private_key() -> None:
    bootstrap = mint_cell_bootstrap(new_hive_id(FakeClock()), _make_endpoint(), FakeClock())

    # Rebuild a signer from the private half and confirm its public half is exactly what
    # mint_cell_bootstrap handed out separately -- the two must never drift apart.
    signer = Ed25519Signer(bytes.fromhex(bootstrap.private_key_hex.get_secret_value()))
    assert public_key_hex(signer.public_key_bytes) == bootstrap.public_key_hex


def test_environment_renders_every_required_hivemind_variable() -> None:
    clock = FakeClock()
    hive_id = new_hive_id(clock)
    endpoint = _make_endpoint()
    bootstrap = mint_cell_bootstrap(hive_id, endpoint, clock)

    env = bootstrap.environment()

    # Exactly the required table from images/base-ubuntu/README.md's "Runtime configuration".
    assert env["HIVEMIND_QUEEN_WAGGLE_URL"] == endpoint.waggle_url
    assert env["HIVEMIND_CELL_ID"] == bootstrap.cell_id
    assert env["HIVEMIND_HIVE_ID"] == hive_id
    assert env["HIVEMIND_QUEEN_NODE_ID"] == endpoint.queen_node_id
    assert env["HIVEMIND_CELL_SIGNING_KEY"] == bootstrap.private_key_hex.get_secret_value()
    assert env["HIVEMIND_QUEEN_VERIFY_KEY"] == endpoint.queen_verify_key_hex
    assert env["HIVEMIND_COMB_SHIELD"] == "MEADOW"  # Roadmap step 10.3a: the Cell's own tier.
    assert "HIVEMIND_SOCKS_PROXY_URL" not in env
    # No provider table: the fake-backend e2e and every existing caller must see byte-for-byte
    # the same environment as before roadmap step 8.x (hivemind.cli.in_cell.providers's own
    # docstring).
    assert "HIVEMIND_PROVIDERS" not in env
    assert "HIVEMIND_SLOTS" not in env
    assert "HIVEMIND_LLM_OFFLINE" not in env


def test_environment_includes_socks_proxy_url_only_when_set() -> None:
    clock = FakeClock()
    endpoint = _make_endpoint(_EndpointFields(socks_proxy_url="socks5://127.0.0.1:9050"))
    bootstrap = mint_cell_bootstrap(new_hive_id(clock), endpoint, clock)

    env = bootstrap.environment()

    assert env["HIVEMIND_SOCKS_PROXY_URL"] == "socks5://127.0.0.1:9050"


def test_environment_renders_providers_and_slots_as_a_json_round_trip() -> None:
    clock = FakeClock()
    endpoint = _make_endpoint(_EndpointFields(providers=(_A_PROVIDER,), slots=(_A_SLOT,)))
    bootstrap = mint_cell_bootstrap(new_hive_id(clock), endpoint, clock)

    env = bootstrap.environment()

    providers = json.loads(env["HIVEMIND_PROVIDERS"])
    slots = json.loads(env["HIVEMIND_SLOTS"])
    assert providers == [
        {
            "name": "local",
            "kind": "openai_compat",
            "base_url": "http://host.docker.internal:1234/v1",
            "default_model": "local-test-model",
            "capabilities": {"vision": False},
            "api_key_env": "HIVEMIND_LOCAL_API_KEY",
        }
    ]
    assert slots == [
        {
            "key": "warden",
            "provider": "local",
            "model": "local-test-model",
            "fallback": None,
            "effort": "MEDIUM",
            "max_output_tokens": None,
        }
    ]


def test_environment_renders_provider_api_key_as_its_own_variable_never_in_the_json() -> None:
    clock = FakeClock()
    endpoint = _make_endpoint(
        _EndpointFields(
            providers=(_A_PROVIDER,),
            slots=(_A_SLOT,),
            provider_api_keys={"HIVEMIND_LOCAL_API_KEY": SecretStr("sk-super-secret")},
        )
    )
    bootstrap = mint_cell_bootstrap(new_hive_id(clock), endpoint, clock)

    env = bootstrap.environment()

    assert env["HIVEMIND_LOCAL_API_KEY"] == "sk-super-secret"
    # The key VALUE never rides the JSON blob, only the variable NAME (module docstring).
    assert "sk-super-secret" not in env["HIVEMIND_PROVIDERS"]
    assert "sk-super-secret" not in repr(endpoint)


def test_environment_renders_llm_offline_only_when_true() -> None:
    clock = FakeClock()
    offline_env = mint_cell_bootstrap(
        new_hive_id(clock),
        _make_endpoint(_EndpointFields(providers=(_A_PROVIDER,), llm_offline=True)),
        clock,
    ).environment()
    online_env = mint_cell_bootstrap(
        new_hive_id(clock),
        _make_endpoint(_EndpointFields(providers=(_A_PROVIDER,), llm_offline=False)),
        clock,
    ).environment()

    assert offline_env["HIVEMIND_LLM_OFFLINE"] == "true"
    assert "HIVEMIND_LLM_OFFLINE" not in online_env


def test_private_key_never_appears_in_bootstrap_repr() -> None:
    clock = FakeClock()
    endpoint = _make_endpoint()
    bootstrap = mint_cell_bootstrap(new_hive_id(clock), endpoint, clock)
    secret_hex = bootstrap.private_key_hex.get_secret_value()

    rendered = repr(bootstrap)

    # codingrules 13/15: key material never appears in a repr or log line. SecretStr's own repr
    # is what guarantees this; this test pins that guarantee against the dataclass that wraps it.
    assert secret_hex not in rendered
    assert "**********" in rendered


def test_queen_endpoint_is_frozen() -> None:
    endpoint = _make_endpoint()

    with pytest.raises(dataclasses.FrozenInstanceError):
        endpoint.waggle_url = "ws://localhost:9000"  # type: ignore[misc]  # The assignment is the test.


def test_cell_bootstrap_is_frozen() -> None:
    bootstrap = mint_cell_bootstrap(new_hive_id(FakeClock()), _make_endpoint(), FakeClock())

    with pytest.raises(dataclasses.FrozenInstanceError):
        bootstrap.cell_id = CellId("cell_should_not_be_settable")  # type: ignore[misc]  # The assignment is the test.
