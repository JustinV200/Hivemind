"""Tests for hivemind.hive.night_veil.session_commands: judge_* and the per-Hive command builders.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/session_commands.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.session_commands for the module under test.
"""

from __future__ import annotations

from hivemind.cell import CompletedCommand
from hivemind.hive.night_veil.results import CheckStatus
from hivemind.hive.night_veil.session_commands import (
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
