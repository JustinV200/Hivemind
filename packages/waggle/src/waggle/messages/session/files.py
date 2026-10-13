"""Define the session family's file messages: put a file onto a device, get one back, delete one.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
session family is terminal-over-Waggle: a Warden (the always-on supervisor of one Cell, a unit of
compute) drives a remote Real Cell (an existing device borrowed for a task and left exactly as
found) through its Pollen Packet (the thin gateway on an enrolled device). The three messages here
move whole files across that link, or remove one, split out of ``waggle.messages.session.commands``
by responsibility so each file stays under the codingrules 5.1 size limit. ``SessionPutFile``
carries one chunk of a file to the device, the chunks tied together by a ``transfer_id`` and closed
by a final chunk carrying the whole file's digest; ``SessionGetFile`` asks for a file, which comes
back as ``SessionOutput`` FILE chunks; ``SessionDeleteFile`` (PROTOCOL_MINOR 13, roadmap phase 11)
removes one file the lease's work created, which a rollback or a clean-up needs and which an exec
of ``rm`` could only do by granting the Warden a shell. All three end in one ``SessionExit`` (or a
``control.error``), as the session rule of spec section 3 fixes, and all are recorded on the
Pheromone Trail (the append-only audit log) when they touch anything outside the lease's scratch
directory. Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Warden and read by a Pollen Packet; calls into
    waggle.messages.base only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A put-file chunk never reaches past total_bytes, and its final chunk always carries the
      digest the packet verifies before renaming the file into place.
    - A delete names a path and nothing else: no glob, no directory flag, no recursion, so a
      single message can only ever remove a single file.

See Also:
    - docs/waggle/spec.md section 8.6 for the normative fields, bounds and validators.
    - docs/waggle/spec.md section 5 (the chunking rule) for offset, final and the group key.
    - waggle.messages.session.commands for the enums and the four Warden-sent messages.
    - waggle.messages.session.output for the SessionOutput and SessionExit that answer these.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CHUNK_BYTES,
    MAX_PATH_CHARS,
    SHA256_PATTERN,
    LeaseIdField,
    MessageIdField,
    TaskIdField,
    WaggleMessage,
    WorkerIdField,
)

MIN_GET_FILE_BYTES = 1  # A cap of 0 could never stream a single byte; the read would refuse itself.
MIN_DELETE_PATH_CHARS = 1  # An empty path would name the scratch directory itself, never a file.

__all__ = [
    "MIN_DELETE_PATH_CHARS",
    "MIN_GET_FILE_BYTES",
    "SessionDeleteFile",
    "SessionGetFile",
    "SessionPutFile",
]


class SessionPutFile(WaggleMessage):
    """Write one chunk of a file onto the device (session.put_file, an event).

    Chunks are keyed by transfer_id, the envelope id of the chunk at offset 0 (which carries its
    own id there); the final chunk closes the transfer, answered by one SessionExit COMPLETED
    correlated to transfer_id, or by a control.error at any chunk, after which the packet deletes
    its temporary file. Two Workers writing the same path therefore never interleave.
    """

    lease_id: LeaseIdField = Field(description="The session the write belongs to.")
    transfer_id: MessageIdField = Field(
        description="The group key: the first chunk's envelope id, echoed on every chunk of the "
        "transfer, minted by the sender before the first chunk is wrapped."
    )
    total_bytes: int = Field(
        ge=0,
        description="Length of the whole file, so the packet can pre-check disk and the lease's "
        "cap before writing; offset plus the chunk length never exceeds it.",
    )
    path: str = Field(
        max_length=MAX_PATH_CHARS,
        description="Destination, absolute or relative to scratch; outside scratch the packet "
        "checks the lease's allowed roots and access level and records the touch.",
    )
    chunk: bytes = Field(max_length=MAX_CHUNK_BYTES, description="File bytes.")
    offset: int = Field(ge=0, description="Byte offset in the file.")
    final: bool = Field(
        default=False,
        description="True on the last chunk; the packet then verifies the digest and renames "
        "its temporary file into place.",
    )
    sha256: Annotated[str, Field(pattern=SHA256_PATTERN)] | None = Field(
        description="Digest of the whole file; required when final, None otherwise."
    )
    is_executable: bool = Field(
        default=False,
        description="Set the executable bit on completion (ignored on Windows).",
    )
    task_id: TaskIdField | None = Field(description="The task this write serves.")
    worker_id: WorkerIdField | None = Field(
        description="The sub-bee on whose behalf it is written."
    )

    @model_validator(mode="after")
    def _chunk_within_total(self) -> SessionPutFile:
        """Reject a chunk that would write past the file's declared length."""
        # The packet pre-allocates and cap-checks on total_bytes; a chunk reaching past it would
        # either be truncated or grow the file past what was approved, and both are wrong.
        end = self.offset + len(self.chunk)
        if end > self.total_bytes:
            raise ValueError(
                f"SessionPutFile chunk ends at byte {end}, past the declared total of "
                f"{self.total_bytes} bytes."
            )
        return self

    @model_validator(mode="after")
    def _digest_exactly_when_final(self) -> SessionPutFile:
        """Require the digest on the final chunk and forbid it on every other."""
        # The digest is what the packet verifies the reassembled file against, so the closing
        # chunk must carry it; on an earlier chunk it could only describe a file not yet whole.
        if self.final != (self.sha256 is not None):
            raise ValueError(
                f"SessionPutFile sha256 is required exactly when final, got final={self.final} "
                f"with sha256 {self.sha256}."
            )
        return self


class SessionGetFile(WaggleMessage):
    """Read a file from the device (session.get_file, a request).

    The packet streams it back as SessionOutput FILE chunks and ends with a SessionExit
    COMPLETED carrying the size and digest.
    """

    lease_id: LeaseIdField = Field(description="The session the read belongs to.")
    path: str = Field(
        max_length=MAX_PATH_CHARS,
        description="Source, absolute or relative to scratch; checked against the access "
        "level's readable roots.",
    )
    max_bytes: int = Field(
        default=DEFAULT_MAX_OUTPUT_BYTES,
        ge=MIN_GET_FILE_BYTES,
        description="Refuse (control.error) rather than stream a file larger than this.",
    )
    task_id: TaskIdField | None = Field(description="The task this read serves.")
    worker_id: WorkerIdField | None = Field(description="The sub-bee on whose behalf it is read.")


class SessionDeleteFile(WaggleMessage):
    """Delete one file on the device (session.delete_file, a request; PROTOCOL_MINOR 13).

    The packet removes the file and ends with one SessionExit with request_kind DELETE_FILE and
    outcome COMPLETED, which carries no size and no digest. A path that does not exist is a
    control.error pollen.not_found, and one outside what the lease may touch (or a directory)
    is pollen.path_refused; neither deletes anything.
    """

    lease_id: LeaseIdField = Field(description="The session the delete belongs to.")
    path: str = Field(
        min_length=MIN_DELETE_PATH_CHARS,
        max_length=MAX_PATH_CHARS,
        description="The file to remove, absolute or relative to scratch; resolved with symlinks "
        "followed and checked against the lease's allowed roots and access level, and a touch "
        "outside scratch is recorded with the file's prior bytes so the release can restore it.",
    )
    task_id: TaskIdField | None = Field(description="The task this delete serves.")
    worker_id: WorkerIdField | None = Field(
        description="The sub-bee on whose behalf it is deleted."
    )
