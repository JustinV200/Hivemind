"""Tests for hivemind.llm.registry's locality rule: which providers serve locally (10.3a).

A Night Veil task binds only local models (ADR-0030): an in-process provider, or one served on
the binding machine's own loopback. A Virtual Cell gateway host is the Hive Stand's machine, so
it is never local here, although `offline` accepts it.

Fits into the Hive:
    Mirrors src/hivemind/llm/registry.py (codingrules section 5.1: split by feature).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry for runs_in_process and runs_locally.
"""

from __future__ import annotations

import pytest

from hivemind.llm.registry import ProviderConfig, ProviderKind, runs_in_process, runs_locally


def _config(kind: ProviderKind, base_url: str = "") -> ProviderConfig:
    return ProviderConfig(kind=kind, base_url=base_url, default_model="m")


def test_an_in_process_provider_is_local_wherever_it_is_bound() -> None:
    assert runs_in_process(_config("fake"))
    assert runs_locally(_config("fake"))


@pytest.mark.parametrize(
    "base_url", ["http://127.0.0.1:1234/v1", "http://localhost:8080/v1", "http://[::1]:8000/v1"]
)
def test_a_server_on_loopback_is_local(base_url: str) -> None:
    config = _config("openai_compat", base_url)

    assert runs_locally(config)
    assert not runs_in_process(config)


@pytest.mark.parametrize(
    "base_url",
    [
        "",  # A hosted, vendor-fixed endpoint.
        "https://api.example.com/v1",
        "http://host.docker.internal:1234/v1",  # The Hive Stand, seen from inside a Cell.
        "http://10.0.2.2:1234/v1",
    ],
)
def test_a_hosted_or_hive_stand_endpoint_is_never_local(base_url: str) -> None:
    assert not runs_locally(_config("openai_compat", base_url))


def test_a_hosted_kind_is_not_local() -> None:
    assert not runs_locally(_config("anthropic"))
