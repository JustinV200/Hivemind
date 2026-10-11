"""Run a Virtual Cell's Warden in process with its own real provider registry, not a scripted fake.

`builders.virtual_cells.ContainerSpawningFakeCellBackend` stands in for "the container runtime" by
running a real in-Cell Warden per provisioned Cell, but it then scripts that Warden's model door
with a `FakeLLMProvider`. The offline proof needs the opposite: the Cell's Warden builds its
registry from the `HIVEMIND_PROVIDERS` table the Queen sent it (the manifest's `[llm.providers]`,
loopback kept because the fake backend has no gateway host to rewrite it to) and its Drone calls the
scripted server over a real socket, so the Cell's own Fanner records the `llm.call` events. This
subclass changes that one thing: it starts the in-Cell Warden without a model script, and closes the
connection pool the Warden's provider opened once it ends (the in-Cell entry point leaves that to
the process exit a real container has).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests/e2e/offline/test_offline_virtual_cell.py`. Calls into `builders.virtual_cells` and
    `hivemind.cli.in_cell.main`.

Key invariants:
    - The Cell's scratch root is a temporary directory of its own, removed when the Warden ends
      (an in-process "container" has no `/var/lib/hivemind`).
    - The Warden runs on the real clock: it talks to the Queen over a real loopback WebSocket.

See Also:
    - tests/builders/virtual_cells.py for the backend this specialises.
"""

from __future__ import annotations

import shutil
import tempfile

import pytest
from builders.virtual_cells import ContainerSpawningFakeCellBackend

from hivemind.cli.in_cell.main import run_in_cell_warden
from hivemind.hive.backends.bootstrap import CellBootstrap
from hivemind.llm import LLMProvider
from hivemind.wardens.deps import WardenDeps
from waggle.clock import SystemClock
from waggle.ids import CellId

__all__ = ["ProviderTableCellBackend", "use_provider_table_cells"]


class ProviderTableCellBackend(ContainerSpawningFakeCellBackend):
    """A container-spawning fake backend whose in-Cell Warden keeps its real provider registry."""

    async def _run_container(self, cell_id: CellId, bootstrap: CellBootstrap) -> None:
        """Run the in-Cell Warden for `cell_id` as the real entry point does, with no script."""
        scratch_root = tempfile.mkdtemp(prefix=f"hivemind-{cell_id}-")
        environ = {**bootstrap.environment(), "HIVEMIND_SCRATCH_ROOT": scratch_root}
        opened: list[LLMProvider] = []

        def _remember(deps: WardenDeps) -> None:
            opened.append(deps.bound.provider)

        try:
            await run_in_cell_warden(environ, SystemClock(), on_deps_built=_remember)
        finally:
            shutil.rmtree(scratch_root, ignore_errors=True)
            # A real Cell's process exits and the operating system closes its sockets; this
            # in-process stand-in lives on, and the in-Cell Warden never closes its registry, so a
            # connection pool left open would be reported as a leak when the test run ends.
            for provider in opened:
                await provider.aclose()


def use_provider_table_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `[virtual_cells] backend = "fake"` build `ProviderTableCellBackend`."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", ProviderTableCellBackend
    )
