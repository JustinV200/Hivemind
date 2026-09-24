"""Define the Capping gate's proposed action: the diff, command or sequence a bee wants to run.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The
Capping gate is the quality gate every side effect outside a lease's scratch directory passes:
propose, check, apply, verify, roll back. ``ProposedAction`` is the action itself, in a shape
deterministic checks can read: one ``ActionKind`` (a diff, a command as an argument list, a
sequence of prose steps for a device, a copy by digest, or typed GUI steps for the Exoskeleton)
with exactly the field that kind needs populated, the paths it touches, and a bound on the
characters it carries in total. ``GUI`` (roadmap step 6.5, protocol 1.6, ADR-0032) carries
``waggle.messages.capping.gui.GuiStep``s the gate applies through the attached Exoskeleton (the
optional display, input, audio and browser peripherals of a Cell), never prose it would have to
interpret. A diff too large for the
wire is written to scratch with the session and referenced by ``paths`` plus ``diff_sha256``
instead. ``COPY`` (roadmap step 5.0e, protocol 1.4) is for the same reason but unconditional: a
diff is text, so it can carry small binary content awkwardly at best, but the `keep` tool moves a
scratch file of any size and kind to a path outside it, so its proposal never carries the bytes at
all -- only their sha256 and size in ``copy_sha256``/``copy_size``, with ``paths`` holding exactly
the source (inside scratch) and the destination (outside it); the gate reads the source's bytes
from scratch itself at apply time and verifies the hash before writing the destination. The model
is split out of ``waggle.messages.capping.proposals`` by responsibility so each file stays under
the codingrules 5.1 size limit. Every bound is a named constant here; the number, not the name, is
normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.capping.proposals, whose
    ProposalSubmitted carries one ProposedAction; calls into waggle.messages.base and
    waggle.messages.capping.gui only.

Key invariants:
    - Exactly the field matching ``kind`` is populated: a DIFF carries one of diff or
      diff_sha256, a COMMAND a non-empty command, an ACTION_SEQUENCE non-empty steps, a COPY both
      copy_sha256 and copy_size plus exactly two paths (source, destination), a GUI non-empty
      gui steps, and no action carries another kind's field.
    - The characters across summary, diff, command, paths and steps never exceed
      MAX_ACTION_CHARS, so a proposal's envelope always fits a frame.
    - The model is frozen and forbids extras through VALUE_MODEL_CONFIG, like a message, but
      does not subclass WaggleMessage, so it can never be registered as a kind.

See Also:
    - docs/waggle/spec.md section 8.9 for the normative fields, bounds and validators.
    - waggle.messages.capping.proposals for ProposalSubmitted, which carries this action.
    - waggle.messages.base for VALUE_MODEL_CONFIG, MAX_PATH_CHARS and SHA256_PATTERN.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import MAX_PATH_CHARS, SHA256_PATTERN, VALUE_MODEL_CONFIG
from waggle.messages.capping.gui import GuiStep

MAX_SUMMARY_CHARS = 1_000  # One paragraph saying what the action does, for the checks and trail.
MAX_DIFF_CHARS = 131_072  # 128 KiB of unified diff inline; a larger one goes to scratch by digest.
MAX_COMMAND_ITEMS = 64  # A program plus its flags; a longer command line belongs in a script.
MAX_COMMAND_ITEM_CHARS = 4_096  # One argument; a path (MAX_PATH_CHARS) is the longest sane one.
MAX_PATHS = 64  # The paths one action touches; a refactor across more is several proposals.
MAX_STEPS = 100  # A prose action sequence for a device; longer is a task.
MAX_STEP_CHARS = 1_000  # One step in prose: what to click, type or wait for.
MAX_ACTION_CHARS = 262_144  # 256 KiB across every text field, so the frame stays under 1 MiB.
COPY_PATHS_LEN = 2  # A COPY action's own paths: exactly (source, destination), never more or less.
MAX_GUI_STEPS = 32  # One tool call's GUI input (fill, then press); a longer run is a procedure.

__all__ = [
    "COPY_PATHS_LEN",
    "MAX_ACTION_CHARS",
    "MAX_COMMAND_ITEMS",
    "MAX_COMMAND_ITEM_CHARS",
    "MAX_DIFF_CHARS",
    "MAX_GUI_STEPS",
    "MAX_PATHS",
    "MAX_STEPS",
    "MAX_STEP_CHARS",
    "MAX_SUMMARY_CHARS",
    "ActionKind",
    "ProposedAction",
]


class ActionKind(Enum):
    """The shape of a proposed action, which decides the field that must be populated."""

    DIFF = "DIFF"  # A unified diff over the paths, inline or by digest.
    COMMAND = "COMMAND"  # One program run as an argument list, never a shell string.
    ACTION_SEQUENCE = "ACTION_SEQUENCE"  # Ordered prose steps, for a device.
    COPY = "COPY"  # roadmap 5.0e: move a scratch file outside it, by digest, never inline bytes.
    GUI = "GUI"  # roadmap 6.5 (1.6): typed Exoskeleton steps the gate applies, never prose.


class ProposedAction(BaseModel):
    """The action a proposal wants to take, in a shape deterministic checks can read.

    Carried by capping.proposal_submitted from a Worker to its Warden; the checks read it, the
    Warden applies it, and the trail records it.
    """

    model_config = VALUE_MODEL_CONFIG

    kind: ActionKind = Field(description="A diff, a command or a sequence of steps.")
    summary: str = Field(max_length=MAX_SUMMARY_CHARS, description="What the action does.")
    diff: Annotated[str, Field(max_length=MAX_DIFF_CHARS)] | None = Field(
        description="The unified diff inline; None when larger than the cap, in which case it "
        "is in scratch, referenced by paths plus diff_sha256.",
    )
    diff_sha256: Annotated[str, Field(pattern=SHA256_PATTERN)] | None = Field(
        description="Digest of a diff written to scratch; set exactly when diff is None for a "
        "DIFF action.",
    )
    command: tuple[Annotated[str, Field(max_length=MAX_COMMAND_ITEM_CHARS)], ...] = Field(
        max_length=MAX_COMMAND_ITEMS,
        description="The command as an argument list, never a shell string; non-empty exactly "
        "for a COMMAND action.",
    )
    cwd: Annotated[str, Field(max_length=MAX_PATH_CHARS)] | None = Field(
        description="The working directory for a command; None means the lease's scratch."
    )
    paths: tuple[Annotated[str, Field(max_length=MAX_PATH_CHARS)], ...] = Field(
        max_length=MAX_PATHS, description="The paths the action touches."
    )
    steps: tuple[Annotated[str, Field(max_length=MAX_STEP_CHARS)], ...] = Field(
        max_length=MAX_STEPS,
        description="The ordered steps; non-empty exactly for an ACTION_SEQUENCE action.",
    )
    copy_sha256: Annotated[str, Field(pattern=SHA256_PATTERN)] | None = Field(
        default=None,
        description="Digest of the source file's bytes, verified against scratch at apply time; "
        "set exactly for a COPY action.",
    )
    copy_size: int | None = Field(
        default=None,
        ge=0,
        description="Size in bytes of the source file at proposal time; set exactly for a COPY "
        "action, and re-checked against the tier's max_copy_bytes and the file's actual size at "
        "apply time.",
    )
    gui: tuple[GuiStep, ...] = Field(
        default=(),
        max_length=MAX_GUI_STEPS,
        description="The typed Exoskeleton steps, applied in order; non-empty exactly for a GUI "
        "action (protocol 1.6).",
    )

    @model_validator(mode="after")
    def _field_matches_kind(self) -> ProposedAction:
        """Require exactly the field the kind needs and refuse the fields of other kinds."""
        # A DIFF is carried inline or by digest, never both and never neither: both would leave
        # the checks two candidates to compare, neither gives them nothing to check.
        if self.kind is ActionKind.DIFF:
            if (self.diff is None) == (self.diff_sha256 is None):
                raise ValueError(
                    "A DIFF action carries exactly one of diff and diff_sha256, got "
                    f"diff {'set' if self.diff is not None else 'None'} and diff_sha256 "
                    f"{self.diff_sha256}."
                )
        # A diff on a COMMAND, ACTION_SEQUENCE or COPY would never be applied, so its presence
        # means the sender confused two actions; refusing it keeps the gate's inputs unambiguous.
        elif self.diff is not None or self.diff_sha256 is not None:
            raise ValueError(
                f"A {self.kind.value} action carries neither diff nor diff_sha256; only a DIFF "
                "action does."
            )
        # command and steps are each non-empty exactly for their own kind (spec 8.9: "non-empty
        # only for" plus "the field matching kind must be populated").
        if bool(self.command) != (self.kind is ActionKind.COMMAND):
            raise ValueError(
                f"A ProposedAction command is non-empty exactly for a COMMAND action, got kind "
                f"{self.kind.value} with {len(self.command)} item(s)."
            )
        if bool(self.steps) != (self.kind is ActionKind.ACTION_SEQUENCE):
            raise ValueError(
                f"A ProposedAction steps is non-empty exactly for an ACTION_SEQUENCE action, "
                f"got kind {self.kind.value} with {len(self.steps)} step(s)."
            )
        # A GUI action is its typed steps and nothing else (protocol 1.6): the gate applies them
        # through the attached Exoskeleton, so an empty tuple would apply nothing at all.
        if bool(self.gui) != (self.kind is ActionKind.GUI):
            raise ValueError(
                f"A ProposedAction gui is non-empty exactly for a GUI action, got kind "
                f"{self.kind.value} with {len(self.gui)} GUI step(s)."
            )
        self._check_copy_fields()
        return self

    def _check_copy_fields(self) -> None:
        """Require copy_sha256/copy_size and exactly two paths for COPY, neither for any other."""
        is_copy = self.kind is ActionKind.COPY
        if (self.copy_sha256 is None or self.copy_size is None) == is_copy:
            raise ValueError(
                "A ProposedAction carries both copy_sha256 and copy_size exactly for a COPY "
                f"action, got kind {self.kind.value} with copy_sha256 "
                f"{'set' if self.copy_sha256 is not None else 'None'} and copy_size "
                f"{self.copy_size}."
            )
        if is_copy and len(self.paths) != COPY_PATHS_LEN:
            raise ValueError(
                f"A COPY action carries exactly {COPY_PATHS_LEN} paths (source, destination), "
                f"got {len(self.paths)}."
            )

    @model_validator(mode="after")
    def _total_chars_within_cap(self) -> ProposedAction:
        """Reject an action whose text fields together exceed MAX_ACTION_CHARS."""
        # The per-field caps alone allow far more than a frame holds (64 command items of 4096
        # characters already reach the cap), so the sum is bounded too, and the number is the
        # chunk size the rest of the protocol fits under 1 MiB.
        total = (
            len(self.summary)
            + len(self.diff or "")
            + sum(len(item) for item in self.command)
            + sum(len(path) for path in self.paths)
            + sum(len(step) for step in self.steps)
            + sum(step.text_chars() for step in self.gui)
        )
        if total > MAX_ACTION_CHARS:
            raise ValueError(
                f"A ProposedAction carries at most {MAX_ACTION_CHARS} characters across summary, "
                f"diff, command, paths, steps and GUI steps, got {total}."
            )
        return self
