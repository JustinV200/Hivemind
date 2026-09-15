"""Tests for hivemind.workers.roles.drone.prompt.brief_for: the Drone's one user turn.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/drone/prompt.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.drone.prompt for the module under test.
"""

from __future__ import annotations

from builders.llm import make_bound
from builders.workers import make_assignment, make_context

from hivemind.workers.roles.drone.prompt import brief_for
from waggle.messages import Postcondition, PostconditionKind


def test_brief_leads_with_the_objective_then_every_criterion_in_plain_words() -> None:
    ctx = make_context(bound=make_bound())
    assignment = make_assignment(
        objective="Write a short self-description.",
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS,
                subject="drone_message.txt",
                argv=(),
                expected=None,
            ),
            Postcondition(
                kind=PostconditionKind.COMMAND_EXITS_ZERO,
                subject="check",
                argv=("python", "-c", "pass"),
                expected=None,
            ),
        ),
    )

    text = brief_for(assignment, ctx.cell)

    # The planner names the file only in the criterion; a bee that never saw it wrote elsewhere.
    assert text.startswith("Write a short self-description.")
    assert "a file exists at drone_message.txt" in text
    assert "the command `python -c pass` exits with code 0" in text


def test_brief_names_the_cell_os_shell_and_command_rules() -> None:
    ctx = make_context(bound=make_bound())
    assignment = make_assignment(objective="Do the thing.")

    text = brief_for(assignment, ctx.cell)

    caps = ctx.cell.capabilities
    assert caps.os.value in text
    assert caps.shell in text
    assert "without a shell" in text
