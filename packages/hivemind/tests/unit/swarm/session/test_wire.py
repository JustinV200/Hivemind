"""Tests for hivemind.swarm.session.wire: what a session's operations share, and their needs.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/wire.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The wire is built over a real lease and link, and the needs it states are read
    back as the capability strings the Guard checks, which is the form gateway contract section 10
    gives them in. What the Guard and the trail are handed about a command (the program's base
    name and a count, never an argument) is shown here, where the command is built.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.wire for the module under test.
    - docs/waggle/gateway-contract.md section 10 for the capabilities each request needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.swarm import LinkRig, RecordingGuard, make_link_rig, open_rig_session

from hivemind.cell.errors import PathNotAllowedError, SessionClosedError
from hivemind.cell.lease import RealCellLease
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError
from hivemind.swarm.session.codes import CAPABILITY_DENIED, LEASE_NOT_OPEN, PATH_REFUSED
from hivemind.swarm.session.paths import DevicePaths
from hivemind.swarm.session.wire import (
    SessionWire,
    command_for,
    exec_needs,
    read_needs,
    translated,
    write_needs,
)


@pytest.fixture
async def wired() -> AsyncIterator[tuple[SessionWire, RealCellLease, LinkRig]]:
    """A wire over a lease on the scripted device, with its link and the rig that owns it."""
    rig = await make_link_rig()
    _session, lease, guard = await open_rig_session(rig)
    yield SessionWire(rig.link, lease, guard, DevicePaths(lease)), lease, rig
    await rig.close()


def _strings(needs: tuple[object, ...]) -> list[str]:
    """The capability strings of a tuple of needs."""
    return [str(need) for need in needs]


def test_the_sender_of_session_traffic_is_the_leases_holder(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, lease, rig = wired

    assert wire.sender == str(lease.holder) == str(rig.node.warden_id)


def test_an_error_context_names_the_request_for_the_errors_translated_from_it(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, lease, _rig = wired

    context = wire.error_context("/etc/passwd", ("ls",), "fs:read:/etc/passwd")

    assert (context.scratch_dir, context.lease_id) == (lease.scratch_root, lease.id)
    assert (context.path, context.argv, context.needed) == (
        "/etc/passwd",
        ("ls",),
        "fs:read:/etc/passwd",
    )


def test_running_a_program_needs_exec_on_it_exactly_as_given(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired

    assert _strings(exec_needs(wire, "ls", None)) == ["exec:ls"]
    assert _strings(exec_needs(wire, "/usr/bin/ls", None)) == ["exec:/usr/bin/ls"]


def test_running_inside_scratch_needs_no_reach_but_running_outside_it_does(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, lease, _rig = wired
    inside = f"{lease.scratch_root.as_posix()}/work"  # Resolved, in the device's spelling.

    assert _strings(exec_needs(wire, "ls", inside)) == ["exec:ls"]
    assert _strings(exec_needs(wire, "ls", "/etc")) == ["exec:ls", "cell:outside_scratch:/etc"]


def test_reading_needs_fs_read_on_the_resolved_path_wherever_it_is(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired

    assert _strings(read_needs(wire, "/etc/hosts")) == ["fs:read:/etc/hosts"]


def test_writing_outside_scratch_needs_reach_as_well_as_the_write(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, lease, _rig = wired
    inside = f"{lease.scratch_root.as_posix()}/out.txt"  # Resolved, in the device's spelling.

    assert _strings(write_needs(wire, inside)) == [f"fs:write:{inside}"]
    assert _strings(write_needs(wire, "/data/x")) == [
        "fs:write:/data/x",
        "cell:outside_scratch:/data/x",
    ]


def test_a_scope_that_can_never_be_held_is_a_denial_that_does_not_echo_it(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired

    with pytest.raises(CommandDeniedError) as caught:
        exec_needs(wire, "", None)

    assert caught.value.needed == "exec:<invalid scope>"


def test_a_command_names_the_programs_base_name_and_a_count_and_never_an_argument(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, lease, _rig = wired
    needs = exec_needs(wire, "/usr/bin/ls", None)

    command = command_for(wire, "exec", needs, ("/usr/bin/ls", "-l", "/secret/dir"))

    assert (command.operation, command.lease_id, command.program) == ("exec", lease.id, "ls")
    assert command.argument_count == 2 and command.needs == needs and not command.outside_scratch
    assert command.task_id == lease.task_id and command.worker_id is None
    assert "/secret/dir" not in repr(command)


def test_a_command_with_no_argv_has_no_program_and_no_arguments(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired

    command = command_for(wire, "get_file", read_needs(wire, "/etc/hosts"))

    assert command.program is None and command.argument_count == 0


def test_a_command_is_flagged_when_any_of_its_needs_reaches_outside_scratch(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired

    command = command_for(wire, "put_file", write_needs(wire, "/data/x"))

    assert command.outside_scratch


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (CAPABILITY_DENIED, CommandDeniedError),
        (PATH_REFUSED, PathNotAllowedError),
        (LEASE_NOT_OPEN, SessionClosedError),
    ],
)
def test_a_refusal_with_a_promised_mapping_is_raised_as_that_error_from_the_devices_own(
    wired: tuple[SessionWire, RealCellLease, LinkRig], code: str, expected: type[Exception]
) -> None:
    wire, _lease, _rig = wired
    refusal = DeviceRequestError(code, "refused")

    with pytest.raises(expected) as caught, translated(wire.error_context("/x", (), "fs:read:/x")):
        raise refusal

    assert caught.value.__cause__ is refusal


def test_a_refusal_with_no_mapping_keeps_its_own_type_so_a_new_code_is_visible(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired
    refusal = DeviceRequestError("pollen.something_new", "unmapped")

    with pytest.raises(DeviceRequestError) as caught, translated(wire.error_context()):
        raise refusal

    assert caught.value is refusal


def test_other_errors_pass_through_a_translated_block_untouched(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, _lease, _rig = wired

    with pytest.raises(RuntimeError, match="unrelated"), translated(wire.error_context()):
        raise RuntimeError("unrelated")


def test_the_scratch_directory_is_a_path_the_session_error_names(
    wired: tuple[SessionWire, RealCellLease, LinkRig],
) -> None:
    wire, lease, _rig = wired

    with pytest.raises(SessionClosedError) as caught, translated(wire.error_context()):
        raise DeviceRequestError(LEASE_NOT_OPEN, "gone")

    assert isinstance(lease.scratch_root, Path) and str(lease.scratch_root) in str(caught.value)
    assert isinstance(wire.guard, RecordingGuard)
