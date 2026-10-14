"""Tests for hivemind.hive.night_veil.session_commands: judge_* and the per-Hive command builders.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/session_commands.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.session_commands for the module under test.
"""

from __future__ import annotations

from hivemind.cell import CompletedCommand, OsFamily
from hivemind.cell.tunnel import COMMANDS as TUNNEL_COMMANDS
from hivemind.hive.night_veil.results import CheckStatus
from hivemind.hive.night_veil.session_commands import (
    CMD_HOSTNAME,
    CMD_TORRC,
    DNS_COMMANDS,
    TOR_BROWSER_PROFILE,
    WEBRTC_COMMANDS,
    SessionProbeConfig,
    direct_egress_cmd,
    hidden_service_cmd,
    judge_contains,
    judge_exit_zero,
    judge_fails,
    metadata_cmd,
)


def _completed(exit_code: int, stdout: bytes = b"") -> CompletedCommand:
    return CompletedCommand(exit_code=exit_code, stdout=stdout, stderr=b"", duration_s=0.01)


def _config(**overrides: object) -> SessionProbeConfig:
    fields: dict[str, object] = {
        "hidden_service_address": "abc123.onion",
        "socks_proxy_port": 9050,
        "locale_profile": "C.UTF-8",
    }
    fields.update(overrides)
    return SessionProbeConfig(**fields)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# judge_exit_zero
# ──────────────────────────────────────────────────────────────────────────────


def test_judge_exit_zero_passes_on_zero() -> None:
    assert judge_exit_zero(_completed(0), ok_detail="ok").status is CheckStatus.PASS


def test_judge_exit_zero_fails_on_nonzero() -> None:
    assert judge_exit_zero(_completed(1), ok_detail="ok").status is CheckStatus.FAIL


# ──────────────────────────────────────────────────────────────────────────────
# judge_contains
# ──────────────────────────────────────────────────────────────────────────────


def test_judge_contains_passes_when_the_needle_is_on_stdout() -> None:
    result = judge_contains(_completed(0, b"default via tun0"), "tun0", ok_detail="ok")
    assert result.status is CheckStatus.PASS


def test_judge_contains_fails_when_the_needle_is_missing() -> None:
    result = judge_contains(_completed(0, b"default via eth0"), "tun0", ok_detail="ok")
    assert result.status is CheckStatus.FAIL


def test_judge_contains_fails_on_nonzero_exit_even_with_the_needle_present() -> None:
    result = judge_contains(_completed(1, b"tun0"), "tun0", ok_detail="ok")
    assert result.status is CheckStatus.FAIL


# ──────────────────────────────────────────────────────────────────────────────
# judge_fails (for a probe that must be refused to be green)
# ──────────────────────────────────────────────────────────────────────────────


def test_judge_fails_passes_when_the_command_itself_failed() -> None:
    assert judge_fails(_completed(7), ok_detail="ok").status is CheckStatus.PASS


def test_judge_fails_fails_when_the_command_unexpectedly_succeeded() -> None:
    assert judge_fails(_completed(0), ok_detail="ok").status is CheckStatus.FAIL


# ──────────────────────────────────────────────────────────────────────────────
# Per-Hive command builders: argv only, never a shell string.
# ──────────────────────────────────────────────────────────────────────────────


def test_hidden_service_cmd_names_the_onion_address_and_socks_port() -> None:
    argv = hidden_service_cmd(_config())

    assert "abc123.onion" in " ".join(argv)
    assert "127.0.0.1:9050" in argv
    assert argv[0] == "curl"


def test_direct_egress_cmd_dials_the_configured_probe_host_with_no_proxy_flag() -> None:
    argv = direct_egress_cmd(_config(direct_egress_probe_host="203.0.113.1"))

    assert "203.0.113.1" in " ".join(argv)
    assert not any("socks" in arg for arg in argv)


def test_metadata_cmd_dials_the_first_configured_metadata_address() -> None:
    argv = metadata_cmd(_config(metadata_addresses=("169.254.169.254", "metadata.google.internal")))

    assert "169.254.169.254" in " ".join(argv)


def test_hidden_service_cmd_prints_the_http_status_and_nothing_else() -> None:
    # The judge reads exactly the status, so curl must print it (`-w`) and discard the body.
    argv = hidden_service_cmd(_config())

    assert argv[argv.index("-w") + 1] == "%{http_code}"
    assert argv[argv.index("-o") + 1] == "/dev/null"
    assert "--socks5-hostname" in argv  # The name is resolved by Tor, never by the Cell.


# ──────────────────────────────────────────────────────────────────────────────
# The multi-read checks' fixed commands: read-only, argv only, one table with Propolis.
# ──────────────────────────────────────────────────────────────────────────────


def test_the_dns_reads_are_the_propolis_tunnel_reads_plus_tors_configuration() -> None:
    # Taken from the tunnel tier's own table so the two tiers cannot disagree on a Cell's DNS.
    tunnel = TUNNEL_COMMANDS[OsFamily.LINUX]

    assert DNS_COMMANDS[: len(tunnel)] == tunnel
    assert [command.key for command in DNS_COMMANDS[len(tunnel) :]] == ["torrc"]
    assert DNS_COMMANDS[-1].argv == CMD_TORRC == ("cat", "/etc/tor/torrc")


def test_the_webrtc_reads_are_the_browser_profiles_two_preference_files() -> None:
    assert [(command.key, command.argv) for command in WEBRTC_COMMANDS] == [
        ("user_js", ("cat", f"{TOR_BROWSER_PROFILE}/user.js")),
        ("prefs_js", ("cat", f"{TOR_BROWSER_PROFILE}/prefs.js")),
    ]


def test_the_hostname_is_read_from_the_kernel_so_no_binary_is_needed() -> None:
    assert CMD_HOSTNAME == ("cat", "/proc/sys/kernel/hostname")


def test_every_read_is_a_plain_cat_of_one_file() -> None:
    # Read-only and fixed: a `cat` of one path cannot change the Cell it attests.
    reads = [CMD_HOSTNAME, CMD_TORRC, *(c.argv for c in (*DNS_COMMANDS, *WEBRTC_COMMANDS))]

    assert all(len(argv) == 2 and argv[0] == "cat" and argv[1].startswith("/") for argv in reads)


def test_the_stand_hostname_is_unknown_unless_the_hive_says_it() -> None:
    # Empty means "not known": the hostname check then judges only the random shape.
    assert _config().stand_hostname == ""
    assert _config(stand_hostname="bee-keeper").stand_hostname == "bee-keeper"
