"""Tests for hivemind.entrance.expose.facts: the host picture the exposure check decides on.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/facts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.facts for the module under test.
"""

from __future__ import annotations

import sys

import pytest
from unit.entrance.expose.support import TAILSCALE_V4, host_facts

from hivemind.entrance.expose import FileState, HostPlatform, TlsFacts, host_platform


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux", HostPlatform.LINUX),
        ("win32", HostPlatform.WINDOWS),
        ("darwin", HostPlatform.MACOS),
        ("freebsd14", HostPlatform.OTHER),
    ],
)
def test_host_platform_reads_sys_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: HostPlatform
) -> None:
    monkeypatch.setattr(sys, "platform", platform)

    assert host_platform() is expected


def test_interface_finds_an_interface_by_its_exact_name() -> None:
    facts = host_facts({"tailscale0": (TAILSCALE_V4,)})

    found = facts.interface("tailscale0")

    assert found is not None
    assert {str(address) for address in found.addresses} == {TAILSCALE_V4}
    assert facts.interface("Tailscale0") is None


def test_not_configured_tls_facts_describe_no_files_at_all() -> None:
    facts = TlsFacts.not_configured()

    assert (facts.cert, facts.key) == (FileState.NOT_CONFIGURED, FileState.NOT_CONFIGURED)
    assert (facts.cert_path, facts.key_path) == (None, None)
    assert facts.key_matches_cert is False
    assert facts.dns_names == ()
