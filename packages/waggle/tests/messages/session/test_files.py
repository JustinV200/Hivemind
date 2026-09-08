"""Tests for waggle.messages.session.files: SessionPutFile and SessionGetFile.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins for both classes construction, the JSON
    round trip (a chunk holding every byte value included), the defaults, the bounds and the id
    kinds, and both validators spec section 8.6 names for SessionPutFile (the digest exactly on
    the final chunk, and offset plus chunk never past total_bytes) in both directions. The
    family's EXAMPLES live in test_commands.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.session.files for the module under test.
    - docs/waggle/spec.md section 8.6 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import DEFAULT_MAX_OUTPUT_BYTES, MAX_CHUNK_BYTES, MAX_PATH_CHARS
from waggle.messages.session.files import SessionGetFile, SessionPutFile

CLOCK = FakeClock()
LEASE_ID = new_id(IdKind.LEASE, CLOCK)
TRANSFER_ID = new_id(IdKind.MESSAGE, CLOCK)
DIGEST = "0123456789abcdef" * 4  # 32 bytes of digest as 64 lowercase hex characters.


def _put(**overrides: object) -> SessionPutFile:
    """A three-byte file sent whole in one final chunk, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "transfer_id": TRANSFER_ID,
        "total_bytes": 3,
        "path": "notes/plan.md",
        "chunk": b"abc",
        "offset": 0,
        "final": True,
        "sha256": DIGEST,
        "task_id": None,
        "worker_id": None,
    }
    return SessionPutFile.model_validate({**fields, **overrides})


def _get(**overrides: object) -> SessionGetFile:
    """A read of one log file for no particular task, then ``overrides``."""
    fields: dict[str, object] = {
        "lease_id": LEASE_ID,
        "path": "/var/log/hive.log",
        "task_id": None,
        "worker_id": None,
    }
    return SessionGetFile.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# SessionPutFile
# ──────────────────────────────────────────────────────────────────────────────


def test_session_put_file_constructs_round_trips_and_defaults() -> None:
    whole = _put(task_id=new_id(IdKind.TASK, CLOCK), worker_id=new_id(IdKind.WORKER, CLOCK))
    first = _put(total_bytes=6, final=False, sha256=None, chunk=bytes(range(3)))

    assert whole.is_executable is False
    assert first.final is False
    assert SessionPutFile.model_validate(whole.model_dump(mode="json")) == whole
    assert SessionPutFile.model_validate(first.model_dump(mode="json")) == first
    assert SessionPutFile.model_validate(
        {
            **whole.model_dump(),
            "chunk": bytes(range(256)),
            "total_bytes": 256,
            "is_executable": True,
        }
    ).is_executable


def test_session_put_file_requires_the_digest_exactly_on_the_final_chunk() -> None:
    with pytest.raises(ValidationError, match="required exactly when final"):
        _put(final=True, sha256=None)
    with pytest.raises(ValidationError, match="required exactly when final"):
        _put(final=False, sha256=DIGEST)


def test_session_put_file_keeps_every_chunk_within_the_declared_total() -> None:
    # A chunk that ends exactly at the total is the normal last chunk; one byte past is not.
    assert _put(total_bytes=6, offset=3).offset == 3
    assert _put(total_bytes=0, chunk=b"").chunk == b""
    assert _put(chunk=b"x" * MAX_CHUNK_BYTES, total_bytes=MAX_CHUNK_BYTES)
    with pytest.raises(ValidationError, match="past the declared total"):
        _put(total_bytes=2)
    with pytest.raises(ValidationError, match="past the declared total"):
        _put(total_bytes=3, offset=1)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"chunk": b"x" * (MAX_CHUNK_BYTES + 1), "total_bytes": MAX_CHUNK_BYTES + 1},
            f"at most {MAX_CHUNK_BYTES} bytes",
        ),
        ({"path": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"total_bytes": -1}, "greater than or equal to 0"),
        ({"offset": -1}, "greater than or equal to 0"),
        ({"sha256": DIGEST[:-1]}, "pattern"),
        ({"sha256": DIGEST.upper()}, "pattern"),
        ({"transfer_id": LEASE_ID}, "msg_"),
        ({"lease_id": TRANSFER_ID}, "lease_"),
        ({"task_id": LEASE_ID}, "task_"),
        ({"worker_id": LEASE_ID}, "worker_"),
        ({"mode": "0644"}, "extra"),
    ],
)
def test_session_put_file_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _put(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# SessionGetFile
# ──────────────────────────────────────────────────────────────────────────────


def test_session_get_file_constructs_round_trips_and_defaults_its_cap() -> None:
    plain = _get()
    for_task = _get(task_id=new_id(IdKind.TASK, CLOCK), worker_id=new_id(IdKind.WORKER, CLOCK))

    assert plain.max_bytes == DEFAULT_MAX_OUTPUT_BYTES
    assert _get(max_bytes=1).max_bytes == 1
    assert SessionGetFile.model_validate(plain.model_dump(mode="json")) == plain
    assert SessionGetFile.model_validate(for_task.model_dump(mode="json")) == for_task


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"max_bytes": 0}, "greater than or equal to 1"),
        ({"path": "x" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
        ({"lease_id": TRANSFER_ID}, "lease_"),
        ({"task_id": LEASE_ID}, "task_"),
        ({"worker_id": LEASE_ID}, "worker_"),
        ({"follow_symlinks": True}, "extra"),
    ],
)
def test_session_get_file_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _get(**changes)
