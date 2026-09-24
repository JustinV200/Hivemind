"""Wire up Virtual Cells when `[virtual_cells] backend` is set: build_virtual_cells.

Roadmap step 5.6's own composition-root half. `build_virtual_cells` returns `None` when
`manifest.virtual_cells.backend` is unset -- "virtual backend unset => no virtual side" (roadmap
step 5.7), so a manifest that never sets it builds a `Hive` exactly as before this module existed
(codingrules section 13: the composition root converts a manifest slice; nothing below it ever sees
one). When it is set, this module builds every Layer-3/Layer-6 collaborator roadmap steps 5.1-5.9
landed and were reconciled by this same dispatch: a `hivemind.hive.registry.BackendRegistry` with
only `[virtual_cells] backend`'s own one selected name registered, never "fake" alongside a real
one too (see `_build_registry`'s own docstring for why: a real Docker run found placement picking
"fake" first by `registry.names()` order in a Docker-configured Hive, provisioning a fake Cell
nothing ever connects to -- `hivemind.hive.lifecycle.CellLifecycle.virtual_backend_candidates`
force-constructs every registered backend on every placement snapshot, so an unselected,
unavailable backend must never be registered, and neither must a selected-but-unwanted one).
The one registered backend is still built lazily inside its own factory (its own
`ConfigurationError` -- `hivemind.hive.backends.docker.sdk_client.SdkDockerClient` already raises
one naming the `hivemind[docker]` extra when the SDK is missing -- only surfaces the first time
`.get()` actually constructs it), a `hivemind.queen.cell_gate.gate.QueenReadinessGate`,
a `hivemind.queen.cell_gate.listener.CellListener` (built here, started/stopped with the Queen's
own lifetime by `hivemind.cli.compose.hive.run_hive`), an `hivemind.hive.overwinter.pool.
OverwinterPool`, a `hivemind.hive.lifecycle.CellLifecycle` and a `hivemind.queen.cell_gate.
provider.LifecycleVirtualCellProvider`, plus the two live-feed closures and the `on_task_finished`
callable `hivemind.queen.deps.QueenDeps`'s own additive fields take.

The Queen's own Waggle URL, as a Cell must dial it (`hivemind.hive.backends.bootstrap.
QueenEndpoint.waggle_url`), is resolved lazily too -- inside each backend's own factory, not
up front -- because `listen_port = 0` (the manifest's own default) means the actual bound port is
only known once `CellListener.start()` has run, which happens after this module's own work, in
`run_hive`; by the time a backend factory is actually called (the Queen's first Virtual placement,
well after `run_hive` started), `listener.uri` already reflects the real bound address.
`docker_gateway_url` is the one Docker-specific rewrite this module makes for that URL: a bare
loopback address reachable from the Queen's own process is never reachable from inside a Docker
container the same way, so a `backend = "docker"` Hive's own `waggle_url` gets `host.docker.
internal` substituted for a loopback host, unless the operator already named an explicit
`advertise_url` -- `hivemind.hive.backends.docker.network` (a file this dispatch may not touch) is
untouched; this is the one place that rewrite happens instead. An offline command (`hive cells
inspect`/`destroy`/`abscond`, `cli/readback/virtual*.py`) builds this same registry just to
construct a backend and read its already-provisioned Cells back, never calling `CellListener.
start()` (only `run_hive`'s own live path does) or provisioning anything new; `_listener_url`
falls back to a documented placeholder (`ws://127.0.0.1:0`) rather than propagating the listener's
own "no port until start()" `RuntimeError`, so those commands can still construct the backend --
a caller that DID try to provision through the placeholder fails at the Cell's own dial-out
instead, the correct failure, not a masked one.

A provisioned Cell also needs the operator's own `[llm.providers]`/`[llm.slots]` table to reach a
real model provider (roadmap step 8.x's own gap): `hivemind.cli.compose.virtual_cell_providers`
builds it from the exact same `hivemind.cli.stores.provider_configs`/`slot_bindings` conversion
the Hive Stand's own `ProviderRegistry` is built from, rewriting every loopback `base_url` so it
is reachable from inside the Cell (`host.docker.internal` for Docker, `hivemind.hive.backends.
qemu.network.USER_NET_HOST_ALIAS` for QEMU, unchanged for the in-process "fake" backend), and
resolving each provider's own API key from this composition root's own environment the same way
`hivemind.manifest.env.provider_api_key` already does. `hivemind.hive.backends.bootstrap.
QueenEndpoint.providers`/`.slots`/
`.provider_api_keys`/`.llm_offline` is where all four ride to the Cell.

The Queen signs every frame she sends a Virtual Cell, and every Cell is told her verify key when it
is provisioned, so the key must outlive the process: minted per process (phase 5 open item 5), a
Cell that outlived a Queen restart could never verify the next Queen. `build_virtual_cells` now
takes `hive_signer`, the Hive's own persisted Ed25519 key (`hivemind.common.secrets.
load_or_mint_hive_signer`, kept at `[hive] secrets_dir`), threaded in by `hivemind.cli.compose.hive.
build_hive` exactly as `environ` is. Only the offline `hive cells` commands, which never start the
listener nor provision a Cell, leave it `None` and get a throwaway key no Cell ever sees.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.cell` (Cell, CellIdentity),
    `hivemind.cli.compose.virtual_cell_backends` (RegistryContext, build_registry,
    docker_gateway_url, night_veil_socks_proxy_url -- this module's own backend-construction
    half, split out for its own line budget), `hivemind.cli.stores` (open_snapshot_ledger),
    `hivemind.hive` (BackendRegistry, CellLifecycle, CellReservation -- the one computation of a
    Virtual Cell's capacity, which the Cell itself repeats from its bootstrap -- NetworkPolicy,
    OverwinterConfig, OverwinterPool, OverwinterSettings, VirtualCellSpec, mint_cell_bootstrap is
    not used here -- backends mint their own),
    `hivemind.hive.night_veil` (NightVeilProbe, the fail-closed
    `_fail_closed_night_veil_probe` below returns), `hivemind.manifest` (HiveManifest),
    `hivemind.pheromone` (PheromoneTrail, TrailRecorder), `hivemind.queen.cell_gate`
    (CellListener, CellListenerDeps, LifecycleVirtualCellProvider, QueenReadinessGate,
    make_on_task_finished, make_quiesce), `hivemind.queen.dispatcher.snapshot` (the two
    hive-to-queen candidate converters), `hivemind.queen.placement` (VirtualBackendCandidate) and
    waggle only.

    **Graceful teardown wiring (this dispatch's own fix, a real Docker run's own defect):**
    `make_quiesce` is closed over `lambda: provider.queen` -- `LifecycleVirtualCellProvider`'s own
    late-bound Queen reference, reused rather than adding a second `bind_queen` call site to
    `hivemind.cli.compose.hive._assemble_hive` (not in this dispatch's allowed-to-fix list) -- and
    handed to `make_on_task_finished` as its `quiesce` parameter, so a finished task's own Cell is
    asked to stop and ship its final trail segment before `hivemind.hive.lifecycle.CellLifecycle.
    teardown` destroys the backend out from under it. `_build_lifecycle` also now binds a
    `TrailSegmentReceiver` unconditionally, not only for `docker`/`qemu`: leaving it unbound for
    `fake` silently dropped every `TrailSegmentSync` a Cell's own Warden ever shipped, which is why
    the graceful stop alone was not enough (see that function's own comment for the full story).

    **Night Veil probe wiring (roadmap step 5.7b, this branch closing a gap an earlier
    implementer's own report named):** `LifecycleVirtualCellProvider` now takes a `probe_factory`
    it calls to attest a freshly provisioned NIGHT_VEIL Cell before `mark_ready`
    (`hivemind.queen.cell_gate.provider`'s own module docstring). The real
    `hive.night_veil.SessionNightVeilProbe` needs a live `hivemind.cell.CellSession` bound to the
    Cell being attested, and `WardenLink` (`hivemind.queen.deps`) carries only a Waggle
    `Transport` today -- no session-opening seam exists yet from the Queen to a Virtual Cell.
    Rather than defaulting to `FakeNightVeilProbe` here (codingrules 14.4: fakes are for tests,
    never a silent production default), `_fail_closed_night_veil_probe` below raises a clear,
    actionable error the moment a NIGHT_VEIL placement is actually attempted, so the gap fails
    loudly instead of attesting against fabricated results. Report item: replace it with a
    `SessionNightVeilProbe` factory once a Queen-side `CellSession` path exists.

Key invariants:
    - `build_virtual_cells` returns `None` whenever `manifest.virtual_cells.backend` is `None`, and
      touches nothing else in that case (module docstring).
    - The `docker`/`qemu` backend factories (`hivemind.cli.compose.virtual_cell_backends`) are only
      ever called by `BackendRegistry.get`, itself only ever called once the Queen actually needs
      that backend -- neither the `docker` package nor `qemu_base_image`/`qemu_vm_root` being unset
      can ever break building a Hive that never uses that backend.
    - `_build_registry` (aliased from that module's own `build_registry`) registers exactly one
      backend, `section.backend`'s own selected name -- never "fake" alongside a real one.

See Also:
    - .claude/roadmap.md step 5.6 for the composition-root wiring this module implements.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for QueenEndpoint and
      why a Cell's own dial-back address needs backend-specific resolution.
    - hivemind.cli.compose.hive for build_hive/run_hive, this module's one caller and the
      listener's own start/stop lifetime owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.cell import Cell, CellIdentity, CombShieldLevel
from hivemind.cli.compose.virtual_cell_backends import (
    RegistryContext as _RegistryContext,
)
from hivemind.cli.compose.virtual_cell_backends import (
    build_registry as _build_registry,
)
from hivemind.cli.compose.virtual_cell_backends import (
    docker_gateway_url,
)
from hivemind.cli.stores import open_snapshot_ledger
from hivemind.hive import (
    BackendRegistry,
    CellLifecycle,
    CellReservation,
    NetworkPolicy,
    OverwinterConfig,
    OverwinterPool,
    OverwinterSettings,
    VirtualCellSpec,
)
from hivemind.hive.night_veil import NightVeilProbe
from hivemind.manifest import HiveManifest
from hivemind.manifest.schema.placement import NetworkPolicyName, VirtualCellsSection
from hivemind.pheromone import PheromoneTrail, TrailRecorder
from hivemind.queen.cell_gate import (
    CellListener,
    CellListenerDeps,
    CellSnapshotHandler,
    LifecycleVirtualCellProvider,
    QueenReadinessGate,
    make_on_cell_granted,
    make_on_task_finished,
    make_quiesce,
)
from hivemind.queen.cell_gate.shutdown import RetireAll, make_retire_all
from hivemind.queen.deps import (
    DormantCellSource,
    OnCellGranted,
    OnTaskFinished,
    VirtualBackendSource,
)
from hivemind.queen.dispatcher.snapshot import (
    dormant_candidate_from_lifecycle,
    virtual_backend_candidate_from_lifecycle,
)
from hivemind.queen.placement import DormantCandidate, VirtualBackendCandidate
from hivemind.queen.trail import TrailSegmentReceiver
from waggle.clock import Clock
from waggle.ids import HiveId
from waggle.messages import OsFamily as WireOsFamily
from waggle.signing import Ed25519Signer

# The manifest carries no `[virtual_cells] max_sub_bees` field yet (roadmap step 5.6's own gap,
# named in this dispatch's report): every provisioned Cell's own template promises this many
# concurrent sub-bees, matching `hivemind.hive.backends.fake.FakeCellBackend`'s own default.
_DEFAULT_MAX_SUB_BEES = 4
_VIRTUAL_CELL_ARCH = "x86_64"  # images/ is Ubuntu-based (codingrules section 2).

__all__ = ["VirtualCellsParts", "build_virtual_cells", "docker_gateway_url"]


@dataclass(frozen=True, slots=True)
class VirtualCellsParts:
    """Every collaborator `build_hive` folds into `QueenDeps` and `run_hive` starts/stops.

    Attributes:
        registry: The one registered CellBackend `[virtual_cells] backend` selected, built lazily
            (`_build_registry`'s own docstring: never "fake" alongside a real one).
        gate: Resolves once a provisioned Cell's own handshake verifies.
        listener: Accepts every Virtual Cell's outbound connection; `run_hive` starts/stops it.
        lifecycle: Owns every Virtual Cell state edge and backend call.
        provider: The real `VirtualCellProvider`; `run_hive` binds the live `Queen` to it once one
            exists (`LifecycleVirtualCellProvider.bind_queen`).
        virtual_backend_source: `QueenDeps.virtual_backend_source`'s own live feed.
        dormant_cell_source: `QueenDeps.dormant_cell_source`'s own live feed.
        on_task_finished: `QueenDeps.on_task_finished`'s own implementation.
        on_cell_granted: `QueenDeps.on_cell_granted`'s own implementation.
        retire_all: Awaited by `run_hive` at shutdown to tear down every Virtual Cell this
            process still tracks, dormant ones included (hivemind.queen.cell_gate.shutdown).
    """

    registry: BackendRegistry
    gate: QueenReadinessGate
    listener: CellListener
    lifecycle: CellLifecycle
    provider: LifecycleVirtualCellProvider
    virtual_backend_source: VirtualBackendSource
    dormant_cell_source: DormantCellSource
    on_task_finished: OnTaskFinished
    on_cell_granted: OnCellGranted
    retire_all: RetireAll


def build_virtual_cells(
    manifest: HiveManifest,
    trail: PheromoneTrail,
    clock: Clock,
    environ: Mapping[str, str] | None = None,
    *,
    hive_signer: Ed25519Signer | None = None,
) -> VirtualCellsParts | None:
    """Build every Virtual Cell collaborator, or None when `[virtual_cells] backend` is unset.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        trail: Where every `cell.*` event the lifecycle drives lands.
        clock: Injected time source shared by every collaborator this builds.
        environ: For resolving each provider's own API key (`provider_api_keys`); `None` (every
            pre-8.x caller) resolves none -- module docstring's own "Fits into the Hive" note.
        hive_signer: The Hive's persisted key (`load_or_mint_hive_signer`), which the Queen
            signs every Virtual Cell frame with; `build_hive` always passes it, so a Cell that
            outlives a Queen restart still verifies the next Queen. `None` (offline `hive cells`
            commands, which never provision) mints a throwaway key for this process only.

    Returns:
        A VirtualCellsParts ready for `hivemind.cli.compose.hive.build_hive` to fold into
        `QueenDeps` and `run_hive` to start/stop, or `None` when no Virtual side is configured.
    """
    section = manifest.virtual_cells
    if section.backend is None:
        return None
    gate, listener, registry, lifecycle = _build_lifecycle(
        manifest, trail, clock, environ or {}, hive_signer
    )
    provider = _build_provider(manifest, lifecycle, gate, trail, clock)
    # Reuses provider's own late-bound Queen reference (`bind_queen`, called once by
    # hivemind.cli.compose.hive._assemble_hive) rather than adding a second bind_queen call site
    # not in this dispatch's allowed-to-fix list (hivemind.queen.cell_gate.quiesce's own module
    # docstring): a Queen does not exist yet at this point, so only a getter closure can reach it.
    quiesce = make_quiesce(lambda: provider.queen, clock)
    return VirtualCellsParts(
        registry=registry,
        gate=gate,
        listener=listener,
        lifecycle=lifecycle,
        provider=provider,
        virtual_backend_source=_virtual_backend_source(lifecycle, section, manifest.hive.id),
        dormant_cell_source=_dormant_cell_source(lifecycle),
        on_task_finished=make_on_task_finished(lifecycle, _null_scrub, quiesce),
        on_cell_granted=make_on_cell_granted(lifecycle),
        retire_all=make_retire_all(lifecycle, quiesce),
    )


def _build_lifecycle(
    manifest: HiveManifest,
    trail: PheromoneTrail,
    clock: Clock,
    environ: Mapping[str, str],
    hive_signer: Ed25519Signer | None,
) -> tuple[QueenReadinessGate, CellListener, BackendRegistry, CellLifecycle]:
    """Build the gate, listener, registry and lifecycle `build_virtual_cells` folds together.

    Split out of `build_virtual_cells` for its own line budget (codingrules 5.1). The listener
    signs with `hive_signer`, and every backend's QueenEndpoint publishes its public half.
    """
    section = manifest.virtual_cells
    # Only the offline commands pass None; they never provision, so no Cell sees this key.
    queen_signer = hive_signer if hive_signer is not None else Ed25519Signer.generate()
    gate = QueenReadinessGate()
    listener = _build_listener(manifest, section, gate, queen_signer, clock)
    ctx = _RegistryContext(
        manifest, section, gate, listener, queen_signer, manifest.hive.node_id, environ
    )
    registry = _build_registry(ctx, clock)
    overwinter_config = OverwinterConfig.from_section(section.overwinter)
    pool = OverwinterPool(clock, overwinter_config)
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    lifecycle = CellLifecycle(
        registry,
        trail,
        clock,
        identity,
        overwinter=OverwinterSettings(pool=pool, config=overwinter_config),
    )
    # Unconditional, unlike the snapshot relay below: any backend's own Warden -- "fake" included,
    # the one `tests.builders.virtual_cells.ContainerSpawningFakeCellBackend` runs a real in-Cell
    # Warden over -- ships its local trail segment as TrailSegmentSync chunks on every heartbeat
    # and once more from `stop()` (hivemind.wardens.trail_sync's own module docstring); leaving
    # this unbound for "fake" left every one of those chunks silently dropped (`CellListener.
    # _dispatch`'s own "no-op when CellListenerDeps names no ... receiver"), so a Cell's whole
    # local trail was lost even when `hivemind.queen.cell_gate.quiesce.make_quiesce` gave its
    # Warden a clean chance to ship it (this dispatch's own fix, confirmed missing by the phase 5
    # e2e slice: the Queen's trail held only her own node id for a finished Virtual Cell's task).
    listener.bind_trail_receiver(TrailSegmentReceiver(trail))
    if section.backend in ("docker", "qemu"):
        # "fake" (dev/test only) never declares can_snapshot=True (hivemind.hive.backends.fake's
        # own default), so it would only ever draw NoopSnapshotter -- opening a real SQLite
        # connection to attach a SnapshotLedgerPort no Cell of that backend can ever use would be
        # pure overhead (and, in a short-lived test process, an unclosed file handle nothing here
        # ever gets a chance to release). Only a real backend gets the snapshot relay wired in.
        _attach_snapshot(manifest, listener, lifecycle, clock)
    return gate, listener, registry, lifecycle


def _build_provider(
    manifest: HiveManifest,
    lifecycle: CellLifecycle,
    gate: QueenReadinessGate,
    trail: PheromoneTrail,
    clock: Clock,
) -> LifecycleVirtualCellProvider:
    """Build the real VirtualCellProvider.

    Split out of `build_virtual_cells` for its own line budget (codingrules 5.1).
    `_fail_closed_night_veil_probe` is this Hive's own probe_factory until a Queen-side
    CellSession path exists (module docstring's own "Night Veil probe wiring").
    """
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=manifest.hive.id, node_id=manifest.hive.node_id
    )
    return LifecycleVirtualCellProvider(lifecycle, gate, recorder, _fail_closed_night_veil_probe)


def _attach_snapshot(
    manifest: HiveManifest, listener: CellListener, lifecycle: CellLifecycle, clock: Clock
) -> None:
    """Wire the snapshot relay into `listener` and `lifecycle` (docker/qemu only; caller's guard).

    Roadmap step 5.10's own follow-up gap: a durable ledger so `hive cells snapshot`/`hive cells
    rollback` and this running Queen all read and write the same book, and the handler `listener`
    answers a Warden's own `CellSnapshotRequest`/`CellRollbackRequest` through -- bound after
    `listener` exists (the same late-binding shape `LifecycleVirtualCellProvider.bind_queen`
    already uses), since `listener` itself has to exist first for `_build_registry`'s own lazy
    endpoint closures.
    """
    ledger = open_snapshot_ledger(manifest.resolve_path(manifest.hive.db))
    lifecycle.attach_snapshot_ledger(ledger)
    listener.bind_snapshot_handler(CellSnapshotHandler(lifecycle, ledger, clock))


def _build_listener(
    manifest: HiveManifest,
    section: VirtualCellsSection,
    gate: QueenReadinessGate,
    queen_signer: Ed25519Signer,
    clock: Clock,
) -> CellListener:
    """Build the (not yet started) CellListener on `[virtual_cells] listen_host`/`listen_port`."""
    return CellListener(
        CellListenerDeps(
            gate=gate,
            queen_signer=queen_signer,
            queen_node_id=manifest.hive.node_id,
            hive_id=manifest.hive.id,
            host=section.listen_host,
            port=section.listen_port,
        ),
        clock,
    )


def _virtual_backend_source(
    lifecycle: CellLifecycle, section: VirtualCellsSection, hive_id: HiveId
) -> VirtualBackendSource:
    """Build the closure `QueenDeps.virtual_backend_source` holds: live headroom, manifest specs."""
    specs = (_default_spec(section, hive_id),)

    async def source() -> tuple[VirtualBackendCandidate, ...]:
        return tuple(
            virtual_backend_candidate_from_lifecycle(backend, specs)
            for backend in lifecycle.virtual_backend_candidates()
        )

    return source


def _dormant_cell_source(lifecycle: CellLifecycle) -> DormantCellSource:
    """Build the closure `QueenDeps.dormant_cell_source` holds: the lifecycle's own dormant list."""

    async def source() -> tuple[DormantCandidate, ...]:
        return tuple(
            dormant_candidate_from_lifecycle(cell) for cell in lifecycle.dormant_candidates()
        )

    return source


def _default_spec(section: VirtualCellsSection, hive_id: HiveId) -> VirtualCellSpec:
    """Build the one `VirtualCellSpec` template `[virtual_cells]`'s own defaults describe."""
    # The capacity placement reads is the reservation's own, computed exactly as the Cell will
    # compute it from its bootstrap (hivemind.hive.models.CellReservation): one set of figures.
    reservation = CellReservation(
        cpu_cores=section.cpu_cores,
        memory_bytes=section.memory_bytes,
        disk_bytes=section.disk_bytes,
        max_sub_bees=_DEFAULT_MAX_SUB_BEES,
    )
    capacity = reservation.capacity(arch=_VIRTUAL_CELL_ARCH, os=WireOsFamily.LINUX)
    return VirtualCellSpec(
        image=section.default_image,
        cpu_cores=section.cpu_cores,
        memory_bytes=section.memory_bytes,
        disk_bytes=section.disk_bytes,
        network_policy=_network_policy(section.network_policy),
        read_only_rootfs=section.read_only_rootfs,
        capacity=capacity,
        comb_shield=CombShieldLevel.MEADOW,
        ready_timeout_s=section.ready_timeout_s,
        hive_id=hive_id,
    )


def _network_policy(name: NetworkPolicyName) -> NetworkPolicy:
    """Convert the manifest's own string literal into `hivemind.hive.NetworkPolicy`."""
    return {
        "none": NetworkPolicy.NONE,
        "egress_only": NetworkPolicy.EGRESS_ONLY,
        "allowlist": NetworkPolicy.ALLOWLIST,
    }[name]


def _fail_closed_night_veil_probe(cell: Cell) -> NightVeilProbe:
    """Refuse to attest any NIGHT_VEIL Cell: no Queen-side CellSession path exists yet.

    Roadmap step 5.7b's own gap (module docstring's "Night Veil probe wiring"): a real
    `hive.night_veil.SessionNightVeilProbe` needs a live `hivemind.cell.CellSession` for the Cell
    being attested, and nothing here can build one from a bare `hivemind.cell.Cell` yet -- the
    same missing seam `_null_scrub` below documents for Overwintering's own scrub step. Raising
    here, rather than quietly handing back a `FakeNightVeilProbe`, means a NIGHT_VEIL placement
    fails loudly and immediately instead of "passing" an attestation that checked nothing real
    (codingrules 14.4: fakes are for tests, never a silent production default).

    Raises:
        NotImplementedError: Always; `LifecycleVirtualCellProvider._attest_or_teardown` catches
            this like any other probe failure, tears `cell` down and raises `CellProvisionError`.
    """
    raise NotImplementedError(
        f"Night Veil attestation has no real probe wired up yet for Cell {cell.id}: no "
        "CellSession path exists from the Queen to a Virtual Cell (roadmap step 5.7b's own gap). "
        "Wire a SessionNightVeilProbe factory here once one does."
    )


async def _null_scrub(cell: object) -> None:
    """The Scrubber `make_on_task_finished` uses: no in-Cell session exists yet to scrub.

    Documented simplification (this dispatch's own report): a real scrub would stop every
    sub-bee and clear scratch over a live `hivemind.cell.CellSession` on the Cell being
    overwintered; that session-opening seam is a later step (the real in-Cell Warden this whole
    branch's own report names as still stubbed). A Cell admitted to the pool without ever being
    scrubbed is still paused correctly -- the pool's own bookkeeping and the backend's own pause
    do not depend on this -- so this is safe, only incomplete.
    """
