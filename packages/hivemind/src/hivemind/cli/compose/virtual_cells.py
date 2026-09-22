"""Wire up Virtual Cells when `[virtual_cells] backend` is set: build_virtual_cells.

Roadmap step 5.6's own composition-root half. `build_virtual_cells` returns `None` when
`manifest.virtual_cells.backend` is unset -- "virtual backend unset => no virtual side" (roadmap
step 5.7), so a manifest that never sets it builds a `Hive` exactly as before this module existed
(codingrules section 13: the composition root converts a manifest slice; nothing below it ever sees
one). When it is set, this module builds every Layer-3/Layer-6 collaborator roadmap steps 5.1-5.9
landed and were reconciled by this same dispatch: a `hivemind.hive.registry.BackendRegistry` with
`"fake"` always registered plus whichever of `"docker"`/`"qemu"` the manifest actually selects
(never all three unconditionally -- see `_build_registry`'s own docstring for why: `hivemind.hive.
lifecycle.CellLifecycle.virtual_backend_candidates` force-constructs every registered backend on
every placement snapshot, so an unselected, unavailable backend must never be registered at all).
Even the selected one is still built lazily inside its own factory (its own `ConfigurationError`
-- `hivemind.hive.backends.docker.sdk_client.SdkDockerClient` already raises one naming the
`hivemind[docker]` extra when the SDK is missing -- only surfaces the first time `.get()` actually
constructs it), a `hivemind.queen.cell_gate.gate.QueenReadinessGate`,
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
untouched; this is the one place that rewrite happens instead.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.cell` (CellIdentity),
    `hivemind.common.errors` (ConfigurationError), `hivemind.forage` (ForageCapacity,
    HostCapacity), `hivemind.hive` (BackendRegistry, CellLifecycle, NetworkPolicy, OverwinterConfig,
    OverwinterPool, OverwinterSettings, VirtualCellSpec, build_docker_backend, build_qemu_backend,
    mint_cell_bootstrap is not used here -- backends mint their own), `hivemind.hive.backends.
    docker` (SdkDockerClient), `hivemind.hive.backends.qemu` (ProcessQemuRunner, QemuBackendConfig),
    `hivemind.manifest` (HiveManifest), `hivemind.pheromone` (PheromoneTrail), `hivemind.queen.
    cell_gate` (CellListener, CellListenerDeps, LifecycleVirtualCellProvider, QueenReadinessGate,
    make_on_task_finished), `hivemind.queen.dispatcher.snapshot` (the two hive-to-queen candidate
    converters), `hivemind.queen.placement` (VirtualBackendCandidate) and waggle only.

Key invariants:
    - `build_virtual_cells` returns `None` whenever `manifest.virtual_cells.backend` is `None`, and
      touches nothing else in that case (module docstring).
    - The `docker`/`qemu` backend factories are only ever called by `BackendRegistry.get`, itself
      only ever called once the Queen actually needs that backend -- neither the `docker` package
      nor `qemu_base_image`/`qemu_vm_root` being unset can ever break building a Hive that never
      uses that backend.

See Also:
    - .claude/roadmap.md step 5.6 for the composition-root wiring this module implements.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for QueenEndpoint and
      why a Cell's own dial-back address needs backend-specific resolution.
    - hivemind.cli.compose.hive for build_hive/run_hive, this module's one caller and the
      listener's own start/stop lifetime owner.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CellIdentity, CombShieldLevel
from hivemind.cli.stores import open_snapshot_ledger
from hivemind.common.errors import ConfigurationError
from hivemind.forage import ForageCapacity, HostCapacity
from hivemind.hive import (
    BackendRegistry,
    CellLifecycle,
    NetworkPolicy,
    OverwinterConfig,
    OverwinterPool,
    OverwinterSettings,
    VirtualCellSpec,
    build_docker_backend,
    build_qemu_backend,
)
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.docker.sdk_client import SdkDockerClient
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.backends.qemu.backend import QemuBackendConfig, QemuCellBackend
from hivemind.hive.backends.qemu.process_runner import ProcessQemuRunner
from hivemind.manifest import HiveManifest
from hivemind.manifest.schema.placement import NetworkPolicyName, VirtualCellsSection
from hivemind.pheromone import PheromoneTrail
from hivemind.queen.cell_gate import (
    CellListener,
    CellListenerDeps,
    CellSnapshotHandler,
    LifecycleVirtualCellProvider,
    QueenReadinessGate,
    make_on_task_finished,
)
from hivemind.queen.deps import DormantCellSource, OnTaskFinished, VirtualBackendSource
from hivemind.queen.dispatcher.snapshot import (
    dormant_candidate_from_lifecycle,
    virtual_backend_candidate_from_lifecycle,
)
from hivemind.queen.placement import DormantCandidate, VirtualBackendCandidate
from hivemind.queen.trail_sync import TrailSegmentReceiver
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId
from waggle.messages import OsFamily as WireOsFamily
from waggle.signing import Ed25519Signer, public_key_hex
from waggle.uris import check_waggle_uri

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
        registry: Every registered CellBackend ("fake" always; "docker"/"qemu" lazily).
        gate: Resolves once a provisioned Cell's own handshake verifies.
        listener: Accepts every Virtual Cell's outbound connection; `run_hive` starts/stops it.
        lifecycle: Owns every Virtual Cell state edge and backend call.
        provider: The real `VirtualCellProvider`; `run_hive` binds the live `Queen` to it once one
            exists (`LifecycleVirtualCellProvider.bind_queen`).
        virtual_backend_source: `QueenDeps.virtual_backend_source`'s own live feed.
        dormant_cell_source: `QueenDeps.dormant_cell_source`'s own live feed.
        on_task_finished: `QueenDeps.on_task_finished`'s own implementation.
    """

    registry: BackendRegistry
    gate: QueenReadinessGate
    listener: CellListener
    lifecycle: CellLifecycle
    provider: LifecycleVirtualCellProvider
    virtual_backend_source: VirtualBackendSource
    dormant_cell_source: DormantCellSource
    on_task_finished: OnTaskFinished


def build_virtual_cells(
    manifest: HiveManifest, trail: PheromoneTrail, clock: Clock
) -> VirtualCellsParts | None:
    """Build every Virtual Cell collaborator, or None when `[virtual_cells] backend` is unset.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        trail: Where every `cell.*` event the lifecycle drives lands.
        clock: Injected time source shared by every collaborator this builds.

    Returns:
        A VirtualCellsParts ready for `hivemind.cli.compose.hive.build_hive` to fold into
        `QueenDeps` and `run_hive` to start/stop, or `None` when no Virtual side is configured.
    """
    section = manifest.virtual_cells
    if section.backend is None:
        return None
    queen_signer = Ed25519Signer.generate()
    gate = QueenReadinessGate()
    listener = _build_listener(manifest, section, gate, queen_signer, clock)
    ctx = _RegistryContext(manifest, section, gate, listener, queen_signer, manifest.hive.node_id)
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
    if section.backend in ("docker", "qemu"):
        # "fake" (dev/test only) never declares can_snapshot=True (hivemind.hive.backends.fake's
        # own default), so it would only ever draw NoopSnapshotter -- opening a real SQLite
        # connection to attach a SnapshotLedgerPort no Cell of that backend can ever use would be
        # pure overhead (and, in a short-lived test process, an unclosed file handle nothing here
        # ever gets a chance to release). Only a real backend gets the relay wired in.
        _attach_snapshot_and_trail(manifest, listener, lifecycle, trail, clock)
    return VirtualCellsParts(
        registry=registry,
        gate=gate,
        listener=listener,
        lifecycle=lifecycle,
        provider=LifecycleVirtualCellProvider(lifecycle, gate),
        virtual_backend_source=_virtual_backend_source(lifecycle, section, manifest.hive.id),
        dormant_cell_source=_dormant_cell_source(lifecycle),
        on_task_finished=make_on_task_finished(lifecycle, _null_scrub),
    )


def _attach_snapshot_and_trail(
    manifest: HiveManifest,
    listener: CellListener,
    lifecycle: CellLifecycle,
    trail: PheromoneTrail,
    clock: Clock,
) -> None:
    """Wire the snapshot relay and the offline trail sync into `listener` and `lifecycle`.

    Roadmap step 5.10's own follow-up gap (the snapshot relay): a durable ledger so `hive cells
    snapshot`/`hive cells rollback` and this running Queen all read and write the same book, and
    the handler `listener` answers a Warden's own `CellSnapshotRequest`/`CellRollbackRequest`
    through -- bound after `listener` exists (the same late-binding shape `LifecycleVirtualCell
    Provider.bind_queen` already uses), since `listener` itself has to exist first for
    `_build_registry`'s own lazy endpoint closures.
    """
    ledger = open_snapshot_ledger(manifest.resolve_path(manifest.hive.db))
    lifecycle.attach_snapshot_ledger(ledger)
    listener.bind_snapshot_handler(CellSnapshotHandler(lifecycle, ledger, clock))
    listener.bind_trail_receiver(TrailSegmentReceiver(trail))


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


def docker_gateway_url(listener_uri: str) -> str:
    """Rewrite a loopback listener URI so a Docker container can dial it back.

    Docker Desktop and modern Docker Engine installs both resolve `host.docker.internal` to the
    host's own loopback from inside a container; a bare `127.0.0.1`/`localhost` inside a container
    means the container itself, never the host (module docstring).

    Args:
        listener_uri: `CellListener.uri`, e.g. `"ws://127.0.0.1:54321"`.

    Returns:
        The same URI with a loopback host replaced by `host.docker.internal`; unchanged if the
        host is already something else (an operator-set `advertise_url` wins over this entirely --
        see `_endpoint_for`, this function's one caller).
    """
    return listener_uri.replace("127.0.0.1", "host.docker.internal").replace(
        "localhost", "host.docker.internal"
    )


@dataclass(frozen=True, slots=True)
class _RegistryContext:
    """Everything `_build_registry`'s own lazy `docker`/`qemu` factories need (codingrules 5.1)."""

    manifest: HiveManifest
    section: VirtualCellsSection
    gate: QueenReadinessGate
    listener: CellListener
    queen_signer: Ed25519Signer
    queen_node_id: NodeId


def _build_registry(ctx: _RegistryContext, clock: Clock) -> BackendRegistry:
    """Register "fake" (always) plus whichever of "docker"/"qemu" is actually selected.

    Deviation from a literal "always register all three" (this dispatch's own report): `hivemind.
    hive.lifecycle.CellLifecycle.virtual_backend_candidates` -- existing code, not this dispatch's
    own -- calls `BackendRegistry.get` for *every* registered name to read its own capabilities,
    on every placement snapshot (`hivemind.queen.dispatcher.snapshot.build_inventory`, every
    tick). Registering "docker"/"qemu" unconditionally would force-construct a `SdkDockerClient()`
    (raising `ConfigurationError` without the `docker` package installed) or a `ProcessQemuRunner`
    with no configured `vm_root`, on the very first tick, for a Hive that only asked for "fake" or
    only actually plans to use one of the other two. Registering only "fake" (always) plus the
    manifest's own selected `section.backend` keeps `BackendRegistry`'s own documented invariant
    ("a Hive that never uses a given backend should never pay to construct it") intact.
    """
    registry = BackendRegistry()
    registry.register("fake", lambda: FakeCellBackend(clock))
    if ctx.section.backend == "docker":
        registry.register("docker", lambda: _build_docker(ctx, clock))
    elif ctx.section.backend == "qemu":
        registry.register("qemu", lambda: _build_qemu(ctx, clock))
    return registry


def _build_docker(ctx: _RegistryContext, clock: Clock) -> DockerCellBackend:
    """Construct the real DockerCellBackend; only ever called once "docker" is actually needed."""
    endpoint = _endpoint_for(ctx, docker=True)
    factory = build_docker_backend(
        SdkDockerClient(), ctx.gate, endpoint, clock, max_cells=ctx.section.max_cells
    )
    return factory()


def _build_qemu(ctx: _RegistryContext, clock: Clock) -> QemuCellBackend:
    """Construct the real QemuCellBackend; only ever called once "qemu" is actually needed."""
    section = ctx.section
    if section.qemu_base_image is None or section.qemu_vm_root is None:
        raise ConfigurationError(
            "[virtual_cells] backend = 'qemu' requires qemu_base_image and qemu_vm_root to both "
            "be set."
        )
    vm_root = ctx.manifest.resolve_path(section.qemu_vm_root)
    base_image = ctx.manifest.resolve_path(section.qemu_base_image)
    config = QemuBackendConfig(base_image=base_image, vm_root=vm_root, max_cells=section.max_cells)
    endpoint = _endpoint_for(ctx, docker=False)
    factory = build_qemu_backend(
        ProcessQemuRunner(vm_root), ctx.gate, endpoint, clock, config=config
    )
    return factory()


def _endpoint_for(ctx: _RegistryContext, *, docker: bool) -> QueenEndpoint:
    """Resolve the Queen's own dial-back URL, lazily (module docstring): `listener.uri` by then."""
    url = ctx.section.advertise_url
    if url is None:
        url = docker_gateway_url(ctx.listener.uri) if docker else ctx.listener.uri
    validated = check_waggle_uri(url, allow_virtual_cell_gateway_host=True)
    return QueenEndpoint(
        waggle_url=validated,
        queen_node_id=ctx.queen_node_id,
        queen_verify_key_hex=public_key_hex(ctx.queen_signer.public_key_bytes),
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
    capacity = ForageCapacity(
        host=HostCapacity(
            cores=max(1, int(section.cpu_cores)),
            memory_bytes=section.memory_bytes,
            memory_free_bytes=section.memory_bytes,
            disk_bytes=section.disk_bytes,
            disk_free_bytes=section.disk_bytes,
            cpu_load=0.0,
            gpus=(),
            arch=_VIRTUAL_CELL_ARCH,
            os=WireOsFamily.LINUX,
        ),
        local_seats=(),
        max_sub_bees=_DEFAULT_MAX_SUB_BEES,
    )
    return VirtualCellSpec(
        image=section.default_image,
        cpu_cores=section.cpu_cores,
        memory_bytes=section.memory_bytes,
        disk_bytes=section.disk_bytes,
        network_policy=_network_policy(section.network_policy),
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


async def _null_scrub(cell: object) -> None:
    """The Scrubber `make_on_task_finished` uses: no in-Cell session exists yet to scrub.

    Documented simplification (this dispatch's own report): a real scrub would stop every
    sub-bee and clear scratch over a live `hivemind.cell.CellSession` on the Cell being
    overwintered; that session-opening seam is a later step (the real in-Cell Warden this whole
    branch's own report names as still stubbed). A Cell admitted to the pool without ever being
    scrubbed is still paused correctly -- the pool's own bookkeeping and the backend's own pause
    do not depend on this -- so this is safe, only incomplete.
    """
