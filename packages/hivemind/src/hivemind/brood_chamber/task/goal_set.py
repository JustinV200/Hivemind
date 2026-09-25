"""Define GoalCapabilities: the capability set a goal's submitter set, as every task stores it.

A goal carries a ceiling (ADR-0039, roadmap step 10.3): the capability set of the principal that
submitted it, an approved device's set through the Hive Entrance, and every task planned from the
goal carries that set so placement and the Warden can narrow what the task may do to it. A task
stores it as sorted, de-duplicated capability strings (`family` or `family:scope`), the same form
`hivemind.guard.CapabilitySet.as_strings` writes and `waggle.messages.task.TaskAssign.
capabilities` carries, so it round-trips through the Brood Chamber's JSON body unchanged. `None`
is meaningful (codingrules section 9): the goal came from the operator's own local path (`hive
run`, `hive tasks submit`), which has no device ceiling; an empty tuple is a goal allowed nothing.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber.task`.
    Read by `hivemind.brood_chamber.task.model` (TaskSpec, TaskDraft). Calls into
    `hivemind.guard` (CapabilitySet, InvalidCapabilityError) to prove each string parses.

Key invariants:
    - A stored goal set is always canonical: every string parses as a capability, the tuple is
      sorted and holds no duplicate, so two equal sets always serialise to the same JSON.
    - The bounds mirror the wire's (`waggle.messages.task.assignment.MAX_CAPABILITIES` and
      `MAX_CAPABILITY_CHARS`), so a stored set always fits the task.assign that carries it.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "A goal carries a
      ceiling".
    - hivemind.guard.capabilities for the grammar every string must parse under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field

from hivemind.guard import CapabilitySet, InvalidCapabilityError

MAX_GOAL_CAPABILITIES = 64  # Mirrors waggle.messages.task.assignment.MAX_CAPABILITIES.
MAX_CAPABILITY_CHARS = 1_024  # Mirrors waggle.messages.task.assignment.MAX_CAPABILITY_CHARS.

__all__ = [
    "MAX_CAPABILITY_CHARS",
    "MAX_GOAL_CAPABILITIES",
    "GoalCapabilities",
    "canonical_goal_set",
]


def canonical_goal_set(value: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Parse every capability string and return them sorted and unique; None passes through.

    Args:
        value: The strings a submitter's set was written as, or None for no ceiling.

    Returns:
        The same set in canonical form (`CapabilitySet.as_strings`), or None unchanged.

    Raises:
        ValueError: A string is not a capability; pydantic reports it as a validation error.
    """
    if value is None:
        return None  # The operator's own local path: no ceiling to canonicalise.
    try:
        return CapabilitySet.parse(*value).as_strings()
    except InvalidCapabilityError as exc:
        # Re-raised as ValueError so a TaskSpec built from bad JSON fails validation cleanly.
        raise ValueError(f"not a goal capability set: {exc}") from exc


# One capability string, bounded like the wire's own, and the whole optional, canonical set.
_CapabilityString = Annotated[str, Field(min_length=1, max_length=MAX_CAPABILITY_CHARS)]
_GoalStrings = Annotated[tuple[_CapabilityString, ...], Field(max_length=MAX_GOAL_CAPABILITIES)]
GoalCapabilities = Annotated[_GoalStrings | None, AfterValidator(canonical_goal_set)]
