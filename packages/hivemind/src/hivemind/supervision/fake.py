"""Define FakeSupervisor: a scripted Supervisor for tests, demos and other supervisors' tests.

A hand-written fake that implements `Supervisor` honestly (codingrules section 14.4), built from a
fixed set of children and canned telemetry/views rather than a real Warden or Queen. Every
`intervene` call that targets a known child is recorded, in order, on `interventions` -- never
`history` or `messages` (codingrules section 4, `scripts/check_no_transcripts.py`), because
those names are reserved for a growing conversation transcript and this is an audit list of calls
made, the same shape as `FakeLLMProvider.calls`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by tests of anything that takes a
    `Supervisor` (a Warden's or the Queen's own tests, once those subsystems exist) and by this
    package's own tests. Calls into `hivemind.supervision.errors`, `hivemind.supervision.
    intervention` and `hivemind.supervision.supervisor` only.

Key invariants:
    - children(), telemetry() and inspect() never see a call recorded; only intervene() records,
      matching what a real Supervisor's caller actually needs to assert against.
    - telemetry(), inspect() and intervene() all raise UnknownChildError for a child id that is
      not among the ChildRefs this instance was built with, exactly like a real implementation.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes over mocks" and where a fake lives.
    - hivemind.supervision.supervisor for the Supervisor protocol this class implements.
    - hivemind.llm.fake (once phase 3's A2 dispatch lands it) for FakeLLMProvider.calls, the
      sibling shape this module's `interventions` list follows.
"""

from __future__ import annotations

from hivemind.supervision.errors import UnknownChildError
from hivemind.supervision.intervention import Intervention
from hivemind.supervision.supervisor import ChildRef
from waggle.messages.supervision import CompactView, ContextTelemetry

__all__ = ["FakeSupervisor"]


class FakeSupervisor:
    """A Supervisor with scripted children, telemetry and views, and a recorded intervention log."""

    def __init__(
        self,
        children: tuple[ChildRef, ...] = (),
        telemetry: dict[str, ContextTelemetry] | None = None,
        views: dict[str, CompactView] | None = None,
    ) -> None:
        """Build a FakeSupervisor.

        Args:
            children: The ChildRefs `children()` returns; also what `telemetry`/`inspect`/
                `intervene` check a child id against.
            telemetry: Canned ContextTelemetry per child id, for `telemetry()`. A child with no
                entry here still raises UnknownChildError only if it is also absent from
                `children`; present in `children` but absent here raises KeyError instead, since
                the fake was built inconsistently by its own test.
            views: Canned CompactView per child id, for `inspect()`. Same rule as `telemetry`.
        """
        self._children = children
        self._telemetry = telemetry if telemetry is not None else {}
        self._views = views if views is not None else {}
        # Every intervene() call that reaches a known child, in order: (child, intervention).
        self.interventions: list[tuple[str, Intervention]] = []

    async def children(self) -> tuple[ChildRef, ...]:
        """Return the ChildRefs this fake was built with."""
        return self._children

    async def telemetry(self, child: str) -> ContextTelemetry:
        """Return `child`'s canned ContextTelemetry.

        Args:
            child: A child id from `children()`.

        Returns:
            The ContextTelemetry given for `child` at construction.

        Raises:
            UnknownChildError: `child` is not among this fake's children.
        """
        self._require_known_child(child)
        return self._telemetry[child]

    async def inspect(self, child: str) -> CompactView:
        """Return `child`'s canned CompactView.

        Args:
            child: A child id from `children()`.

        Returns:
            The CompactView given for `child` at construction.

        Raises:
            UnknownChildError: `child` is not among this fake's children.
        """
        self._require_known_child(child)
        return self._views[child]

    async def intervene(self, child: str, intervention: Intervention) -> None:
        """Record `(child, intervention)` on `interventions`.

        Args:
            child: A child id from `children()`.
            intervention: The lever pulled.

        Raises:
            UnknownChildError: `child` is not among this fake's children.
        """
        self._require_known_child(child)
        self.interventions.append((child, intervention))

    def _require_known_child(self, child: str) -> None:
        """Raise UnknownChildError unless `child` is one of this fake's ChildRefs."""
        if not any(ref.id == child for ref in self._children):
            raise UnknownChildError(child)
