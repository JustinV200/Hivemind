"""Run real in-Cell Wardens for the Guard Bee's end-to-end tests: lured, lingering, quick to ship.

`LuredCells` is the container-spawning fake backend (one real in-Cell Warden per Virtual Cell)
with every container's Drone scripted into the injection correlation (`lure_script`), and
`LingeringCells` the same with every Drone on one long command, so its bee is still at work when
a test acts on it. `ship_on_every_call` keeps every in-Cell Warden's deps as the backend builds
them, and ships the Cell's trail segment before each of its Drone's model calls, as the Warden's
next heartbeat would (every 15 s in a Cell): a Cell's records reach the Queen at once.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`
    by the Guard Bee's end-to-end tests over Virtual and Night Veil Cells.

Key invariants:
    - A Warden's own heartbeat still ships as it always does; this only ships sooner as well.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import builders.virtual_cells as virtual_cells_builders
import pytest
from builders.guard_bee.lure import LURE_CALLS, lure_script
from builders.virtual_cells import ContainerSpawningFakeCellBackend

from hivemind.cli.in_cell.main import run_in_cell_warden as real_run_in_cell_warden
from hivemind.forage import ModelSlot
from hivemind.hive import BackendCapabilities
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.fake import ReadinessGateExpect
from hivemind.llm import LLMRequest, LLMResponse, ToolCall, text_response, tool_call_response
from hivemind.wardens.deps import WardenDeps
from waggle.clock import Clock

__all__ = ["LingeringCells", "LuredCells", "linger_script", "ship_on_every_call"]


def linger_script() -> tuple[LLMResponse, ...]:
    """A container's Drone that only starts the lure's long command: at work until stopped."""
    [(call_id, name, arguments)] = [call for call in LURE_CALLS if call[0] == "linger"]
    call = tool_call_response(ToolCall(id=call_id, name=name, arguments=arguments))
    return (call, text_response("Never reached: the scenario ends first."))


class LuredCells(ContainerSpawningFakeCellBackend):
    """The container-spawning fake backend, every container's Drone scripted with the lure."""

    def __init__(
        self,
        clock: Clock,
        capabilities: BackendCapabilities | None = None,
        *,
        endpoint: QueenEndpoint | None = None,
        gate: ReadinessGateExpect | None = None,
    ) -> None:
        """Build the backend as the composition root does; see `FakeCellBackend.__init__`."""
        super().__init__(clock, capabilities, endpoint=endpoint, gate=gate, script=lure_script)


class LingeringCells(ContainerSpawningFakeCellBackend):
    """The container-spawning fake backend, every container's Drone lingering at one command."""

    def __init__(
        self,
        clock: Clock,
        capabilities: BackendCapabilities | None = None,
        *,
        endpoint: QueenEndpoint | None = None,
        gate: ReadinessGateExpect | None = None,
    ) -> None:
        """Build the backend as the composition root does; see `FakeCellBackend.__init__`."""
        super().__init__(clock, capabilities, endpoint=endpoint, gate=gate, script=linger_script)


def ship_on_every_call(monkeypatch: pytest.MonkeyPatch) -> list[WardenDeps]:
    """Keep every in-Cell Warden's deps, and ship its trail before each of its Drone's calls.

    Args:
        monkeypatch: The test's fixture; the backend's own `run_in_cell_warden` is wrapped.

    Returns:
        The list every in-Cell Warden's deps are appended to as its container starts.
    """
    built: list[WardenDeps] = []

    async def run_in_cell_warden(
        environ: Mapping[str, str],
        clock: Clock,
        *,
        on_deps_built: Callable[[WardenDeps], None] | None = None,
    ) -> None:
        def watch(deps: WardenDeps) -> None:
            if on_deps_built is not None:
                on_deps_built(deps)  # The backend's own script first, as it runs alone.
            built.append(deps)
            _ship_before_calls(deps, monkeypatch)

        await real_run_in_cell_warden(environ, clock, on_deps_built=watch)

    monkeypatch.setattr(virtual_cells_builders, "run_in_cell_warden", run_in_cell_warden)
    return built


def _ship_before_calls(deps: WardenDeps, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ship `deps`' trail before each Worker call its Cell's shared provider answers."""
    provider, trail_sync = deps.bound.provider, deps.trail_sync
    assert trail_sync is not None  # Every in-Cell Warden ships its own trail.
    real_complete = provider.complete

    async def complete(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.WORKER:
            await trail_sync.sync()
        return await real_complete(request)

    monkeypatch.setattr(provider, "complete", complete)
