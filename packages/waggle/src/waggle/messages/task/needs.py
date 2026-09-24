"""Define ExoskeletonNeed: what peripherals a task needs, carried to the Warden on task.assign.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A task's
needs (hivemind's ``cell.TaskNeeds``) decide where it is placed, but the Warden (the always-on
supervisor of the Cell a task lands on) must also know them, to attach the Exoskeleton (the
optional display, input, audio and browser peripherals of a Cell) and to grant the network scopes
the task needs. Until protocol 1.6 the Warden rebuilt a task's needs from its Tempo alone, so both
were lost in transit (ADR-0031). ``ExoskeletonNeed`` is the wire form of the Exoskeleton half:
present on a ``task.assign`` exactly when the task needs one, saying whether the browser fast path
alone is enough and whether the task needs audio. The network scopes ride beside it as plain
strings, bounded by the constants here. Every bound is a named constant; the number, not the
name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.task.assignment, whose
    TaskAssign carries an optional ExoskeletonNeed and the task's network scopes; built by the
    Queen's dispatcher from a task's TaskNeeds and read by a Warden's spawn logic. Calls into
    waggle.messages.base only.

Key invariants:
    - ``browser_only`` and ``audio`` are never both set: audio needs the desktop's sound server,
      which a browser-only attachment never starts.
    - The model is frozen and forbids extras through VALUE_MODEL_CONFIG.

See Also:
    - docs/waggle/spec.md section 8.2 for the normative fields and bounds.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why needs travel.
    - waggle.messages.task.assignment for TaskAssign, the one message carrying this model.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from waggle.messages.base import VALUE_MODEL_CONFIG

MAX_TASK_NETWORK_SCOPES = 32  # Mirrors hivemind.cell.needs.MAX_NETWORK_SCOPES: one task's reach.
MAX_TASK_SCOPE_CHARS = 253  # RFC 1035's hostname limit, the longest one scope entry can be.

__all__ = ["MAX_TASK_NETWORK_SCOPES", "MAX_TASK_SCOPE_CHARS", "ExoskeletonNeed", "NetworkScope"]

# One outbound destination a task's capability set must allow reaching: a hostname, never empty.
NetworkScope = Annotated[str, Field(min_length=1, max_length=MAX_TASK_SCOPE_CHARS)]


class ExoskeletonNeed(BaseModel):
    """What Exoskeleton a task needs: a full desktop or the browser alone, and whether audio.

    Present on a task.assign exactly when the task needs an Exoskeleton at all; a terminal-only
    task carries None instead.
    """

    model_config = VALUE_MODEL_CONFIG

    browser_only: bool = Field(
        default=False,
        description="Whether the browser fast path alone is enough: no desktop display, pointer "
        "or keyboard beyond the page, and no audio.",
    )
    audio: bool = Field(
        default=False, description="Whether the task needs to hear or speak through the Cell."
    )

    @model_validator(mode="after")
    def _audio_needs_a_desktop(self) -> ExoskeletonNeed:
        """Reject browser_only together with audio."""
        # Audio runs through the desktop's own sound server, which a browser-only attachment
        # never starts; asking for both would promise a peripheral no attach plan can deliver.
        if self.browser_only and self.audio:
            raise ValueError(
                "An ExoskeletonNeed cannot be browser_only and need audio: audio needs the "
                "desktop's sound server."
            )
        return self
