"""Start a model server's process on a Cell and wait until its provider reports healthy.

A **model server** is a long-running OpenAI-compatible HTTP process (ADR-0044) a Cell's Warden
starts through its ``CellSession`` (the terminal on that Cell; the only way anything there is run).
``launch_spec`` turns a ``ServerConfig``'s argv template into a ``BackgroundSpec`` by replacing the
literal ``{port}`` and ``{scratch}`` placeholders, and sends the server's output to a log file in
scratch so a failed start leaves something to read. ``wait_healthy`` then polls the provider's own
``health()`` (the OpenAI-compatible adapter's cheap ``GET /models``) until it reports HEALTHY, the
process dies, or the start-up bound passes, so the Warden never announces a source whose server
cannot answer yet. Substitution is a plain ``str.replace`` per argument, never ``str.format``,
because a server's arguments may legitimately carry braces (a JSON option).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Called by ``host``. Calls into ``hivemind.cell``, ``hivemind.
    llm`` (HealthState, LLMProvider), ``config``, ``report``, ``sources`` and waggle only.

Key invariants:
    - The argv is never run through a shell (``BackgroundSpec``'s own contract), so a substituted
      path with spaces or metacharacters is one argument, never reinterpreted.
    - ``wait_healthy`` never waits past ``timeout_s`` of the injected clock, and never raises for a
      probe that fails: a half-started server can fail its probe in many ways, all of them "not
      yet".

See Also:
    - hivemind.cell.session for BackgroundSpec and the CellSession contract.
    - hivemind.llm.providers.openai_compat for the health probe the provider runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hivemind.cell import BackgroundSpec, BackgroundStartError
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.forage import ModelSource
from hivemind.llm import HealthState, LLMProvider
from hivemind.wardens.local_pool.hosting.config import (
    HostedCell,
    RunningServer,
    ServerConfig,
)
from hivemind.wardens.local_pool.hosting.report import ReportPort, record_never_loaded
from hivemind.wardens.local_pool.hosting.sources import hosted
from waggle.clock import Clock
from waggle.messages.forage import SourceRemovalCause

if TYPE_CHECKING:
    # Type-only: wardens.deps imports hosting.config, so a runtime import here would cycle.
    from hivemind.wardens.deps import WardenDeps

POLL_INTERVAL_S = 0.2  # A loopback probe is cheap; this keeps a fast server's start-up latency low.
LOG_NAME_TEMPLATE = (
    "model-server-{server_id}.log"  # Under scratch, so a lease's release removes it.
)

log = get_logger(__name__)

__all__ = ["POLL_INTERVAL_S", "bring_up", "give_back", "launch_spec", "wait_healthy"]


def launch_spec(config: ServerConfig, scratch_dir: Path) -> BackgroundSpec:
    """Build the ``BackgroundSpec`` that starts ``config``'s server on a session.

    Args:
        config: The server to start.
        scratch_dir: The session's scratch directory, substituted for ``{scratch}``.

    Returns:
        The argv with ``{port}`` and ``{scratch}`` replaced, logging to a file in scratch.
    """
    argv = tuple(
        argument.replace("{port}", str(config.port)).replace("{scratch}", str(scratch_dir))
        for argument in config.argv
    )
    return BackgroundSpec(
        argv=argv, log_path=Path(LOG_NAME_TEMPLATE.format(server_id=config.server_id))
    )


async def wait_healthy(
    provider: LLMProvider,
    is_alive: Callable[[], Awaitable[bool]],
    clock: Clock,
    timeout_s: float,
) -> bool:
    """Poll ``provider.health()`` until HEALTHY, the process dies, or ``timeout_s`` passes.

    Args:
        provider: The live provider the server answers as; its ``health()`` is the probe.
        is_alive: Whether the server's process is still running; a dead process ends the wait.
        clock: Source of the deadline and of the pause between probes.
        timeout_s: How long the whole wait may take, on ``clock``.

    Returns:
        True once the provider reported HEALTHY; False when the process died or time ran out.
    """
    deadline = clock.now() + timedelta(seconds=timeout_s)
    # One probe per poll interval until a bound is hit: the loop decides one thing, ready or not.
    while True:
        if not await is_alive():
            return False  # The process exited on its own; no probe can succeed now.
        if await _probe(provider):
            return True
        if clock.now() >= deadline:
            return False
        # A short pause on the injected clock, so a fake clock drives the wait deterministically.
        await clock.sleep(POLL_INTERVAL_S)


async def _probe(provider: LLMProvider) -> bool:
    """Return whether one health probe says HEALTHY; any failure is just "not yet"."""
    try:
        # Loopback HTTP, milliseconds; the adapter's own timeout bounds a server that accepts the
        # connection and never answers (that surfaces as an exception, handled below).
        health = await provider.health()
    except Exception as exc:
        # SAFETY: a half-started external server can drop the connection in ways the adapter does
        # not name (a reset, a truncated response). Every one means "not ready"; the caller's
        # deadline bounds the retries, so swallowing here cannot spin forever or hide a live bug.
        log.debug("wardens.hosting.probe_failed", error=type(exc).__name__)
        return False
    return health.state is HealthState.HEALTHY


async def bring_up(
    deps: WardenDeps,
    place: HostedCell,
    report: ReportPort,
    config: ServerConfig,
    planned: tuple[ModelSource, ...],
) -> RunningServer | None:
    """Register ``config``'s provider, start its process and wait until it answers.

    Every failure leaves nothing behind: the process is stopped, the provider given back, and the
    ``planned`` sources recorded on the trail as never loaded (cause ``SERVER_DIED``). Nothing is
    sent to the Queen, who never heard of them.

    Args:
        deps: The Warden's collaborators; its provider door is used here.
        place: The Cell and session to start the process on.
        report: Where a failure is recorded.
        config: The server to start.
        planned: The sources the ceilings let it offer, ``host_cell_id`` still None.

    Returns:
        The running server, its sources stamped with this Cell as host and not yet on the map;
        None when the server could not be registered, started or made healthy.
    """
    provider = _register(deps, config)
    session = place.session
    if provider is None:
        await _record_dead(report, config, planned)
        return None
    try:
        # Local fork+exec: milliseconds; raises when the program cannot be started at all.
        process = await session.start(launch_spec(config, session.scratch_dir))
    except BackgroundStartError as error:
        log.warning("wardens.hosting.start_failed", server_id=config.server_id, error=str(error))
        await give_back(deps, config.provider, provider)
        await _record_dead(report, config, planned)
        return None

    async def alive() -> bool:
        return await session.is_running(process)

    # Bounded by the manifest's startup_timeout_s on the injected clock.
    if not await wait_healthy(provider, alive, deps.clock, config.startup_timeout_s):
        log.warning("wardens.hosting.unhealthy", server_id=config.server_id)
        await session.stop(process)
        await give_back(deps, config.provider, provider)
        await _record_dead(report, config, planned)
        return None
    offered = tuple(hosted(source, place.cell.id) for source in planned)
    return RunningServer(config, process, provider, offered)


async def give_back(deps: WardenDeps, name: str, provider: LLMProvider) -> None:
    """Unregister a runtime-added provider, closing the instance the registry gave up.

    Args:
        deps: The Warden's collaborators; ``unregister_provider`` is the door.
        name: The provider's ``[llm.providers]`` name.
        provider: The live instance ``bring_up`` got for it.
    """
    unregister = deps.unregister_provider
    # The door reports True only for an instance it dropped from the registry: ours to close.
    # A manifest-declared provider stays registered and is closed by the registry itself.
    if unregister is not None and unregister(name):
        await provider.aclose()


def _register(deps: WardenDeps, config: ServerConfig) -> LLMProvider | None:
    """Register ``config``'s provider and return it; None when the registry refuses it."""
    register = deps.register_provider
    if register is None:
        return None  # ModelServerHost already declined this Warden; kept to narrow the type.
    try:
        return register(config.provider, config.provider_config)
    except HiveMindError as error:
        # An offline Hive refuses a non-loopback provider, an unknown kind has no factory:
        # either way the server is not usable, and Warden.start must not fail over it.
        log.warning("wardens.hosting.register_failed", server_id=config.server_id, error=str(error))
        return None


async def _record_dead(
    report: ReportPort, config: ServerConfig, planned: tuple[ModelSource, ...]
) -> None:
    """Record, on the trail only, that a server's planned sources never loaded."""
    ids = tuple(source.source_id for source in planned)
    await record_never_loaded(report, config.server_id, ids, SourceRemovalCause.SERVER_DIED)
