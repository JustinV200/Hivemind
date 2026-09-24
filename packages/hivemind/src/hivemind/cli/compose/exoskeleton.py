"""Wire the Exoskeleton into a Warden's deps: its screen, browser, flight recorder and ears.

Roadmap steps 6.4-6.6, composition-root half. A Warden equips a sub-bee whose task needs an
Exoskeleton (a display, input, audio or a browser on its Cell) with four `hivemind.wardens.
WardenDeps` fields that default to "nothing" until a composition root wires them:
`exoskeleton_config` (the screen attach starts, its readiness budget, whether Chromium keeps its
own sandbox), `browser_launcher` (a `ChromiumLauncher`, only where the `hivemind[browser]` extra
that drives it is installed), `recording_store` (where the flight recorder keeps each recording)
and `ears` (the transcriber slot's chain behind a transcription gate, for the `listen` tool). This
module builds all four for both Wardens: the Hive Stand's, from the manifest's `[exoskeleton]`
section, the Hive's database file and its Fanner (the seat meter every model call passes
through); and the in-Cell Warden's (`hivemind.cli.in_cell.deps`), from defaults, since a Virtual
Cell reads no manifest. It also opens the Hive Stand's recording store with the retention sweep
applied, and registers that store as a Night Veil side channel, so a Night Veil Cell's recordings
are purged with the Cell (codingrules section 12's Night Veil boundary).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.deps` (`open_default_stores`, `build_warden_deps`) and by
    `hivemind.cli.in_cell.deps`. Calls into `hivemind.cli.stores` (open_recordings),
    `hivemind.common.logging`, `hivemind.exoskeleton` (config, geometry, browser launcher,
    recorder stores), `hivemind.forage` (Tempo), `hivemind.llm` (Ears, the transcription gates),
    `hivemind.manifest.schema` (ExoskeletonSection), `hivemind.pheromone` (SideChannelPurger),
    `hivemind.wardens` (WardenDeps) and waggle.

Key invariants:
    - A Hive without the browser extra still composes: no launcher is built, and attach refuses a
      browser need up front (EXOSKELETON_FAILED) instead of failing mid-launch.
    - Chromium's own sandbox is off only inside a Virtual Cell (the Cell is the sandbox) or on a
      Hive Stand running as root (Chromium cannot start sandboxed there); no setting turns it off.
    - A transcriber binding that cannot hear leaves `ears` None, so the `listen` tool is simply
      not offered, rather than failing the whole composition.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for attach and the launcher.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for the
      recorder, its retention and its Night Veil purge.
    - hivemind.wardens.spawn.equip for how a Warden uses these four fields.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hivemind.cli.stores import open_recordings
from hivemind.common.logging import get_logger
from hivemind.exoskeleton import ExoskeletonConfig, ScreenSize
from hivemind.exoskeleton.browser import BrowserLauncher
from hivemind.exoskeleton.recorder import (
    InMemoryRecordingStore,
    RecordingStore,
    SqliteRecordingStore,
)
from hivemind.forage import Tempo
from hivemind.llm import (
    DirectTranscriptionGate,
    Ears,
    FannerTranscriptionGate,
    ProviderRegistry,
    TranscriptionGate,
    TranscriptionUnsupportedError,
    UnresolvableSlotError,
)
from hivemind.manifest.schema import ExoskeletonSection
from hivemind.pheromone import SideChannelPurger
from hivemind.wardens import WardenDeps
from waggle.clock import Clock
from waggle.ids import CellId

if TYPE_CHECKING:
    from hivemind.cli.compose.deps import HiveParts

# The module the `hivemind[browser]` extra installs; ChromiumLauncher drives the browser through it.
BROWSER_EXTRA_MODULE = "playwright"

log = get_logger(__name__)

__all__ = [
    "BROWSER_EXTRA_MODULE",
    "ExoskeletonWiring",
    "RecordingSideChannel",
    "build_browser_launcher",
    "exoskeleton_config",
    "hive_stand_exoskeleton",
    "in_cell_exoskeleton",
    "night_veil_side_channels",
    "open_hive_recordings",
    "resolve_ears",
    "running_as_root",
]


@dataclass(frozen=True, slots=True)
class ExoskeletonWiring:
    """The four WardenDeps fields the Exoskeleton needs, built together and applied at once."""

    config: ExoskeletonConfig  # The screen, the readiness budget and Chromium's sandbox.
    launcher: BrowserLauncher | None  # None: no browser extra here, so no browser fast path.
    recordings: RecordingStore | None  # None would keep no recordings; both Wardens keep them.
    ears: Ears | None  # None: nothing bound can hear, so no `listen` tool.

    def apply(self, deps: WardenDeps) -> WardenDeps:
        """Return `deps` with its four Exoskeleton fields set from this wiring.

        Args:
            deps: A Warden's deps as the rest of its composition root built them.

        Returns:
            A copy of `deps`; every other field is unchanged.
        """
        return dataclasses.replace(
            deps,
            exoskeleton_config=self.config,
            browser_launcher=self.launcher,
            recording_store=self.recordings,
            ears=self.ears,
        )


@dataclass(frozen=True, slots=True)
class RecordingSideChannel:
    """A recording store as a Night Veil side channel (`hivemind.pheromone.SideChannelPurger`).

    Codingrules section 12 lists flight recordings among the execution records a Night Veil Cell
    never keeps after teardown; this adapter lets `NightVeilTeardownPurge` remove them with the
    rest of the Cell's side channels.
    """

    store: RecordingStore  # The store whose recordings of the torn-down Cell go.

    async def purge(self, cell_id: CellId) -> int:
        """Delete every recording of `cell_id`, frames included; return how many went."""
        return await self.store.purge_cell(str(cell_id))


def hive_stand_exoskeleton(parts: HiveParts) -> ExoskeletonWiring:
    """Build the Hive Stand Warden's wiring from `[exoskeleton]`, its stores and its Fanner.

    Args:
        parts: The Hive's shared collaborators (`hivemind.cli.compose.deps.HiveParts`).

    Returns:
        The wiring `hivemind.cli.compose.deps.build_warden_deps` applies.
    """
    config = exoskeleton_config(parts.manifest.exoskeleton, browser_sandbox=not running_as_root())
    return ExoskeletonWiring(
        config=config,
        launcher=build_browser_launcher(parts.clock, config.screen),
        recordings=parts.stores.recordings,
        # One metered gate for every sub-bee's ears on the default tempo: WardenDeps carries one
        # Ears for all of them, so a transcription is metered and recorded, not grant-attributed.
        ears=resolve_ears(parts.registry, FannerTranscriptionGate(parts.fanner, Tempo())),
    )


def in_cell_exoskeleton(registry: ProviderRegistry, clock: Clock) -> ExoskeletonWiring:
    """Build the in-Cell Warden's wiring: defaults, no Chromium sandbox, recordings in memory.

    A Virtual Cell reads no manifest, so the screen and readiness budget are `ExoskeletonConfig`'s
    defaults (the same values `[exoskeleton]` defaults to). Chromium's own sandbox is off: the
    Cell is itself the sandbox, and its container drops the capabilities that sandbox needs. The
    Cell has no database file of its own (its Warden's memory store and trail segment live in
    process), so recordings go to an `InMemoryRecordingStore`: a Virtual Cell's recordings live
    with the Cell and go when it is torn down, until a later step ships them to the Queen. No
    Fanner runs inside a Cell, so its ears are unmetered, like its chat calls (`DirectCallGate`).

    Args:
        registry: The Cell's own ProviderRegistry (`hivemind.cli.in_cell.providers`).
        clock: Paces the browser launcher's waits and stamps its screenshots.

    Returns:
        The wiring `hivemind.cli.in_cell.deps.build_in_cell_warden_deps` applies.
    """
    config = ExoskeletonConfig(browser_sandbox=False)
    return ExoskeletonWiring(
        config=config,
        launcher=build_browser_launcher(clock, config.screen),
        recordings=InMemoryRecordingStore(),
        ears=resolve_ears(registry, DirectTranscriptionGate()),
    )


def exoskeleton_config(section: ExoskeletonSection, *, browser_sandbox: bool) -> ExoskeletonConfig:
    """Convert `[exoskeleton]` into the ExoskeletonConfig attach reads.

    Args:
        section: The manifest's `[exoskeleton]` section.
        browser_sandbox: Whether Chromium keeps its own sandbox; decided by the caller from where
            the browser runs, never read from the manifest (module docstring).

    Returns:
        The config for every attach this Warden performs.
    """
    return ExoskeletonConfig(
        screen=ScreenSize(section.screen_width, section.screen_height),
        ready_timeout_s=section.ready_timeout_s,
        browser_sandbox=browser_sandbox,
    )


def running_as_root() -> bool:
    """Return whether this process runs as root, where Chromium cannot keep its own sandbox."""
    # Windows has no effective uid, and Chromium's sandbox works for an administrator there.
    if sys.platform == "win32":
        return False
    return os.geteuid() == 0


def build_browser_launcher(clock: Clock, window: ScreenSize) -> BrowserLauncher | None:
    """Build a ChromiumLauncher when the browser extra is installed, else None.

    Args:
        clock: Paces the launcher's wait for the DevTools port and stamps its screenshots.
        window: The browser window's size: the size of the display it fills.

    Returns:
        The launcher, or None where Playwright is missing: attach then refuses a browser need at
        planning time rather than starting a browser nothing here could drive.
    """
    # find_spec only locates the package; nothing imports Playwright outside its one backend.
    if importlib.util.find_spec(BROWSER_EXTRA_MODULE) is None:
        return None
    # Imported only once the extra is known to be there, so a terminal-only Hive never loads it.
    from hivemind.exoskeleton.browser import ChromiumLauncher, ChromiumSettings

    return ChromiumLauncher(clock, settings=ChromiumSettings(window=window))


def resolve_ears(registry: ProviderRegistry, gate: TranscriptionGate) -> Ears | None:
    """Resolve the transcriber slot behind `gate`, or None when nothing bound to it can hear.

    Args:
        registry: The ProviderRegistry whose `[llm.slots] transcriber` chain is resolved.
        gate: The Fanner's metered gate on the Hive Stand, the direct one inside a Cell.

    Returns:
        The Ears every sub-bee's `listen` tool uses, or None.
    """
    try:
        bound = registry.bound_transcriber()
    except (TranscriptionUnsupportedError, UnresolvableSlotError) as exc:
        # Unsupported: the slot is bound to a chat-only kind (minimal.toml binds anthropic).
        # Unresolvable: the registry has no transcriber row at all (a Cell given no slot table
        # falls back to a fake bound only to WARDEN and WORKER). Either way nothing can hear;
        # debug, because composition must print nothing a `--json` caller would have to skip.
        log.debug("warden.ears_unavailable", reason=str(exc))
        return None
    return Ears(gate=gate, bound=bound)


def open_hive_recordings(
    db: Path, section: ExoskeletonSection, clock: Clock
) -> SqliteRecordingStore:
    """Open the Hive's recording store and delete recordings past `[exoskeleton]`'s retention.

    Run once as the Hive starts, this is the retention sweep: a recording is deleted once nothing
    in it happened within the last `recording_retention_days`.

    Args:
        db: The Hive's own `[hive] db` file.
        section: The manifest's `[exoskeleton]` section, for the retention window.
        clock: Supplies "now" for the cutoff.

    Returns:
        The opened store, older recordings already gone.
    """
    store = open_recordings(db)
    cutoff = clock.now() - timedelta(days=section.recording_retention_days)
    # asyncio.run: the composition root's one-call seam into the async store, like open_recordings.
    pruned = asyncio.run(store.prune_before(cutoff))
    # Only a sweep that deleted evidence is worth a line; a no-op one stays silent, so starting a
    # Hive prints nothing a `hive run --json` caller would have to skip.
    if pruned:
        log.info("exoskeleton.recordings_pruned", pruned=pruned, cutoff=cutoff.isoformat())
    return store


def night_veil_side_channels(recordings: RecordingStore) -> tuple[SideChannelPurger, ...]:
    """Return every side channel this Hive's Night Veil teardown purge must run.

    Args:
        recordings: The Hive's recording store.

    Returns:
        The side channels, in purge order, for `hivemind.pheromone.NightVeilTeardownPurge`; the
        flight recorder's store is the first registered.
    """
    return (RecordingSideChannel(recordings),)
