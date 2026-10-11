"""Host model servers on a Cell: start, offer, supervise and stop what a Warden may serve from.

A Cell that reports ``can_host_model`` can run a long-running OpenAI-compatible **model server**
(llama.cpp's server, vLLM, Ollama, LM Studio). Its Warden (the Cell's always-on supervisor) starts
the ones the Hive Manifest declares in ``[llm.servers]`` through its ``CellSession`` (roadmap step
8.3, ADR-0044), waits for the server's health probe, adds the models it serves to the live Forage
map as sources hosted on this Cell, and tells the Queen (the central orchestrator) with
``forage.source_added``; stopping reverses every step. This package is that mechanism: ``config``
(the decoupled ``ServerConfig`` the Warden reads, since it may not import the manifest), ``sources``
(which sources fit the Queen's ceilings, and the wire messages), ``report`` (sending and recording
them), ``launch`` (the process, its health wait and the bring-up), ``door`` (the registry seam)
and ``host`` (the ``ModelServerHost`` that ties them together).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool sub-package. Built by ``hivemind.wardens.warden.Warden`` once it holds its lease;
    its config is built by ``hivemind.cli.stores.server_configs`` and its provider door by both
    composition roots.

Key invariants:
    - Nothing a host started outlives its Warden's lease: ``ModelServerHost.stop_all`` runs before
      the lease is released.
    - A source is on the map and announced only while its server is healthy.
    - The Warden never imports the manifest: everything it needs is in ``ServerConfig``.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - .claude/codingrules.md section 8.10 for ceilings and the Nuc.
    - hivemind.queen.ticks.sources for the Queen's side of the reports.

Public API:
    - ModelServerHost: the host (host).
    - ServerConfig, HostedCell, RunningServer, RegisterProvider, UnregisterProvider: its inputs and
      a snapshot row of what it runs (config).
    - provider_door: the registry closures a composition root builds (door).
    - plan_loads, LoadPlan: the ceilings rule, pure (sources).
"""

from hivemind.wardens.local_pool.hosting.config import (
    HostedCell,
    RegisterProvider,
    RunningServer,
    ServerConfig,
    UnregisterProvider,
)
from hivemind.wardens.local_pool.hosting.door import provider_door
from hivemind.wardens.local_pool.hosting.host import ModelServerHost
from hivemind.wardens.local_pool.hosting.sources import LoadPlan, plan_loads

__all__ = [
    "HostedCell",
    "LoadPlan",
    "ModelServerHost",
    "RegisterProvider",
    "RunningServer",
    "ServerConfig",
    "UnregisterProvider",
    "plan_loads",
    "provider_door",
]
