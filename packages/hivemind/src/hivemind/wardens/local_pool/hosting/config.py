"""Define ServerConfig and HostedCell: what a Warden needs to host a model server, decoupled.

A **model server** is a long-running OpenAI-compatible HTTP process a Cell's Warden (its always-on
supervisor) starts on its own Cell (roadmap step 8.3, ADR-0044). The Hive Manifest declares one in
``[llm.servers.<server_id>]``, but a Warden may not import ``hivemind.manifest`` (the same Layer 1
sibling rule that keeps ``hivemind.llm`` away from it), so the composition root
(``hivemind.cli.stores.server_configs``) converts each row once into the frozen ``ServerConfig``
here: the argv template, the loopback port, the health path and start-up bound, the provider's
already-built ``ProviderConfig`` and the Forage map sources (a source is one model that can serve a
call) the server may load, each a ``ModelSource`` whose ``host_cell_id`` is still ``None`` because
the Cell is not known until the Warden holds its lease; ``hivemind.wardens.local_pool.hosting.host``
fills it in at start time. ``HostedCell`` groups the three things only a leased Warden has (its
Cell, its session and a way to read the Queen's current ceilings) so the host takes one argument,
not five (codingrules section 5.1). The two provider callables are the seam to the model registry:
the composition root closes them over a ``ProviderRegistry``, and the host never sees one.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Built by ``hivemind.cli.stores`` (``ServerConfig``),
    ``hivemind.wardens.warden`` (``HostedCell``) and ``launch`` (``RunningServer``); read by
    ``host``, ``sources`` and ``launch`` in this package and carried by ``hivemind.wardens.deps.
    WardenDeps``. Calls into ``hivemind.cell``, ``hivemind.forage`` and ``hivemind.llm`` only.

Key invariants:
    - ``ServerConfig`` is frozen: a Warden never edits what the manifest declared.
    - Every ``ServerConfig.sources`` entry is built with ``host_cell_id=None``; only the host fills
      it, with its own Cell's id, so one config is valid on any Cell.
    - ``argv`` still holds the literal ``{port}`` and ``{scratch}`` placeholders; substitution
      happens in ``hivemind.wardens.local_pool.hosting.launch``, at start time.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - hivemind.manifest.schema.llm.servers for ServerSpec, the manifest row this mirrors.
    - hivemind.cli.stores for server_configs, the one place a ServerSpec becomes a ServerConfig.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from hivemind.cell import BackgroundProcess, Cell, CellSession
from hivemind.forage import Ceilings, ModelSource
from hivemind.llm import LLMProvider, ProviderConfig

# Builds (or fetches) the live provider a started server answers as, registering its config first;
# the composition root closes it over a ProviderRegistry (cli.compose.deps, cli.in_cell.deps).
RegisterProvider = Callable[[str, ProviderConfig], LLMProvider]
# Forgets a runtime-registered provider by name; True when the registry gave up an instance the
# caller now owns (and must close), False when the manifest still declares the name.
UnregisterProvider = Callable[[str], bool]

__all__ = [
    "HostedCell",
    "RegisterProvider",
    "RunningServer",
    "ServerConfig",
    "UnregisterProvider",
    "bounded",
]


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """One ``[llm.servers.<server_id>]`` row, as a Warden reads it.

    Attributes:
        server_id: The manifest key; ``SourceAdded.server_id`` on the wire.
        provider: The ``[llm.providers]`` name the server answers as.
        provider_config: That provider's configuration, handed to ``RegisterProvider``.
        argv: The command that starts the server, ``{port}``/``{scratch}`` not yet substituted.
        port: The loopback TCP port the server listens on.
        health_path: The path below the provider's base URL that answers when ready.
        startup_timeout_s: Seconds the health probe may take to pass before the server is stopped.
        sources: The Forage map sources the server may load, with ``host_cell_id`` still None.
        keep_alive_s: Idle residency of a loaded model for a server that unloads itself; None
            leaves the server's default.
        load_on_start: Whether ``ModelServerHost.start_all`` starts it.
    """

    server_id: str
    provider: str
    provider_config: ProviderConfig
    argv: tuple[str, ...]
    port: int
    health_path: str
    startup_timeout_s: float
    sources: tuple[ModelSource, ...]
    keep_alive_s: float | None = None
    load_on_start: bool = True


@dataclass(frozen=True, slots=True)
class RunningServer:
    """One model server a host started, and what it currently offers.

    Attributes:
        config: The manifest row it was started from.
        process: The background process the session started.
        provider: The live provider it answers as.
        sources: The sources currently on the map, ``host_cell_id`` this Cell; shrinks on unload.
    """

    config: ServerConfig
    process: BackgroundProcess
    provider: LLMProvider
    sources: tuple[ModelSource, ...]


@dataclass(frozen=True, slots=True)
class HostedCell:
    """The Cell a host serves from: what only a Warden holding its lease has.

    Attributes:
        cell: The leased Cell; its ``capabilities.can_host_model`` gates everything.
        session: The Warden's own session on it, the only way a server is started or stopped.
        ceilings_of: Reads the Queen's current ``Ceilings`` for this Warden, or None while she has
            set none (the operator's manifest then bounds what loads).
    """

    cell: Cell
    session: CellSession
    ceilings_of: Callable[[], Ceilings | None]


def bounded(config: ServerConfig, deadline_s: float | None) -> ServerConfig:
    """Return ``config`` with its start-up wait cut to ``deadline_s`` when that is shorter.

    A promotion carries the longest the Queen will wait for the server; the manifest's own bound
    still applies when it is the shorter of the two, so an order can hurry a start and never
    stretch one.

    Args:
        config: The manifest row.
        deadline_s: The Queen's deadline in seconds, or None when she set none.

    Returns:
        ``config`` itself, or a copy with ``startup_timeout_s`` lowered to the deadline.
    """
    if deadline_s is None or deadline_s >= config.startup_timeout_s:
        return config
    return replace(config, startup_timeout_s=deadline_s)
