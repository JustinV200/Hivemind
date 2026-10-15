"""Tests for hivemind.swarm.session.codes: the device's codes as session errors, and exit codes.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/codes.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.codes for the module under test.
    - docs/waggle/gateway-contract.md section 15 for the codes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell.errors import BackgroundStartError, PathNotAllowedError, SessionClosedError
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError
from hivemind.swarm.session.codes import (
    CAPABILITY_DENIED,
    KILLED_EXIT_CODE,
    LEASE_NOT_OPEN,
    NOT_FOUND,
    PATH_REFUSED,
    START_FAILED,
    UNKNOWN_SIGNAL_EXIT_CODE,
    ErrorContext,
    exit_code_for,
    translate,
)
from waggle.clock import FakeClock
from waggle.ids import new_lease_id, new_message_id
from waggle.messages.session.commands import SessionOutcome, SessionRequestKind
from waggle.messages.session.output import SessionExit

_CONTEXT = ErrorContext(
    scratch_dir=Path("/pollen/scratch/x"),
    lease_id=new_lease_id(FakeClock()),
    path="/pollen/outside/f",
    argv=("runs", "--flag"),
    needed="exec:runs",
)


def _refusal(code: str) -> DeviceRequestError:
    """A device refusal carrying `code`."""
    return DeviceRequestError(code, "The device said no.", is_retryable=False)


def _exit(outcome: SessionOutcome, **fields: object) -> SessionExit:
    """An EXEC exit with `outcome` and the fields that outcome needs."""
    clock = FakeClock()
    base: dict[str, object] = {
        "lease_id": new_lease_id(clock),
        "request_id": new_message_id(clock),
        "request_kind": SessionRequestKind.EXEC,
        "outcome": outcome,
        "exit_code": None,
        "signal_name": None,
        "duration_s": 0.0,
        "size_bytes": None,
        "sha256": None,
        "reason": "Scripted.",
    }
    base.update(fields)
    return SessionExit(**base)


def test_capability_denied_becomes_a_command_denied_naming_the_need() -> None:
    mapped = translate(_refusal(CAPABILITY_DENIED), _CONTEXT)

    assert isinstance(mapped, CommandDeniedError)
    assert mapped.needed == "exec:runs"
    assert mapped.lease_id == _CONTEXT.lease_id


def test_capability_denied_without_a_named_need_still_maps() -> None:
    bare = ErrorContext(scratch_dir=Path("/s"), lease_id=_CONTEXT.lease_id)

    mapped = translate(_refusal(CAPABILITY_DENIED), bare)

    assert isinstance(mapped, CommandDeniedError)
    assert mapped.needed == "an unnamed capability"


def test_path_refused_becomes_path_not_allowed_with_the_path_the_caller_asked_for() -> None:
    mapped = translate(_refusal(PATH_REFUSED), _CONTEXT)

    assert isinstance(mapped, PathNotAllowedError)
    assert mapped.path == Path("/pollen/outside/f")


def test_not_found_becomes_file_not_found() -> None:
    assert isinstance(translate(_refusal(NOT_FOUND), _CONTEXT), FileNotFoundError)


def test_start_failed_becomes_background_start_error_with_the_program_only_quoted() -> None:
    mapped = translate(_refusal(START_FAILED), _CONTEXT)

    assert isinstance(mapped, BackgroundStartError)
    assert mapped.argv == ("runs", "--flag")
    assert "--flag" not in str(mapped)


def test_start_failed_without_an_argv_still_maps() -> None:
    bare = ErrorContext(scratch_dir=Path("/s"), lease_id=_CONTEXT.lease_id)

    assert isinstance(translate(_refusal(START_FAILED), bare), BackgroundStartError)


def test_a_lease_that_is_not_open_means_the_session_is_closed() -> None:
    mapped = translate(_refusal(LEASE_NOT_OPEN), _CONTEXT)

    assert isinstance(mapped, SessionClosedError)
    assert mapped.scratch_dir == _CONTEXT.scratch_dir


@pytest.mark.parametrize(
    "code", ["pollen.quota_exceeded", "pollen.transfer_refused", "pollen.unsupported", "x.y"]
)
def test_a_code_with_no_promised_mapping_stays_the_device_request_error(code: str) -> None:
    refusal = _refusal(code)

    assert translate(refusal, _CONTEXT) is refusal


def test_an_exit_keeps_the_exit_code() -> None:
    assert exit_code_for(_exit(SessionOutcome.EXITED, exit_code=7)) == 7


def test_a_signal_death_is_the_negative_signal_number() -> None:
    assert exit_code_for(_exit(SessionOutcome.SIGNALED, signal_name="SIGTERM")) == -15


@pytest.mark.parametrize(("name", "code"), [("SIGKILL", -9), ("SIGABRT", -6), ("SIGQUIT", -3)])
def test_a_signal_posix_numbers_alike_everywhere_gets_that_number_on_any_host(
    name: str, code: int
) -> None:
    # A Windows Hive Stand's table has no SIGKILL or SIGQUIT and numbers SIGABRT 22, yet each is
    # the device's signal, and the device is POSIX.
    assert exit_code_for(_exit(SessionOutcome.SIGNALED, signal_name=name)) == code


def test_a_signal_this_host_has_no_number_for_gets_the_unknown_code() -> None:
    exit_message = _exit(SessionOutcome.SIGNALED, signal_name="CTRL_BREAK_EVENT")

    assert exit_code_for(exit_message) == UNKNOWN_SIGNAL_EXIT_CODE


def test_a_command_the_device_killed_gets_the_killed_code() -> None:
    assert exit_code_for(_exit(SessionOutcome.KILLED)) == KILLED_EXIT_CODE
