"""Define PlanSchema: the JSON schema a model fills to decompose one goal into a task graph.

Roadmap step 3.18: "The planner emits `acceptance` for every subtask as a list of postconditions
plus, where nothing machine-checkable exists, a rubric for a judge." `PlannedPostcondition` is a
model-facing shape for one acceptance criterion -- the same four fields `waggle.messages.
Postcondition` carries, with that class's cross-field rules (argv only for a command kind,
`expected` required for a comparison kind) re-checked by delegating to it, so a weak model's
near-miss on those rules is a validation retry within the `hivemind.llm.ladders.structured`
degradation ladder, with the rule it broke fed back as the correction, rather than a hard
`PlannerError` after the ladder has already returned. `hivemind.queen.planner.plan` is the one
place that then converts a `PlannedPostcondition` into a real `Postcondition`. `PlannedTask`
mirrors `hivemind.brood_chamber.task.model.TaskDraft`'s own fields (key, title, objective,
acceptance, needs, clearance, depends_on, leaves), reusing `hivemind.cell.TaskNeeds` and
`hivemind.cell.HoneyClearance` directly since both are already fully defaulted, lenient shapes a
model can fill in without a second, parallel definition; `leaves` (roadmap step 5.0b) reuses
`waggle.messages.PlannedLeaving` the same way, unlike `acceptance`, because `PlannedLeaving`
already is the model-facing shape -- there is no separate wire type to convert into. `PlanSchema`
is the whole reply: one or more `PlannedTask`s, the first of which becomes the goal.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's planner
    sub-package (which MAY import `hivemind.llm`). Read by `hivemind.llm.ladders.structured.
    complete_structured` (as the schema a model's reply is validated against, given
    `hivemind.queen.planner.plan.plan_goal`'s own `context={"scratch_root": ...}` when the caller
    supplied one) and by `hivemind.queen.planner.plan._to_graph_draft` (as the value it converts).
    Calls into `hivemind.brood_chamber.task.model` (KEY_PATTERN), `hivemind.cell` (HoneyClearance,
    TaskNeeds) and `waggle.messages` (PlannedLeaving, Postcondition, PostconditionKind) only.

Key invariants:
    - `PlannedPostcondition` and `PlannedTask` are frozen and forbid extras, like every boundary
      value in this repository. `PlannedPostcondition` re-runs `Postcondition`'s own cross-field
      rules (an `argv` on a `FILE_EXISTS` criterion, a `JUDGE_RUBRIC` with no rubric) by building
      one, so such a miss is a ladder retry carrying the broken rule, not a `PlannerError`; those
      rules live in `waggle.messages.labels` only. It adds two planning-only rules on top: the
      kind must be one `hivemind.supervision.capping.CHECKABLE_KINDS` can verify today (a kind
      the gate reports as unsupported would fail every acceptance run), and a command kind must
      carry an `argv`, since Capping runs `argv` and never `subject`.
    - `PlannedTask.key` carries `TaskDraft.KEY_PATTERN` for the same reason: a key the model gets
      wrong is retried inside the ladder, not rejected by `plan.py` afterwards.
    - `PlannedTask.acceptance` never accepts an empty tuple (`min_length=1`): roadmap step 3.18's
      own rule, "every subtask carries at least one acceptance postcondition", is enforced at the
      schema the model fills in, not only after the fact.
    - `PlannedTask.leaves` defaults to empty; each entry's own shape (absolute or `~`-rooted, no
      bare root/drive, no `..` segment) is `PlannedLeaving`'s own validator (waggle, so it holds
      regardless of who builds one). This module adds the one planning-only rule `PlannedLeaving`
      cannot check for itself -- a pattern inside the Hive Stand's own scratch -- by reading
      `ValidationInfo.context["scratch_root"]`; that key is only ever set by `plan_goal`, so a
      `PlannedTask` built directly (every test that does not exercise the ladder) skips the rule
      rather than raising on a context it was never given.
    - `PlanSchema.tasks` never accepts an empty tuple either: a plan with no tasks decomposes
      nothing.

See Also:
    - .claude/roadmap.md step 3.18 for "the planner emits acceptance for every subtask".
    - .claude/roadmap.md step 3.20 for this module's own roadmap bullet.
    - .claude/roadmap.md step 5.0b for "the plan declares what stays".
    - waggle.messages.labels for Postcondition, PostconditionKind and PlannedLeaving.
    - hivemind.brood_chamber.task.model for TaskDraft and TaskGraphDraft, what `plan.py` converts
      this schema's values into.
    - hivemind.queen.planner.plan for plan_goal, the one caller of PlanSchema.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from hivemind.brood_chamber.task.model import KEY_PATTERN
from hivemind.cell import HoneyClearance, TaskNeeds
from hivemind.supervision.capping import CHECKABLE_KINDS
from waggle.messages import PlannedLeaving, Postcondition, PostconditionKind
from waggle.messages.base import MAX_PATH_CHARS
from waggle.messages.labels import MAX_ARGV_ITEM_CHARS, MAX_ARGV_ITEMS, MAX_EXPECTED_CHARS

MAX_KEY_CHARS = 64  # A short, url-safe draft key; matches TaskDraft.key's own scale.
MIN_ACCEPTANCE_ITEMS = 1  # roadmap 3.18: every subtask carries at least one acceptance criterion.
MAX_ACCEPTANCE_ITEMS = 32  # Matches TaskDraft.acceptance's own ceiling.
MAX_TITLE_CHARS = 200  # Matches TaskDraft.title's own scale.
MAX_OBJECTIVE_CHARS = 8_000  # Matches TaskDraft.objective's own scale.
MAX_DEPENDS_ON = 64  # Matches TaskDraft.depends_on's own scale.
MAX_LEAVES_ITEMS = 16  # roadmap 5.0b: matches TaskDraft.leaves's own ceiling.
MIN_PLAN_TASKS = 1  # A plan that decomposes nothing is not a plan.
MAX_PLAN_TASKS = 64  # A generous single goal's worth of subtasks.
# The kinds whose Capping check runs `argv` (supervision.capping.postconditions); mirrors
# waggle.messages.labels' own private set, which that module deliberately does not export.
_COMMAND_KINDS = frozenset({PostconditionKind.COMMAND_EXITS_ZERO, PostconditionKind.TEST_PASSES})

__all__ = [
    "MAX_ACCEPTANCE_ITEMS",
    "MAX_DEPENDS_ON",
    "MAX_KEY_CHARS",
    "MAX_LEAVES_ITEMS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_PLAN_TASKS",
    "MAX_TITLE_CHARS",
    "MIN_ACCEPTANCE_ITEMS",
    "MIN_PLAN_TASKS",
    "PlanSchema",
    "PlannedPostcondition",
    "PlannedTask",
]


class PlannedPostcondition(BaseModel):
    """One acceptance criterion, as a model fills it in; converted to a real Postcondition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PostconditionKind = Field(
        description="What is asserted. Only FILE_EXISTS/FILE_ABSENT (the path in `subject`) and "
        "COMMAND_EXITS_ZERO/TEST_PASSES (the command in `argv`, never in `subject`, which is only "
        "a short label) can be checked by this Hive today; HTTP_STATUS, ELEMENT_TEXT and "
        "JUDGE_RUBRIC are refused. A result meant for a human is written to a file and checked "
        "with FILE_EXISTS."
    )
    subject: str = Field(
        max_length=MAX_PATH_CHARS,
        description="The path, command target or a short label for JUDGE_RUBRIC.",
    )
    argv: tuple[Annotated[str, Field(max_length=MAX_ARGV_ITEM_CHARS)], ...] = Field(
        default=(),
        max_length=MAX_ARGV_ITEMS,
        description="The command as an argument list; only for COMMAND_EXITS_ZERO/TEST_PASSES, "
        "and empty for every other kind.",
    )
    expected: str | None = Field(
        default=None,
        max_length=MAX_EXPECTED_CHARS,
        description="Required for HTTP_STATUS (the status code), ELEMENT_TEXT (the text) and "
        "JUDGE_RUBRIC (one specific, checkable question); None for every other kind.",
    )

    @model_validator(mode="after")
    def _obeys_postcondition_rules(self) -> PlannedPostcondition:
        """Re-run Postcondition's cross-field rules; a miss is a ladder retry, not an error."""
        # A kind the Capping gate can only report as "unsupported in v0" would make every claim
        # fail acceptance, retry, and fail again; refusing it here (with the way out) turns that
        # into one ladder retry at planning time instead. CHECKABLE_KINDS is the gate's own list.
        if self.kind not in CHECKABLE_KINDS:
            checkable = ", ".join(sorted(kind.value for kind in CHECKABLE_KINDS))
            raise ValueError(
                f"A {self.kind.value} postcondition cannot be checked by this Hive yet; use one of "
                f"{checkable}. For a result a human reads, have the subtask write it to a file and "
                "check FILE_EXISTS on that path."
            )
        # Building the real value is the check: its validators raise the one message that names
        # the rule broken, which the structured ladder then hands back to the model as its
        # correction. The instance is discarded; plan.py builds the one that is kept.
        Postcondition(kind=self.kind, subject=self.subject, argv=self.argv, expected=self.expected)
        # One planning-only rule on top: a command criterion with no command can never be checked
        # (Capping runs `argv`, not `subject`), so a model that wrote the command line into
        # `subject` is corrected here instead of certifying nothing later.
        if self.kind in _COMMAND_KINDS and not self.argv:
            raise ValueError(
                f"A {self.kind.value} postcondition requires a non-empty `argv`: the command as "
                "an argument list, never a shell string in `subject`."
            )
        return self


class PlannedTask(BaseModel):
    """One subtask, as a model fills it in; converted to a hivemind.brood_chamber.TaskDraft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The same pattern TaskDraft.key enforces, so a key the model gets wrong (an uppercase "T1",
    # say) fails inside the structured-output ladder, where it is retried, rather than in plan.py's
    # conversion afterwards; the description spells the format out for a model that ignores
    # `pattern` in a schema.
    key: str = Field(
        pattern=KEY_PATTERN,
        max_length=MAX_KEY_CHARS,
        description="This subtask's own short, unique name in the plan: lowercase letters, digits, "
        "'-' or '_' only, starting with a letter or digit, e.g. 'fetch-source' or 'task_1'.",
    )
    title: str = Field(max_length=MAX_TITLE_CHARS, description="A one-line summary.")
    objective: str = Field(max_length=MAX_OBJECTIVE_CHARS, description="The full brief.")
    acceptance: tuple[PlannedPostcondition, ...] = Field(
        min_length=MIN_ACCEPTANCE_ITEMS,
        max_length=MAX_ACCEPTANCE_ITEMS,
        description="How this subtask's own Warden will know it succeeded.",
    )
    needs: TaskNeeds = Field(
        default_factory=TaskNeeds, description="What this subtask requires from its Cell."
    )
    clearance: HoneyClearance = Field(
        default=HoneyClearance.C1, description="This subtask's data-sensitivity label."
    )
    depends_on: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_DEPENDS_ON,
        description="Keys of other subtasks in this same plan that must succeed first, spelled "
        "exactly as their own `key` fields.",
    )
    leaves: tuple[PlannedLeaving, ...] = Field(
        default=(),
        max_length=MAX_LEAVES_ITEMS,
        description="Only what the goal itself asks to remain on this subtask's Cell once its "
        "lease is released ('install X', 'set up a project in Y') -- never working files, logs "
        "or anything scratch could hold instead. Empty unless the goal truly asks for it.",
    )

    @model_validator(mode="after")
    def _leaves_stay_outside_scratch(self, info: ValidationInfo) -> PlannedTask:
        """Reject a declared leaving whose pattern falls inside the Hive Stand's own scratch.

        Scratch is removed wholesale on every lease's release regardless of what a plan declares
        (roadmap 5.0 preamble), so a leaving inside it could never actually persist; catching that
        here, inside the ladder, lets the model correct it instead of the declaration silently
        doing nothing at run time. `info.context["scratch_root"]` is only set by `plan_goal`
        (module docstring); every other caller (every test that builds a PlannedTask directly)
        skips this rule rather than raising on a context it was never given.
        """
        context = info.context or {}
        scratch_root = context.get("scratch_root")
        if scratch_root is None:
            return self  # No scratch_root in hand (module docstring): nothing to check against.
        for leaving in self.leaves:
            if _pattern_reaches_into_scratch(leaving.pattern, scratch_root):
                raise ValueError(
                    f"PlannedTask.leaves pattern {leaving.pattern!r} is inside the Hive Stand's "
                    f"own scratch ({scratch_root}); scratch is removed wholesale on release, so "
                    "nothing there can be declared to stay -- move the work outside scratch or "
                    "drop this leaving."
                )
        return self


class PlanSchema(BaseModel):
    """The model's whole reply: one or more subtasks; the first becomes the goal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tasks: tuple[PlannedTask, ...] = Field(
        min_length=MIN_PLAN_TASKS,
        max_length=MAX_PLAN_TASKS,
        description="The subtasks, goal (first) onward.",
    )


def _pattern_reaches_into_scratch(pattern: str, scratch_root: Path) -> bool:
    """Return whether `pattern` names a path at or inside `scratch_root`.

    `scratch_root` is a real, resolved filesystem Path (`hivemind.manifest`'s own
    `[hive_stand] scratch_root`, resolved -- `plan_goal`'s own docstring), so its concrete class
    already says which flavour to parse `pattern` with: a `pathlib.Path` built on this host IS a
    `PureWindowsPath` or a `PurePosixPath` already (never constructed here from untrusted text,
    only inspected -- codingrules "never the host's Path" is about building a path from text, not
    reading one the manifest already resolved). A `~`-rooted pattern is skipped: its concrete
    location depends on the Cell that eventually runs the task, not on this Hive Stand's own
    scratch_root, so it can never be compared here.

    Args:
        pattern: A `PlannedLeaving.pattern` already known well-formed (that class's own
            validator has already run, since nested models validate before the outer one).
        scratch_root: The Hive Stand's own resolved scratch root.

    Returns:
        True when `pattern` names `scratch_root` itself or anything nested under it.
    """
    if pattern.startswith("~"):
        return False  # Cannot be compared to a Hive-Stand-specific root (docstring).
    flavor = PureWindowsPath if isinstance(scratch_root, PureWindowsPath) else PurePosixPath
    return flavor(pattern).is_relative_to(scratch_root)
