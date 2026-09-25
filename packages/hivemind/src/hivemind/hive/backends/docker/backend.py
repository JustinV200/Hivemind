"""Provide DockerCellBackend: provision, destroy, pause and list Virtual Cells on a Docker daemon.

The Docker backend (ADR-0026: "Docker first and QEMU second", ADR-0027: "connect outbound only,
boot a Warden") is the first working `hivemind.hive.backends.base.CellBackend`. It never talks to
the Docker daemon or the Queen-side readiness machinery directly: it is built entirely from three
collaborators handed to its constructor -- a `hivemind.hive.backends.docker.client.
DockerClientPort` (the real `SdkDockerClient` or, in every test, `FakeDockerClient`), a
`hivemind.hive.backends.bootstrap.ReadinessGate` (the real Queen-side gate, a later roadmap step,
or `FakeReadinessGate`), and a `hivemind.hive.backends.bootstrap.QueenEndpoint` (where the Queen
is, as reachable from inside a Cell). `hivemind.hive.backends.docker.network` decides what network
each `VirtualCellSpec.network_policy` needs; this module does everything else: resource-limit
mapping, the all-or-nothing provisioning sequence and its cleanup, idempotent destroy, label-only
listing, and pause/resume. Roadmap step 10.6a: given the Hive's control network (`[virtual_cells]
control_subnet`), it dual-homes every Cell whose link does not ride Tor -- created on the control
network, attached to its own per-policy network before it starts, and dialling the control
gateway -- and declares `can_cut_egress`, carried out by `hivemind.hive.backends.docker.egress`.

Fits into the Hive:
    Layer 3 (sources of Cells). Implements `hivemind.hive.backends.base.CellBackend`; constructed
    by the composition root (a later phase's `cli/`) when `[hive] backend = "docker"` and
    registered through `hivemind.hive.registry.BackendRegistry`. Calls into hivemind.cell,
    hivemind.hive.backends.base, hivemind.hive.backends.bootstrap, hivemind.hive.backends.docker
    (client, egress, network), hivemind.hive.cell_state, hivemind.hive.errors, hivemind.hive.models
    and waggle only.

Key invariants:
    - provision() either returns a Cell of kind VIRTUAL or raises CellProvisionError; any resource
      already created (network, volume, container) is removed before the error is raised, and the
      ReadinessGate registration is forgotten too (codingrules Appendix A.1: all-or-nothing).
    - destroy() is idempotent even after this backend's own process restarted with no in-memory
      state: every resource name is recomputed from `cell_id` alone
      (`hivemind.hive.backends.docker.network.network_name`, this module's own `container_name`/
      `_volume_name`), never looked up in a table this instance might not still hold.
    - A Night Veil Cell's container runs with the `none` log driver: nothing it prints reaches a
      daemon log (`docker logs`, a json-file on the host) that would outlive it (codingrules 12).
    - `spec.disk_bytes` is not enforced: Docker's per-container disk quota
      (`storage_opt={"size": ...}`) needs a storage driver most default installs -- Docker Desktop
      over WSL2 included, the dev host ADR-0026 names -- do not provide, so setting it would break
      provisioning on the very host this backend is meant to work on first. Documented here and in
      this package's README rather than silently ignored.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md for the CellBackend contract this
      class implements.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the readiness
      handshake (CellReady plus the first Heartbeat) `provision()` waits on through ReadinessGate.
    - hivemind.hive.backends.docker.network for exactly what each NetworkPolicy enforces, and the
      dual-homing a control network adds.
    - hivemind.hive.backends.docker.client for DockerClientPort and its value types.
    - hivemind.hive.backends.docker.fake and hivemind.hive.backends.fake for the two fakes this
      backend is tested against with no real Docker daemon or Queen.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from hivemind.cell import AccessLevel, Cell, CellKind, CombShieldLevel
from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.backends.bootstrap import (
    CellBootstrap,
    CellReadyInfo,
    QueenEndpoint,
    ReadinessGate,
    cell_endpoint,
    mint_cell_bootstrap,
)
from hivemind.hive.backends.docker.client import (
    ContainerInfo,
    ContainerSpec,
    DockerClientError,
    DockerClientPort,
    VolumeSpec,
)
from hivemind.hive.backends.docker.egress import cut_egress, ensure_control, restore_egress
from hivemind.hive.backends.docker.network import (
    ControlNetwork,
    NetworkPlan,
    network_name,
    plan_network,
)
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import (
    BackendCapabilityError,
    CellDestroyError,
    CellEgressError,
    CellProvisionError,
)
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import Clock
from waggle.ids import CellId, HiveId

_BACKEND_NAME = "docker"
# images/base-ubuntu/README.md's own DEFAULT_SCRATCH_ROOT: the scratch volume mounts exactly here.
_SCRATCH_MOUNT_PATH = "/var/lib/hivemind/scratch"

# tmpfs mount target, not a host temp directory this process itself reads or writes.
_TMP_MOUNT_PATH = "/tmp"  # noqa: S108  # SAFETY: container-internal tmpfs mount, not a host path.
_DEFAULT_PIDS_LIMIT = 256  # Generous for a Warden plus a handful of sub-bees; bounds a fork bomb.
_NANOS_PER_CPU = 1_000_000_000  # Docker's nano_cpus unit: billionths of one logical CPU.
_LABEL_HIVE_ID = "hivemind.hive_id"
_LABEL_CELL_ID = "hivemind.cell_id"
_LABEL_IMAGE = "hivemind.image"
_LABEL_COMB_SHIELD = "hivemind.comb_shield"
# Roadmap step 5.7a: the only image whose own nftables kill-switch actually enforces VPN_TOR
# (images/night-veil-ubuntu, roadmap step 5.3a); provision() refuses VPN_TOR on any other image.
_NIGHT_VEIL_IMAGE = "night-veil-ubuntu"
# Codingrules 12: a Night Veil Cell's stdout and stderr reach no daemon log that outlives it.
_NIGHT_VEIL_LOG_DRIVER = "none"

__all__ = ["DockerBackendConfig", "DockerCellBackend", "build_docker_backend", "container_name"]


@dataclass(frozen=True, slots=True)
class DockerBackendConfig:
    """What `DockerCellBackend` needs beyond client/gate/endpoint/clock, bundled (codingrules 5.1).

    Mirrors `hivemind.hive.backends.qemu.backend.QemuBackendConfig`: one value rather than more
    keyword arguments once the constructor's parameter count would cross the limit.

    Attributes:
        max_cells: The most Cells the backend may hold at once, or None for no cap of its own
            beyond whatever the daemon itself enforces.
        control: The Hive's control network (roadmap step 10.6a), or None: with one, every Cell
            whose link does not ride Tor is dual-homed and its egress can be cut.
    """

    max_cells: int | None = None
    control: ControlNetwork | None = None


class DockerCellBackend:
    """CellBackend over a Docker daemon, driven entirely through its injected collaborators."""

    def __init__(
        self,
        client: DockerClientPort,
        gate: ReadinessGate,
        endpoint: QueenEndpoint,
        clock: Clock,
        config: DockerBackendConfig | None = None,
    ) -> None:
        """Create a DockerCellBackend with nothing provisioned yet.

        Args:
            client: How this backend talks to Docker; `SdkDockerClient` for a real daemon,
                `FakeDockerClient` for tests.
            gate: How this backend learns a Cell has become reachable.
            endpoint: Where and who the Queen is, as every provisioned Cell must reach her.
            clock: Source of every minted CellId (`hivemind.hive.backends.bootstrap.
                mint_cell_bootstrap`).
            config: Its headroom and the Hive's control network; the defaults (no cap, no control
                network) when None.
        """
        config = config if config is not None else DockerBackendConfig()
        self._client = client
        self._control = config.control
        self._gate = gate
        self._endpoint = endpoint
        self._clock = clock
        self._max_cells = config.max_cells
        # In-process bookkeeping only, for `capabilities.headroom`: `list_cells` and `destroy`
        # never read this, since they work from Docker's own labels and deterministic names
        # instead (this module's own key invariant: destroy survives a process restart).
        self._active_ids: set[CellId] = set()

    @property
    def name(self) -> str:
        """This backend's registry name, "docker"."""
        return _BACKEND_NAME

    @property
    def client(self) -> DockerClientPort:
        """This backend's own DockerClientPort, for `hivemind.hive.snapshot.snapshotter_for`.

        Roadmap step 5.10: the snapshotter factory builds a `DockerSnapshotter` over the exact
        same client this backend provisions and destroys through, rather than opening a second
        connection to the daemon.
        """
        return self._client

    @property
    def capabilities(self) -> BackendCapabilities:
        """Docker can snapshot (5.10), pause, hold Night Veil, and cut egress on a control network.

        Night Veil: its image runs the kill-switch (5.3a), its container logs nowhere, and its
        container, network, volume and snapshot images are removed at its teardown.
        """
        headroom = (
            None if self._max_cells is None else max(0, self._max_cells - len(self._active_ids))
        )
        return BackendCapabilities(
            can_snapshot=True,
            can_pause=True,
            headroom=headroom,
            can_cut_egress=self._control is not None,
            can_night_veil=True,
        )

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """See `CellBackend.provision`."""
        self._check_headroom(spec)
        if spec.network_policy is NetworkPolicy.VPN_TOR and spec.image != _NIGHT_VEIL_IMAGE:
            # The in-image nftables kill-switch is VPN_TOR's only real enforcement (module
            # docstring); a spec that does not boot that image must never be accepted, whatever
            # else it asks for -- refusing here is cheaper than failing partway through.
            raise CellProvisionError(
                self.name,
                spec.image,
                f"VPN_TOR requires image={_NIGHT_VEIL_IMAGE!r} (roadmap step 5.3a), so its own "
                "kill-switch is what actually enforces this Cell's network policy",
            )
        # Roadmap step 10.3a: a NIGHT_VEIL Cell dials the hidden service through Tor, or is
        # refused here before anything exists; every other tier keeps this backend's endpoint.
        endpoint = cell_endpoint(self._endpoint, spec, self.name)
        bootstrap = mint_cell_bootstrap(spec.hive_id, endpoint, self._clock)
        plan = _plan(spec, bootstrap, self._control)
        # Registered before any infrastructure exists (ADR-0027): the Queen must be able to verify
        # this Cell's very first signed frame, which can arrive the instant the container starts.
        await self._gate.expect(bootstrap.cell_id, bootstrap.public_key_hex)
        try:
            cell = await self._provision_resources(spec, bootstrap, plan)
        except (DockerClientError, CellEgressError, TimeoutError) as exc:
            await self._gate.forget(bootstrap.cell_id)
            raise CellProvisionError(self.name, spec.image, str(exc)) from exc
        self._active_ids.add(bootstrap.cell_id)
        return cell

    async def _provision_resources(
        self, spec: VirtualCellSpec, bootstrap: CellBootstrap, plan: NetworkPlan
    ) -> Cell:
        """Create the networks, volume and container in order, cleaning up all of it on failure."""
        cell_id = bootstrap.cell_id
        created: list[tuple[str, str]] = []  # (kind, name), in creation order, for _cleanup.
        try:
            # The Hive's control network is shared and outlives every Cell: made or reused here,
            # never recorded for _cleanup.
            if plan.control is not None and self._control is not None:
                await ensure_control(self._client, self._control)
            await self._client.create_network(plan.spec)
            created.append(("network", plan.spec.name))
            volume_spec = _build_volume_spec(spec, cell_id)
            await self._client.create_volume(volume_spec)
            created.append(("volume", volume_spec.name))
            container_spec = _build_container_spec(spec, bootstrap, plan, volume_spec.name)
            await self._client.create_container(container_spec)
            created.append(("container", container_spec.name))
            if plan.control is not None:
                # Dual-homed: created on the control network, given its own before it starts, so
                # its first packet already has both (hivemind.hive.backends.docker.network).
                await self._client.connect_network(plan.spec.name, container_spec.name)
            await self._client.start_container(container_spec.name)
            # No timeout wrapper here: ReadinessGate.wait_ready's own `timeout_s` argument is the
            # deadline (its contract: raises TimeoutError past it), so a second one would only
            # race the first for no benefit.
            ready_info = await self._gate.wait_ready(cell_id, spec.ready_timeout_s)
        except (DockerClientError, CellEgressError, TimeoutError):
            await self._cleanup(created)
            raise
        return _build_cell(spec, cell_id, self.name, ready_info)

    async def _cleanup(self, created: list[tuple[str, str]]) -> None:
        """Undo every resource `_provision_resources` made, most recently created first."""
        for kind, name in reversed(created):
            if kind == "container":
                await self._client.remove_container(name, force=True)
            elif kind == "volume":
                await self._client.remove_volume(name)
            else:
                await self._client.remove_network(name)

    def _check_headroom(self, spec: VirtualCellSpec) -> None:
        """Raise CellProvisionError before creating anything if this backend is already full."""
        if self._max_cells is not None and len(self._active_ids) >= self._max_cells:
            raise CellProvisionError(
                self.name, spec.image, f"at its headroom of {self._max_cells} cells"
            )

    async def destroy(self, cell_id: CellId) -> None:
        """See `CellBackend.destroy` (idempotent)."""
        try:
            await self._client.remove_container(container_name(cell_id), force=True)
            await self._client.remove_volume(_volume_name(cell_id))
            await self._client.remove_network(network_name(cell_id))
        except DockerClientError as exc:
            raise CellDestroyError(self.name, cell_id, str(exc)) from exc
        self._active_ids.discard(cell_id)
        await self._gate.forget(cell_id)

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """See `CellBackend.list_cells`: reads Docker's own labels, nothing this instance holds."""
        infos = await self._client.list_containers({_LABEL_HIVE_ID: str(hive_id)})
        return tuple(_to_record(info) for info in infos)

    async def pause(self, cell_id: CellId) -> None:
        """See `CellBackend.pause`."""
        if not self.capabilities.can_pause:
            raise BackendCapabilityError(self.name, "pause", cell_id=cell_id)
        await self._client.pause_container(container_name(cell_id))

    async def resume(self, cell_id: CellId) -> None:
        """See `CellBackend.resume`."""
        if not self.capabilities.can_pause:
            raise BackendCapabilityError(self.name, "resume", cell_id=cell_id)
        await self._client.unpause_container(container_name(cell_id))

    async def cut_egress(self, cell_id: CellId) -> None:
        """See `EgressCutter.cut_egress`: detach its own network, keep the control network."""
        if self._control is None:
            raise BackendCapabilityError(self.name, "cut_egress", cell_id=cell_id)
        await cut_egress(self._client, self._control, cell_id)

    async def restore_egress(self, cell_id: CellId) -> None:
        """See `EgressCutter.restore_egress`: attach its own network again."""
        if self._control is None:
            raise BackendCapabilityError(self.name, "restore_egress", cell_id=cell_id)
        await restore_egress(self._client, self._control, cell_id)


def build_docker_backend(
    client: DockerClientPort,
    gate: ReadinessGate,
    endpoint: QueenEndpoint,
    clock: Clock,
    config: DockerBackendConfig | None = None,
) -> Callable[[], DockerCellBackend]:
    """Close over this backend's collaborators and return a zero-arg factory for the registry.

    `hivemind.hive.registry.BackendRegistry.register` takes a `CellBackendFactory` -- a callable
    with no arguments -- because the registry itself never constructs collaborators (codingrules
    8.2: no service locator); the composition root builds them once and calls this function to get
    something it can hand to `register("docker", ...)`. The return type here is a plain
    `Callable[[], DockerCellBackend]` rather than an imported `CellBackendFactory` alias, so this
    module never needs `hivemind.hive.registry` -- a `DockerCellBackend` already satisfies
    `CellBackend` structurally, and `register`'s own parameter type accepts this callable as-is.

    Args:
        client: How the backend talks to Docker.
        gate: How the backend learns a Cell has become reachable.
        endpoint: Where and who the Queen is.
        clock: Source of every minted CellId.
        config: The backend's headroom and the Hive's control network, or None for neither.

    Returns:
        A callable that builds a fresh `DockerCellBackend` from the given collaborators each time
        it is called; `BackendRegistry.get` calls it at most once and caches the result.
    """

    def factory() -> DockerCellBackend:
        return DockerCellBackend(client, gate, endpoint, clock, config)

    return factory


def container_name(cell_id: CellId) -> str:
    """Return this Cell's deterministic container name; mirrors `network.network_name`."""
    return f"hivemind-cell-{cell_id}"


def _volume_name(cell_id: CellId) -> str:
    """Return this Cell's deterministic scratch-volume name; mirrors `network.network_name`."""
    return f"hivemind-cell-{cell_id}-scratch"


def _build_volume_spec(spec: VirtualCellSpec, cell_id: CellId) -> VolumeSpec:
    """Build the per-Cell scratch volume's spec."""
    return VolumeSpec(
        name=_volume_name(cell_id),
        labels={_LABEL_HIVE_ID: str(spec.hive_id), _LABEL_CELL_ID: cell_id},
    )


def _build_container_spec(
    spec: VirtualCellSpec, bootstrap: CellBootstrap, network_plan: NetworkPlan, volume_name: str
) -> ContainerSpec:
    """Map VirtualCellSpec onto ContainerSpec: resource limits, labels and least-privilege flags."""
    labels = {
        **spec.labels,
        _LABEL_HIVE_ID: str(spec.hive_id),
        _LABEL_CELL_ID: bootstrap.cell_id,
        _LABEL_IMAGE: spec.image,
        _LABEL_COMB_SHIELD: spec.comb_shield.value,
    }
    return ContainerSpec(
        name=container_name(bootstrap.cell_id),
        image=spec.image,
        environment=bootstrap.environment(),
        labels=labels,
        # Dual-homed: created on the control network; its own is attached before it starts.
        network_name=network_plan.control or network_plan.spec.name,
        extra_hosts=network_plan.extra_hosts,
        volume_name=volume_name,
        volume_mount_path=_SCRATCH_MOUNT_PATH,
        # NOTE: spec.disk_bytes is deliberately not mapped to Docker's storage_opt "size": that
        # option needs a storage driver (overlay2 with xfs pquota, devicemapper) most default
        # installs -- Docker Desktop over WSL2 included -- do not provide; see this module's own
        # docstring and hive/backends/README.md for the documented limitation.
        nano_cpus=int(spec.cpu_cores * _NANOS_PER_CPU),
        mem_limit_bytes=spec.memory_bytes,
        pids_limit=_DEFAULT_PIDS_LIMIT,
        cap_drop=("ALL",),
        # Roadmap step 5.7a: a VPN_TOR Cell's in-image nftables kill-switch needs CAP_NET_ADMIN to
        # load its own ruleset at boot, which cap_drop=("ALL",) above would otherwise strip; every
        # other network policy gets nothing back (least privilege, codingrules 15).
        cap_add=("NET_ADMIN",) if spec.network_policy is NetworkPolicy.VPN_TOR else (),
        security_opt=("no-new-privileges:true",),
        # The root filesystem is writable unless the spec says otherwise: a Virtual Cell is FULL
        # access (VirtualCellSpec.read_only_rootfs's own docstring). A tmpfs /tmp is mounted either
        # way so a read-only Cell still has the /tmp a Python process expects.
        read_only_rootfs=spec.read_only_rootfs,
        tmpfs={_TMP_MOUNT_PATH: ""},
        log_driver=_NIGHT_VEIL_LOG_DRIVER
        if spec.comb_shield is CombShieldLevel.NIGHT_VEIL
        else None,
    )


def _plan(
    spec: VirtualCellSpec, bootstrap: CellBootstrap, control: ControlNetwork | None
) -> NetworkPlan:
    """Plan the Cell's networks; refuse a dual-homed Cell that would not dial the control gateway.

    A Night Veil Cell's link rides Tor over its egress (it dials through a SOCKS proxy), so it is
    never dual-homed; any other Cell of a Hive with a control network is, and it must dial the
    gateway, or the first cut would take its link with the egress (network module docstring).

    Raises:
        CellProvisionError: A dual-homed Cell's endpoint names some other host.
    """
    endpoint = bootstrap.endpoint
    joins = control if endpoint.socks_proxy_url is None else None
    plan = plan_network(spec, bootstrap.cell_id, joins)
    if plan.control is not None and control is not None:
        host = urlsplit(endpoint.waggle_url).hostname
        if host != control.gateway:
            raise CellProvisionError(
                _BACKEND_NAME,
                spec.image,
                f"a Cell on the control network dials its gateway {control.gateway}, but its "
                f"endpoint names {host}; set [virtual_cells] listen_host to the gateway",
            )
    return plan


def _build_cell(
    spec: VirtualCellSpec, cell_id: CellId, source: str, ready_info: CellReadyInfo
) -> Cell:
    """Build the VIRTUAL, FULL-access Cell a successfully provisioned `spec` reports."""
    return Cell(
        id=cell_id,
        kind=CellKind.VIRTUAL,
        name=spec.image,
        source=source,
        capabilities=ready_info.capabilities,
        capacity=ready_info.capacity,
        access_level=AccessLevel.FULL,
        comb_shield=spec.comb_shield,
    )


def _to_record(info: ContainerInfo) -> VirtualCellRecord:
    """Build a VirtualCellRecord from one Docker container's labels and status."""
    cell_id = CellId(info.labels.get(_LABEL_CELL_ID, info.name))
    status = VirtualCellStatus.DORMANT if info.status == "paused" else VirtualCellStatus.READY
    return VirtualCellRecord(
        cell_id=cell_id,
        status=status,
        image=info.labels.get(_LABEL_IMAGE, ""),
        labels=dict(info.labels),
        created_at=info.created_at,
    )
