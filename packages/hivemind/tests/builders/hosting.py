"""Build a ModelServerHost over fakes: a hosting-capable Cell, a scripted session and provider.

`make_hosting_rig` wires everything `hivemind.wardens.local_pool.hosting.ModelServerHost` touches
to in-memory fakes: a `builders.wardens.make_warden_deps` Warden whose one Cell reports
`can_host_model`, a `hivemind.cell.fake.FakeSession` standing in for the Cell's terminal (so
`script_start` decides whether a server "starts"), a `hivemind.llm.FakeLLMProvider` whose
`set_outage` decides whether its health probe passes, a live `hivemind.forage.ForageMap`, and a
`RegistryDoor` that records every register and unregister. `drive` runs a coroutine that waits on
the fake clock (a health wait) to completion by advancing the clock as it sleeps.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/wardens/local_pool/hosting and the Warden's hosting lifecycle test.

Key invariants:
    - `RegistryDoor.register` returns the rig's one FakeLLMProvider, whatever the name.
    - `drive` never advances the clock until the awaited coroutine is blocked on it, and gives up
      after `DRIVE_LIMIT` rounds rather than hanging a test.

See Also:
    - hivemind.wardens.local_pool.hosting.host for the class under test.
    - builders.wardens for make_warden_deps and QueenEnd.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path

from builders.cells import make_capabilities, make_cell
from builders.forage import make_source
from builders.wardens import QueenEnd, make_warden_deps

from hivemind.cell import Cell
from hivemind.cell.fake import FakeSession
from hivemind.forage import Ceilings, ForageMap, ModelSource
from hivemind.llm import FakeLLMProvider, LLMProvider, ProviderConfig
from hivemind.wardens import Warden, WardenDeps
from hivemind.wardens.local_pool.hosting import (
    HostedCell,
    ModelServerHost,
    RegisterProvider,
    ServerConfig,
)
from waggle.clock import FakeClock
from waggle.ids import WardenId

DRIVE_LIMIT = 500  # Event-loop rounds `drive` allows before it calls a test stuck.
PROVIDER_NAME = "local-prov"  # The provider name the rig's servers answer as.
PROGRAM = "scripted-server"  # argv[0] of the rig's servers; FakeSession.script_start keys on it.
CLOCK_STEP_S = 0.5  # How far `drive` moves the clock each time the awaited code is asleep.


def make_server_config(
    server_id: str = "box", sources: tuple[ModelSource, ...] | None = None, **overrides: object
) -> ServerConfig:
    """Build a ServerConfig serving `sources` (two small ones by default) as `PROVIDER_NAME`."""
    served = sources if sources is not None else (_source("small"), _source("large"))
    fields: dict[str, object] = {
        "server_id": server_id,
        "provider": PROVIDER_NAME,
        "provider_config": ProviderConfig(kind="openai_compat", base_url="http://127.0.0.1:1/v1"),
        "argv": (PROGRAM, "--port", "{port}", "--scratch", "{scratch}"),
        "port": 18_080,
        "health_path": "/models",
        "startup_timeout_s": 3.0,
        "sources": served,
    }
    fields.update(overrides)
    return ServerConfig(**fields)  # type: ignore[arg-type]


def _source(source_id: str, vram_bytes: int | None = None) -> ModelSource:
    """A source of `PROVIDER_NAME` no Cell hosts yet."""
    return make_source(
        source_id=source_id,
        provider=PROVIDER_NAME,
        model=f"{source_id}-model",
        seats=2,
        vram_bytes_required=vram_bytes,
        capabilities=("json_mode",),
    )


def vram_source(source_id: str, vram_bytes: int) -> ModelSource:
    """A source needing `vram_bytes` of VRAM, for a ceilings test."""
    return _source(source_id, vram_bytes)


@dataclass
class RegistryDoor:
    """The register/unregister callables a host is given, recording what it was asked."""

    provider: FakeLLMProvider
    removes_on_unregister: bool = False  # What `unregister` reports: True = instance given up.
    registered: list[str] = field(default_factory=list)
    unregistered: list[str] = field(default_factory=list)

    def register(self, name: str, config: ProviderConfig) -> LLMProvider:
        """Record `name` and hand back the rig's provider."""
        del config
        self.registered.append(name)
        return self.provider

    def unregister(self, name: str) -> bool:
        """Record `name` and report whether an instance was given up."""
        self.unregistered.append(name)
        return self.removes_on_unregister


@dataclass
class HostingRig:
    """One host with every fake behind it, ready for a test to drive."""

    host: ModelServerHost
    deps: WardenDeps
    queen_end: QueenEnd
    session: FakeSession
    provider: FakeLLMProvider
    door: RegistryDoor
    forage_map: ForageMap
    cell: Cell
    clock: FakeClock
    warden_id: WardenId
    ceilings: Ceilings | None = None


def make_hosting_rig(
    configs: tuple[ServerConfig, ...] | None = None,
    *,
    can_host_model: bool = True,
    ceilings: Ceilings | None = None,
    has_door: bool = True,
    register: RegisterProvider | None = None,
) -> HostingRig:
    """Build a HostingRig serving `configs` (one two-source server by default)."""
    clock = FakeClock()
    served = configs if configs is not None else (make_server_config(),)
    cell = make_cell(clock=clock, capabilities=make_capabilities(can_host_model=can_host_model))
    provider = FakeLLMProvider(name=PROVIDER_NAME)
    door = RegistryDoor(provider)
    forage_map = ForageMap((), clock=clock)
    deps, queen_end, warden_id = make_warden_deps(
        clock,
        cells=(cell,),
        servers={config.server_id: config for config in served},
        map=forage_map,
        register_provider=(register or door.register) if has_door else None,
        unregister_provider=door.unregister if has_door else None,
    )
    session = FakeSession(Path("/fake-scratch/host"), clock)
    rig = HostingRig(
        host=None,  # type: ignore[arg-type]  # Filled just below: it needs the rig's ceilings.
        deps=deps,
        queen_end=queen_end,
        session=session,
        provider=provider,
        door=door,
        forage_map=forage_map,
        cell=cell,
        clock=clock,
        warden_id=warden_id,
        ceilings=ceilings,
    )
    place = HostedCell(cell=cell, session=session, ceilings_of=lambda: rig.ceilings)
    rig.host = ModelServerHost(deps, warden_id, place)
    return rig


def make_hosting_warden(
    *, can_host_model: bool = True, with_servers: bool = True
) -> tuple[Warden, WardenDeps, QueenEnd, ForageMap, RegistryDoor]:
    """Build a Warden (not yet started) on a Cell that may host `make_server_config()`'s server."""
    clock = FakeClock()
    cell = make_cell(clock=clock, capabilities=make_capabilities(can_host_model=can_host_model))
    door = RegistryDoor(FakeLLMProvider(name=PROVIDER_NAME))
    forage_map = ForageMap((), clock=clock)
    config = make_server_config()
    deps, queen_end, warden_id = make_warden_deps(
        clock,
        cells=(cell,),
        servers={config.server_id: config} if with_servers else {},
        map=forage_map,
        register_provider=door.register,
        unregister_provider=door.unregister,
    )
    return Warden(warden_id, deps), deps, queen_end, forage_map, door


async def drive[ResultT](clock: FakeClock, work: Awaitable[ResultT]) -> ResultT:
    """Run `work` to completion, advancing `clock` whenever it is asleep on it."""
    task = asyncio.ensure_future(work)
    for _ in range(DRIVE_LIMIT):
        if task.done():
            break
        # Let the work run until it blocks, then move time so its next sleep can end.
        await asyncio.sleep(0)
        clock.advance(CLOCK_STEP_S)
    return await asyncio.wait_for(task, timeout=1.0)
