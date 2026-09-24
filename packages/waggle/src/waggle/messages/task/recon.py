"""Define ScoutReport: what a Scout found, carried up on task.result and down on task.assign.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Scout
(roadmap step 6.10) is a Worker role that does cheap, strictly budgeted reconnaissance before the
Queen (the central orchestrator) commits Foragers (the Worker role that drives a GUI or a browser
to gather or act) to a goal. Its findings travel as a ``ScoutReport``: on the Scout's own
``task.result`` up to the Queen, and on each dependent task's ``task.assign`` down to the Forager
that works it, so what the Scout learned is in the Forager's brief rather than rediscovered. The
report is deliberately small and structured: whether the Scout recommends going ahead, one
paragraph of summary, and short lists of findings, suggested steps, risks and where the work
happens. It is prose a model wrote, so every reader treats it as untrusted input. Every bound is a
named constant here; the number, not the name, is normative. ``SCOUT_REPORT_FILE`` names the file
a Scout writes its report to, which its task's acceptance checks.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.task.reports (TaskResult)
    and waggle.messages.task.assignment (TaskAssign); built by hivemind's Scout role and read by
    the Queen's dispatcher and the Forager's prompt. Calls into waggle.messages.base only.

Key invariants:
    - Every list and every string is bounded, so a report always fits a frame beside the rest of
      its message.
    - The model is frozen and forbids extras through VALUE_MODEL_CONFIG.

See Also:
    - docs/waggle/spec.md section 8.2 for the normative fields and bounds.
    - .claude/roadmap.md step 6.10 for the Scout role.
    - waggle.messages.task.reports for TaskResult, and waggle.messages.task.assignment for
      TaskAssign, the two messages carrying this model.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from waggle.messages.base import VALUE_MODEL_CONFIG

MAX_RECON_SUMMARY_CHARS = 1_000  # One paragraph; the lists below carry the detail.
MAX_RECON_ITEM_CHARS = 500  # One finding, step, risk or target: a sentence or two, never a page.
MAX_RECON_FINDINGS = 16  # What a strictly budgeted look around can honestly establish.
MAX_RECON_STEPS = 16  # A suggested plan longer than this is a plan, not a suggestion.
MAX_RECON_RISKS = 8  # The risks worth a Forager's attention; the rest is noise.
MAX_RECON_TARGETS = 8  # Where the work happens: URLs, window titles, application names.
MAX_RECON_REPORTS = 4  # Scout reports one task.assign carries, one per Scout it depended on.
# Where a Scout writes its report, relative to its lease's scratch: the planner makes FILE_EXISTS
# on this path a Scout task's acceptance, so the Warden verifies a report exists before it
# carries one up. One name shared by the planner and the Scout role, so they cannot drift.
SCOUT_REPORT_FILE = "scout-report.json"

__all__ = [
    "MAX_RECON_FINDINGS",
    "MAX_RECON_ITEM_CHARS",
    "MAX_RECON_REPORTS",
    "MAX_RECON_RISKS",
    "MAX_RECON_STEPS",
    "MAX_RECON_SUMMARY_CHARS",
    "MAX_RECON_TARGETS",
    "SCOUT_REPORT_FILE",
    "ScoutReport",
]

_Item = Annotated[str, Field(min_length=1, max_length=MAX_RECON_ITEM_CHARS)]


class ScoutReport(BaseModel):
    """What a Scout found: whether to go ahead, a summary, and bounded lists of detail."""

    model_config = VALUE_MODEL_CONFIG

    feasible: bool = Field(
        description="Whether the Scout recommends committing Foragers to the work as planned; "
        "False holds the dependent tasks back with this report's summary as the reason."
    )
    summary: str = Field(
        min_length=1,
        max_length=MAX_RECON_SUMMARY_CHARS,
        description="One paragraph: what the Scout looked at and what it concluded.",
    )
    findings: tuple[_Item, ...] = Field(
        default=(), max_length=MAX_RECON_FINDINGS, description="Facts the Scout established."
    )
    suggested_steps: tuple[_Item, ...] = Field(
        default=(),
        max_length=MAX_RECON_STEPS,
        description="How the Scout would do the work, in order; advice, never an order.",
    )
    risks: tuple[_Item, ...] = Field(
        default=(), max_length=MAX_RECON_RISKS, description="What could go wrong, and where."
    )
    targets: tuple[_Item, ...] = Field(
        default=(),
        max_length=MAX_RECON_TARGETS,
        description="Where the work happens: URLs, window titles or application names.",
    )
