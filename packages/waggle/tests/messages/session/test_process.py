"""Tests for waggle.messages.session.process: ProcessAction, SessionProcess, SessionProcessState.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins the enum's members, and for both messages
    construction, the JSON round trip, the bounds and the id kinds, and every validator spec
    section 8.6 names in both directions: which of the command line, working directory,
    environment, log path and pid belong to which action, the command line's total size and
    the environment's unique names, and that only a STOP reports what it found running. The
    family's EXAMPLES live in test_commands.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.session.process for the module under test.
    - docs/waggle/spec.md section 8.6 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_PATH_CHARS, MAX_REASON_CHARS
from waggle.messages.session.commands import (
    MAX_ARGV_ITEM_CHARS,
    MAX_ARGV_ITEMS,
    MAX_ARGV_TOTAL_CHARS,
    MAX_ENV_VARS,
)
from waggle.messages.session.process import (
    MIN_PID,
    ProcessAction,
    SessionProcess,
    SessionProcessState,
)

CLOCK = FakeClock()
LEASE_ID = new_id(IdKind.LEASE, CLOCK)
LINE = ("Xvfb", ":99", "-screen", "0", "1280x800x24")
# The launch-only fields of a SessionProcess, each at a value that is not "unset".
LAUNCH_FIELDS: dict[str, object] = {
    "cwd": "work",
    "env": ({"name": "DISPLAY", "value": ":99"},),
    "log_path": "xvfb.log",
}


def _process(**overrides: object) -> SessionProcess:
    """A START of a display server with no working directory or environment, then overrides."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "action": ProcessAction.START,
        "argv": LINE,
        "cwd": None,
        "env": (),
        "log_path": "xvfb.log",
        "pid": None,
        "task_id": None,
        "worker_id": None,
    }
    return SessionProcess.model_validate({**fields, **overrides})


def _stop(action: str = "STOP", **overrides: object) -> SessionProcess:
    """A STOP (or QUERY) of pid 4242: no launch fields, a pid."""
    changes: dict[str, object] = {"action": action, "argv": (), "log_path": None, "pid": 4242}
    return _process(**{**changes, **overrides})


def _state(**overrides: object) -> SessionProcessState:
    """The state a successful START reports, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "action": ProcessAction.START,
        "pid": 4242,
        "argv": LINE,
        "log_path": "/var/lib/pollen/scratch/xvfb.log",
        "is_running": True,
        "was_running": None,
        "reason": "The display server started and holds :99.",
    }
    return SessionProcessState.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# ProcessAction
# ──────────────────────────────────────────────────────────────────────────────


def test_process_action_lists_exactly_the_spec_members_with_values_equal_to_names() -> None:
    assert [member.value for member in ProcessAction] == ["START", "STOP", "QUERY"]
    assert all(member.name == member.value for member in ProcessAction)


# ──────────────────────────────────────────────────────────────────────────────
# SessionProcess
# ──────────────────────────────────────────────────────────────────────────────


def test_session_process_round_trips_a_start_a_stop_and_a_query() -> None:
    start = _process(
        cwd="work",
        env=({"name": "DISPLAY", "value": ":99"},),
        task_id=new_id(IdKind.TASK, CLOCK),
        worker_id=new_id(IdKind.WORKER, CLOCK),
    )
    stop = _stop()
    query = _stop("QUERY")

    for message in (start, stop, query):
        assert SessionProcess.model_validate(message.model_dump(mode="json")) == message
    assert (start.action, stop.action, query.action) == tuple(ProcessAction)
    assert start.pid is None
    assert (stop.pid, query.pid) == (4242, 4242)


def test_session_process_accepts_a_start_with_no_log_and_a_command_line_at_every_limit() -> None:
    quiet = _process(log_path=None)
    at_limits = _process(
        argv=("x" * MAX_ARGV_ITEM_CHARS,) * (MAX_ARGV_TOTAL_CHARS // MAX_ARGV_ITEM_CHARS),
        env=tuple({"name": f"V{index}", "value": "1"} for index in range(MAX_ENV_VARS)),
        cwd="x" * MAX_PATH_CHARS,
        log_path="y" * MAX_PATH_CHARS,
    )

    assert quiet.log_path is None
    assert sum(len(item) for item in at_limits.argv) == MAX_ARGV_TOTAL_CHARS
    assert len(at_limits.env) == MAX_ENV_VARS


def test_session_process_hides_environment_values_from_the_repr() -> None:
    start = _process(env=({"name": "API_TOKEN", "value": "hunter2"},))

    assert "API_TOKEN" in repr(start)
    assert "hunter2" not in repr(start)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # START needs a program; STOP and QUERY must carry none.
        ({"argv": ()}, "argv is required exactly when action is START"),
        ({"action": "STOP", "pid": 4242}, "argv is required exactly when action is START"),
        # A launch-only field on a STOP would read as a restart in disguise.
        ({"action": "STOP", "argv": (), "pid": 4242, "cwd": "work"}, "for START only"),
        (
            {
                "action": "STOP",
                "argv": (),
                "pid": 4242,
                "log_path": None,
                "env": LAUNCH_FIELDS["env"],
            },
            "for START only",
        ),
        ({"action": "QUERY", "argv": (), "pid": 4242}, "for START only"),
        # The pid belongs to STOP and QUERY, and is required there.
        ({"pid": 4242}, "pid is required exactly when action is STOP or QUERY"),
        ({"action": "STOP", "argv": (), "log_path": None}, "pid is required exactly when"),
        ({"action": "QUERY", "argv": (), "log_path": None}, "pid is required exactly when"),
        # Bounds.
        ({"argv": ("x",) * (MAX_ARGV_ITEMS + 1)}, f"at most {MAX_ARGV_ITEMS}"),
        ({"argv": ("x" * (MAX_ARGV_ITEM_CHARS + 1),)}, f"at most {MAX_ARGV_ITEM_CHARS}"),
        (
            {
                "argv": ("x" * MAX_ARGV_ITEM_CHARS,)
                * (MAX_ARGV_TOTAL_CHARS // MAX_ARGV_ITEM_CHARS + 1)
            },
            f"more than the {MAX_ARGV_TOTAL_CHARS}",
        ),
        ({"cwd": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"log_path": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"env": ({"name": "A", "value": "1"},) * (MAX_ENV_VARS + 1)}, f"at most {MAX_ENV_VARS}"),
        ({"env": ({"name": "A", "value": "1"}, {"name": "A", "value": "2"})}, "unique"),
        (
            {"action": "STOP", "argv": (), "log_path": None, "pid": 0},
            f"greater than or equal to {MIN_PID}",
        ),
        ({"action": "RESTART"}, "action"),
        ({"lease_id": new_id(IdKind.CELL, CLOCK)}, "lease_"),
        ({"task_id": LEASE_ID}, "task_"),
        ({"worker_id": LEASE_ID}, "worker_"),
        ({"signal": "KILL"}, "extra"),
    ],
)
def test_session_process_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _process(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# SessionProcessState
# ──────────────────────────────────────────────────────────────────────────────


def test_session_process_state_round_trips_what_a_start_a_stop_and_a_query_report() -> None:
    started = _state()
    exited_at_once = _state(is_running=False, log_path=None)
    stopped = _state(action="STOP", argv=(), log_path=None, is_running=False, was_running=True)
    already_dead = _state(
        action="STOP", argv=(), log_path=None, is_running=False, was_running=False
    )
    queried = _state(action="QUERY", argv=(), log_path=None)

    for state in (started, exited_at_once, stopped, already_dead, queried):
        assert SessionProcessState.model_validate(state.model_dump(mode="json")) == state
    assert already_dead.was_running is False
    assert queried.was_running is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # Only a START has a command line to echo, and it must echo it.
        ({"argv": ()}, "argv is set exactly when action is START"),
        ({"action": "QUERY", "log_path": None}, "argv is set exactly when action is START"),
        # The log path is a START's alone.
        ({"action": "QUERY", "argv": ()}, "log_path is for START only"),
        # was_running exists exactly for a STOP.
        ({"was_running": True}, "was_running is set exactly when action is STOP"),
        (
            {"action": "STOP", "argv": (), "log_path": None},
            "was_running is set exactly when action is STOP",
        ),
        # Bounds.
        ({"pid": 0}, f"greater than or equal to {MIN_PID}"),
        (
            {
                "argv": ("x" * MAX_ARGV_ITEM_CHARS,)
                * (MAX_ARGV_TOTAL_CHARS // MAX_ARGV_ITEM_CHARS + 1)
            },
            "totals",
        ),
        ({"argv": ("x",) * (MAX_ARGV_ITEMS + 1)}, f"at most {MAX_ARGV_ITEMS}"),
        ({"log_path": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"reason": "x" * (MAX_REASON_CHARS + 1)}, f"at most {MAX_REASON_CHARS}"),
        ({"is_running": "perhaps"}, "is_running"),
        ({"lease_id": new_id(IdKind.CELL, CLOCK)}, "lease_"),
        ({"exit_code": 0}, "extra"),
    ],
)
def test_session_process_state_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _state(**changes)
