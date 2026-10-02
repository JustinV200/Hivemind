"""Decide whether a command the Capping gate runs itself may run on its Cell, and record a refusal.

ADR-0060 lets a command run below FULL access only on a Cell whose processes are contained. A
Worker's own `run_command` asks the Guard before it proposes anything, but the gate also runs
commands of its own: a `COMMAND_EXITS_ZERO` or `TEST_PASSES` postcondition after applying a
proposal, and the same kinds as a task's acceptance criteria, which the Warden checks on its own
session. Those commands come from a model (the planner wrote the criteria, the Worker the
postconditions), so on a Cell that may not run commands they must not run either. `CommandAdmission`
is that one question, asked once per command: it reads the Cell's access level and containment
straight off the `Cell` the gate already holds (never its kind), and, given the Warden's `Enforcer`
and principal (`CommandGuard`), asks the Guard's access-level ceiling alone, which records a
refusal as `guard.denied` under the same rule a Worker's own command meets
(`guard.access_level.<level>`). Without a guard it still refuses, from the same table, unrecorded.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Built
    by a Warden (`hivemind.wardens.spawn` for a gate, `hivemind.wardens.ticks.results` for
    acceptance); read by `hivemind.supervision.capping.postconditions.check_postcondition`. Calls
    into `hivemind.cell` (Cell), `hivemind.guard` (admits, the policy request shapes, Enforcer) and
    waggle only.

Key invariants:
    - A command is admitted only when the Cell's access level permits `exec` for this Cell's
      containment (`hivemind.guard.admits`); a FULL Cell, and a contained Cell at SCRATCH, always
      are; a READ_ONLY Cell never is.
    - With a guard, a refusal is on the trail before `refusal` returns it; nothing here ever runs
      the command.

See Also:
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md.
    - hivemind.guard.enforcer.Enforcer.check_access_level for the recorded check.
    - hivemind.supervision.capping.postconditions for the one caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.cell import Cell
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    EnforcementPoint,
    Enforcer,
    PolicyContext,
    PolicyRequest,
    PrincipalRef,
    admits,
)
from waggle.messages.labels import PostconditionKind

# The kinds whose check runs a command on the Cell; public so the planner can tell which tasks
# need a Cell that runs commands (ADR-0060) without restating the list.
COMMAND_POSTCONDITION_KINDS: frozenset[PostconditionKind] = frozenset(
    {PostconditionKind.COMMAND_EXITS_ZERO, PostconditionKind.TEST_PASSES}
)

__all__ = ["COMMAND_POSTCONDITION_KINDS", "CommandAdmission", "CommandGuard"]


@dataclass(frozen=True, slots=True)
class CommandGuard:
    """Who the gate asks the Guard as, and through what, before it runs a command itself.

    Attributes:
        enforcer: The Warden's own Enforcer, which records a refusal as `guard.denied`.
        principal: The Warden the gate runs for: the command is its check, not the Worker's.
    """

    enforcer: Enforcer
    principal: PrincipalRef


@dataclass(frozen=True, slots=True)
class CommandAdmission:
    """Whether a command the gate runs itself may run on `cell` (module docstring).

    Attributes:
        cell: The Cell the command would run on; its access level and containment decide.
        guard: The Warden's Enforcer and principal, so a refusal is recorded; None refuses from
            the same table without a trail row (a gate wired without the Guard, in tests).
    """

    cell: Cell
    guard: CommandGuard | None = None

    async def refusal(self, argv: Sequence[str]) -> str | None:
        """Say why the command `argv` may not run on this Cell, or None when it may.

        Args:
            argv: The command as an argument list; its program names the `exec` scope.

        Returns:
            The Guard's reason when the Cell's access level refuses `exec` here (already on the
            trail when a guard is wired); None when the command may run.

        Raises:
            Whatever the trail's `record` raises: a refusal is never returned unrecorded.
        """
        level = self.cell.access_level
        contained = self.cell.capabilities.processes_contained
        if admits(level, CapabilityFamily.EXEC, processes_contained=contained):
            return None
        if self.guard is None:
            # No Enforcer to ask: the same table refuses it, said in the Guard's own terms.
            return (
                f"a command may not run on this Cell at access level {level.name} (ADR-0060); "
                "it needs a Virtual Cell, or FULL access"
            )
        request = self._request(self.guard.principal, argv)
        decision = await self.guard.enforcer.check_access_level(request)
        # admits() already said no, so the access-level ceiling refuses; read it defensively.
        return None if decision is None else decision.reason

    def _request(self, principal: PrincipalRef, argv: Sequence[str]) -> PolicyRequest:
        """Build the access-level question for `argv`: the Warden, its Cell, no held set."""
        cell = self.cell
        context = PolicyContext(
            comb_shield=cell.comb_shield,
            access_level=cell.access_level,
            processes_contained=cell.capabilities.processes_contained,
            bound_tier=cell.comb_shield,
        )
        # TOOL_INVOCATION: the command is the very action a Worker's run_command takes there.
        return PolicyRequest(
            principal=principal,
            point=EnforcementPoint.TOOL_INVOCATION,
            needed=Capability(family=CapabilityFamily.EXEC, scope=_program(argv)),
            held=CapabilitySet.empty(),
            context=context,
        )


def _program(argv: Sequence[str]) -> str:
    """Name the program `argv` runs for its `exec` scope; "*" for an empty one, naming any."""
    return argv[0] if argv and argv[0] else "*"
