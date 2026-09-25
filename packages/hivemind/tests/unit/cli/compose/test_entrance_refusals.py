"""Test hivemind.cli.compose.entrance's refusals: `hive serve` itself refuses every unsafe exposure.

ADR-0041: every remote mode needs TLS on a DNS name, lan and tunnel also mutual TLS, tunnel a
command to run; a mode whose prerequisites do not hold refuses to start, naming the rule, before
any socket is bound. ``test_plan_refusals`` proves the pure check makes every refusal from facts it
is handed; this module proves ``serve_hive`` makes every one from what it reads itself, one case
per ``ExposureRule``: each case composes the Hive ``hive serve`` runs (the fake provider, the
Hive's own SQLite file) over one broken ``[entrance]`` section, writes the TLS files the case
names (a self-signed certificate on a DNS name, or an expired one, one on another name, garbage,
a key that belongs to another certificate, a path with nothing there, or no table at all), and
enters ``serve_hive`` on a fake interface table.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Split by feature from
    ``test_entrance.py`` (codingrules 5.1), as ``test_plan_refusals.py`` is from ``test_plan.py``:
    that module starts the Entrance in every mode, this one asserts every refusal.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.plan for the rules and the order they are checked in.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from builders.entrance.mtls import PUBLIC_NAME, self_signed_tls, served_exposed

from hivemind.cli.compose.entrance import ServedHive, serve_hive
from hivemind.entrance.expose import ExposureRefusedError, ExposureRule, HostPlatform
from hivemind.entrance.expose import gather as gather_module

_OVERLAY = "100.101.102.103"  # On the fake tailscale0, inside vpn_cidrs' default range.
_LAN = "192.168.1.20"  # On the fake eth0: a private LAN address.
# The host every case runs on: Tailscale up, a LAN link, loopback.
_HOST = {"tailscale0": [_OVERLAY], "eth0": [_LAN], "lo": ["127.0.0.1"]}
# Each mode's section when nothing is broken, as TOML values; a case changes or drops keys.
_GOOD: Mapping[str, Mapping[str, str]] = {
    "vpn": {"expose": '"vpn"', "remote_bind": f'"{_OVERLAY}:8711"'},
    "lan": {"expose": '"lan"', "remote_bind": f'"{_LAN}:8711"'},
    "tunnel": {
        "expose": '"tunnel"',
        "remote_bind": '"127.0.0.1:0"',
        "tunnel_command": '["cloudflared", "tunnel", "run"]',
    },
}
_EVERY_MODE = {"bind": '"127.0.0.1:0"', "public_url": f'"https://{PUBLIC_NAME}"'}

# Which [entrance.tls] files a case writes: (cert, key) paths, or None for no table at all.
type TlsFiles = Callable[[Path], tuple[Path, Path] | None]


def _good(directory: Path) -> tuple[Path, Path]:
    """A current self-signed certificate on public_url's DNS name, and its own key."""
    files = self_signed_tls(directory / "good")
    return files.cert_path, files.key_path


def _no_table(_directory: Path) -> None:
    """No ``[entrance.tls]`` table: neither file is configured."""
    return None


def _cert_missing(directory: Path) -> tuple[Path, Path]:
    """The certificate's path names nothing."""
    return directory / "nothing-here.pem", _good(directory)[1]


def _cert_garbage(directory: Path) -> tuple[Path, Path]:
    """The certificate's file holds no certificate."""
    return _garbage(directory / "garbage.pem"), _good(directory)[1]


def _key_missing(directory: Path) -> tuple[Path, Path]:
    """The key's path names nothing."""
    return _good(directory)[0], directory / "nothing-here.key"


def _key_garbage(directory: Path) -> tuple[Path, Path]:
    """The key's file holds no key."""
    return _good(directory)[0], _garbage(directory / "garbage.key")


def _key_of_another(directory: Path) -> tuple[Path, Path]:
    """A readable key, but the private half of another certificate."""
    return _good(directory)[0], self_signed_tls(directory / "another").key_path


def _expired(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate on the right name that ended an hour ago."""
    files = self_signed_tls(directory / "expired", expired=True)
    return files.cert_path, files.key_path


def _other_name(directory: Path) -> tuple[Path, Path]:
    """A current certificate on a name that is not public_url's."""
    files = self_signed_tls(directory / "other", "other.example.test")
    return files.cert_path, files.key_path


def _garbage(path: Path) -> Path:
    """Write a file that is no PEM at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a certificate, not a key\n", encoding="utf-8")
    return path


@dataclass(frozen=True, slots=True)
class _Case:
    """One broken section: the rule it breaks, its mode, and what differs from the good one.

    Attributes:
        rule: The rule ``serve_hive`` must name.
        mode: ``vpn``, ``lan`` or ``tunnel``.
        changes: Keys set to other TOML values, or dropped (None).
        tls: The TLS files written.
        platform: The platform the host reports, when the rule depends on it.
    """

    rule: ExposureRule
    mode: str
    changes: Mapping[str, str | None] = field(default_factory=dict)
    tls: TlsFiles = _good
    platform: HostPlatform | None = None


_CASES = [
    _Case(ExposureRule.MUTUAL_TLS_REQUIRED, "lan", {"mutual_tls": "false"}),
    _Case(ExposureRule.TUNNEL_COMMAND_MISSING, "tunnel", {"tunnel_command": None}),
    _Case(ExposureRule.REMOTE_BIND_MISSING, "vpn", {"remote_bind": None}),
    _Case(ExposureRule.REMOTE_BIND_LOOPBACK, "lan", {"remote_bind": '"127.0.0.1:8711"'}),
    _Case(ExposureRule.VPN_BIND_GLOBAL, "vpn", {"remote_bind": '"8.8.4.4:8711"'}),
    _Case(ExposureRule.VPN_BIND_OUTSIDE_CIDRS, "vpn", {"remote_bind": f'"{_LAN}:8711"'}),
    _Case(ExposureRule.VPN_INTERFACE_UNNAMED, "vpn", platform=HostPlatform.MACOS),
    _Case(ExposureRule.VPN_INTERFACE_ABSENT, "vpn", {"vpn_interface": '"wg0"'}),
    _Case(ExposureRule.VPN_BIND_NOT_ON_INTERFACE, "vpn", {"remote_bind": '"100.101.102.104:8711"'}),
    _Case(ExposureRule.LAN_BIND_NOT_PRIVATE, "lan", {"remote_bind": '"8.8.4.4:8711"'}),
    _Case(ExposureRule.LAN_BIND_NOT_LOCAL, "lan", {"remote_bind": '"192.168.1.99:8711"'}),
    _Case(ExposureRule.TUNNEL_BIND_NOT_LOOPBACK, "tunnel", {"remote_bind": f'"{_LAN}:8711"'}),
    _Case(
        ExposureRule.TUNNEL_BIND_CLASHES,
        "tunnel",
        {"bind": '"127.0.0.1:8710"', "remote_bind": '"127.0.0.1:8710"'},
    ),
    _Case(ExposureRule.PUBLIC_URL_MISSING, "vpn", {"public_url": None}),
    _Case(ExposureRule.PUBLIC_URL_NOT_A_NAME, "vpn", {"public_url": f'"https://{_OVERLAY}"'}),
    _Case(ExposureRule.RP_ID_OUTSIDE_PUBLIC_URL, "vpn", {"rp_id": '"other.example"'}),
    _Case(
        ExposureRule.RP_ID_PUBLIC_SUFFIX,
        "vpn",
        {"public_url": '"https://hive.tail1234.ts.net"', "rp_id": '"ts.net"'},
    ),
    _Case(ExposureRule.TLS_NOT_CONFIGURED, "vpn", tls=_no_table),
    _Case(ExposureRule.TLS_CERT_UNREADABLE, "vpn", tls=_cert_missing),
    _Case(ExposureRule.TLS_CERT_UNPARSEABLE, "vpn", tls=_cert_garbage),
    _Case(ExposureRule.TLS_KEY_UNREADABLE, "vpn", tls=_key_missing),
    _Case(ExposureRule.TLS_KEY_UNPARSEABLE, "lan", tls=_key_garbage),
    _Case(ExposureRule.TLS_KEY_MISMATCH, "tunnel", tls=_key_of_another),
    _Case(ExposureRule.TLS_CERT_NOT_CURRENT, "vpn", tls=_expired),
    _Case(ExposureRule.TLS_NAME_MISMATCH, "lan", tls=_other_name),
]


def _section(case: _Case, directory: Path) -> str:
    """The case's ``[entrance]`` body: the mode's good section, changed, with its TLS table."""
    values = {**_EVERY_MODE, **_GOOD[case.mode], **case.changes}
    lines = [f"{key} = {value}" for key, value in values.items() if value is not None]
    files = case.tls(directory)
    # The sub-table comes last: every key after it would belong to [entrance.tls].
    if files is not None:
        lines += ["", "[entrance.tls]", f'cert = "{files[0]}"', f'key = "{files[1]}"']
    return "\n".join(lines) + "\n"


async def _start(served: ServedHive) -> None:
    """Enter ``serve_hive``, which must refuse before anything listens."""
    async with serve_hive(served):
        raise AssertionError("serve_hive started over a section it must refuse.")


@pytest.mark.parametrize("case", [pytest.param(case, id=case.rule.value) for case in _CASES])
def test_serve_refuses_with_the_rule_the_host_breaks(
    case: _Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The platform is the operating system's own answer; on this host only a stand-in for the
    # adapter's reading of it (our name, codingrules 14.4) reaches a rule of another platform.
    if case.platform is not None:
        monkeypatch.setattr(gather_module, "host_platform", lambda: case.platform)

    # Composed outside the loop, as `hive serve` composes it (build_hive refuses a running loop).
    served = served_exposed(tmp_path / "hive", _section(case, tmp_path / "tls"), _HOST)

    with pytest.raises(ExposureRefusedError) as caught:
        asyncio.run(_start(served))

    assert caught.value.rule is case.rule
    assert caught.value.mode.value == case.mode
    assert case.rule.value in str(caught.value)


def test_every_rule_has_a_case_that_serve_hive_refuses() -> None:
    assert [case.rule for case in _CASES] == list(ExposureRule)
