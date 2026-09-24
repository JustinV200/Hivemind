"""Define the ``[placement]`` and ``[virtual_cells]`` sections: real-vs-virtual policy, defaults.

Roadmap step 5.7 (ADR-0028): ``PlacementSection`` is the manifest's own face of `hivemind.queen.
placement.policy.PlacementPolicy` -- ``prefer`` (which side placement honours when both a Real and
a Virtual candidate fit), ``allow_hive_stand`` (whether the Hive Stand may still be chosen) and
``[placement.roles.<role>]`` overrides, keyed by lowercase ``waggle.messages.task.WorkerRole``
member name, the same convention ``hivemind.manifest.schema.forage.ForageSection.roles`` already
follows. ``VirtualCellsSection`` is ``[virtual_cells]``: the manifest-level default shape of a
freshly provisioned Virtual Cell (which `hivemind.hive.backends.base.CellBackend` to provision
from, its default image, cpu, memory, disk, outbound network shape, how long to wait for it to
become ready, and the most Virtual Cells the Hive may hold at once) plus a nested
``[virtual_cells.overwinter]`` (``docs/adr/0029-overwintering-policy.md``: whether the pool is
enabled at all, its own size caps, how long a dormant Cell may sit before the Undertaker destroys
it, and the disk Forage the manifest allots to it). Every field defaults such that a manifest that
omits both sections entirely loads unchanged and behaves exactly as today: ``prefer = "real"``,
``allow_hive_stand = true``, and ``[virtual_cells] backend`` unset, which
`hivemind.queen.placement.policy.PlacementPolicy.default_virtual_spec` reads as "no Virtual side
configured at all" (the composition root never builds a template from an unset backend).
``network_policy`` is a plain string literal here, not `hivemind.hive.NetworkPolicy`: `manifest` is
Layer 1 and `hive` is Layer 3, so this module may never import it (codingrules section 4); the
composition root converts the string once it already holds both.

Roadmap step 5.10 adds ``snapshot_retention_s``/``snapshot_disk_budget_mb`` to
``VirtualCellsSection``: how long a Capping snapshot (``hivemind.hive.snapshot``) survives and how
much disk one Cell's own live snapshots may hold before the oldest is evicted, mirroring
``VirtualCellsOverwinterSection.disk_budget_mb``'s own "accounted as Forage" documentation for a
different pool of disk.

Roadmap step 5.6 (this branch) adds ``listen_host``/``listen_port``/``advertise_url`` to
``VirtualCellsSection``: where the Queen's own `queen.cell_gate.CellListener` binds its WebSocket
server (``listen_host``/``listen_port``, loopback by default like every other Waggle listener,
``waggle.transport.websocket_server.WebSocketServer``'s own default) and the URL a provisioned
Cell is actually told to dial (``advertise_url``, optional: unset means "use the bound
``listen_host``/``listen_port`` directly", which only works when the Cell can reach that host --
never true for a Docker container reaching its own host, hence the field). This section carries no
Ed25519 key material of its own: `hivemind.hive.backends.bootstrap.QueenEndpoint` (a later
composition-root value, not a manifest field) is where the Queen's own key hex is threaded in.

Roadmap step 10.6a adds ``control_subnet`` (Docker only): the private IPv4 subnet of the Hive's
internal control network, which dual-homes every Docker Cell so isolation can cut its egress and
keep its Waggle link (`hivemind.hive.backends.docker.network`). Its first host is the gateway
(``control_gateway``): the validator holds ``listen_host`` to it, and ``advertise_url`` too when
set, since a Cell reaches the listener there and nowhere else once its egress is cut.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``. Calls into ``waggle.messages.task``
    (WorkerRole, for the same role-key validator ``forage.py`` already runs) only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - ``PlacementSection.roles`` rejects a key that is not a lowercase ``WorkerRole`` member name,
      mirroring ``ForageSection``'s own validator; unlike ``roles`` there, no role is *required* to
      be present, since `hivemind.queen.placement.policy.PlacementPolicy.prefer_for` already falls
      back to the top-level ``prefer`` for any role the manifest never names.
    - ``VirtualCellsSection.backend`` is ``None`` by default: "virtual backend unset => no virtual
      side" (roadmap step 5.7's own build instructions), so a manifest that never sets it keeps
      every task on the Real side, exactly as v0 always has.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the policy this section feeds.
    - docs/adr/0029-overwintering-policy.md for the ``[virtual_cells.overwinter]`` fields.
    - .claude/roadmap.md step 5.7 for the field-by-field description this module implements.
    - hivemind.manifest.schema.forage for ForageSection, the role-key validator pattern this module
      mirrors.
    - hivemind.queen.placement.policy for PlacementPolicy/VirtualSpecTemplate, the runtime shapes a
      composition root converts these sections into.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.messages.task import WorkerRole

# Which side hivemind.queen.placement.decide.decide honours (ADR-0028 rule 6).
PreferName = Literal["real", "virtual"]
# The network policies a manifest may name for a freshly provisioned Virtual Cell; VPN_TOR is
# Night Veil's own forced profile (hivemind.queen.placement.decide._place_night_veil) and is never
# a configurable default here.
NetworkPolicyName = Literal["none", "egress_only", "allowlist"]

DEFAULT_PREFER: PreferName = "real"  # v0's only behaviour: every task placed on a Real Cell.
DEFAULT_ALLOW_HIVE_STAND = True  # The Hive Stand is a candidate unless the operator excludes it.
DEFAULT_VIRTUAL_CELLS_IMAGE = "base-ubuntu"  # Terminal-first; matches images/base-ubuntu.
DEFAULT_VIRTUAL_CELLS_CPU_CORES = 1.0  # A modest default footprint for one fresh Virtual Cell.
DEFAULT_VIRTUAL_CELLS_MEMORY_BYTES = 1 * 1024**3  # 1 GiB.
DEFAULT_VIRTUAL_CELLS_DISK_BYTES = 8 * 1024**3  # 8 GiB.
# Deliberately wider than hivemind.hive.VirtualCellSpec's own NONE default: a Cell must dial out to
# the Queen through the host gateway (ADR-0027), and "none" is an `internal` Docker network, which
# on Docker Desktop has no route to host.docker.internal at all -- the Warden inside never
# connects, every placement times out and the task falls back to the Hive Stand (a real run,
# 2026-09-23). On native Linux an internal network reaches its own bridge's address and nothing
# else, docker0 (where host-gateway points) included; an operator who wants "none" there sets
# [virtual_cells] control_subnet too, whose gateway the link then rides (roadmap step 10.6a).
DEFAULT_VIRTUAL_CELLS_NETWORK_POLICY: NetworkPolicyName = "egress_only"
DEFAULT_READY_TIMEOUT_S = 60.0  # Matches hivemind.hive.models.DEFAULT_READY_TIMEOUT_S.
DEFAULT_MAX_CELLS = 4  # A conservative cap until the operator raises it deliberately.
DEFAULT_OVERWINTER_ENABLED = True  # docs/adr/0029: reuse is the point of the pool.
DEFAULT_OVERWINTER_MAX_CELLS = 4  # Matches [virtual_cells] max_cells by default.
DEFAULT_OVERWINTER_MAX_PER_IMAGE = 2  # A rare image never crowds out a common one.
DEFAULT_OVERWINTER_MAX_DORMANT_S = 3600.0  # One hour before the Undertaker sweeps it up.
DEFAULT_OVERWINTER_DISK_BUDGET_MB = 8192  # 8 GiB of disk Forage set aside for dormant Cells.
DEFAULT_VIRTUAL_CELLS_LISTEN_HOST = "127.0.0.1"  # Loopback: matches WebSocketServer's own default.
DEFAULT_VIRTUAL_CELLS_LISTEN_PORT = 0  # 0: let the OS choose, like WebSocketServer's own default.
# Roadmap step 5.10: how long a Capping snapshot survives, and how much disk one Cell's own
# snapshots may hold, before the oldest is evicted to make room for a new one
# (hivemind.hive.snapshot.SnapshotLedger). One hour and 4 GiB are generous defaults for a Cell
# whose disk itself defaults to 8 GiB (DEFAULT_VIRTUAL_CELLS_DISK_BYTES above).
DEFAULT_SNAPSHOT_RETENTION_S = 3600.0
DEFAULT_SNAPSHOT_DISK_BUDGET_MB = 4096
# A control subnet holds the gateway plus the Cells; a /29 (eight addresses) is the smallest.
MIN_CONTROL_SUBNET_ADDRESSES = 8
_IPV4 = 4  # ipaddress's own version number for an IPv4 network.

__all__ = [
    "DEFAULT_ALLOW_HIVE_STAND",
    "DEFAULT_MAX_CELLS",
    "DEFAULT_OVERWINTER_DISK_BUDGET_MB",
    "DEFAULT_OVERWINTER_ENABLED",
    "DEFAULT_OVERWINTER_MAX_CELLS",
    "DEFAULT_OVERWINTER_MAX_DORMANT_S",
    "DEFAULT_OVERWINTER_MAX_PER_IMAGE",
    "DEFAULT_PREFER",
    "DEFAULT_READY_TIMEOUT_S",
    "DEFAULT_SNAPSHOT_DISK_BUDGET_MB",
    "DEFAULT_SNAPSHOT_RETENTION_S",
    "DEFAULT_VIRTUAL_CELLS_CPU_CORES",
    "DEFAULT_VIRTUAL_CELLS_DISK_BYTES",
    "DEFAULT_VIRTUAL_CELLS_IMAGE",
    "DEFAULT_VIRTUAL_CELLS_LISTEN_HOST",
    "DEFAULT_VIRTUAL_CELLS_LISTEN_PORT",
    "DEFAULT_VIRTUAL_CELLS_MEMORY_BYTES",
    "DEFAULT_VIRTUAL_CELLS_NETWORK_POLICY",
    "MIN_CONTROL_SUBNET_ADDRESSES",
    "NetworkPolicyName",
    "PlacementRoleOverride",
    "PlacementSection",
    "PreferName",
    "VirtualCellsOverwinterSection",
    "VirtualCellsSection",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# Every lowercase WorkerRole member name, matching forage.py's own role-key validator constant.
_VALID_ROLE_KEYS = frozenset(role.name.lower() for role in WorkerRole)


class PlacementRoleOverride(BaseModel):
    """One ``[placement.roles.<role>]`` row: a `prefer` override for that role alone."""

    model_config = _MODEL_CONFIG

    prefer: PreferName | None = Field(
        default=None,
        description="Overrides [placement] prefer for this role; None inherits the top-level "
        "value (hivemind.queen.placement.policy.PlacementPolicy.prefer_for's own fallback).",
    )


class PlacementSection(BaseModel):
    """``[placement]``: which side placement honours, the Hive Stand's eligibility, overrides."""

    model_config = _MODEL_CONFIG

    prefer: PreferName = Field(
        default=DEFAULT_PREFER,
        description="Which side hivemind.queen.placement.decide.decide honours when both a Real "
        "and a Virtual candidate fit a task's TaskNeeds (ADR-0028 rule 6).",
    )
    allow_hive_stand: bool = Field(
        default=DEFAULT_ALLOW_HIVE_STAND,
        description="Whether the Hive Stand may still be chosen as a Real candidate; false "
        "excludes it outright (ADR-0028 rule 3), even as the only attached Warden.",
    )
    roles: dict[str, PlacementRoleOverride] = Field(
        default_factory=dict,
        description="[placement.roles.<role>] overrides, keyed by lowercase WorkerRole name; a "
        "role absent here simply inherits the top-level prefer.",
    )

    @model_validator(mode="after")
    def _roles_are_worker_roles(self) -> PlacementSection:
        """Reject a role key that is not a lowercase `WorkerRole` member name."""
        unknown = [key for key in self.roles if key not in _VALID_ROLE_KEYS]
        if unknown:
            raise ValueError(
                f"[placement.roles] has unknown role keys {unknown}; expected one of "
                f"{sorted(_VALID_ROLE_KEYS)}."
            )
        return self


class VirtualCellsOverwinterSection(BaseModel):
    """``[virtual_cells.overwinter]``: the Overwintering pool's own bounds (`docs/adr/0029`)."""

    model_config = _MODEL_CONFIG

    enabled: bool = Field(
        default=DEFAULT_OVERWINTER_ENABLED,
        description="Whether a released Virtual Cell may ever go DORMANT instead of being torn "
        "down; false always tears down (matching a Hive with no pool at all).",
    )
    max_cells: int = Field(
        default=DEFAULT_OVERWINTER_MAX_CELLS,
        gt=0,
        description="The most dormant Cells the pool holds in total, across every image.",
    )
    max_per_image: int = Field(
        default=DEFAULT_OVERWINTER_MAX_PER_IMAGE,
        gt=0,
        description="The most dormant Cells the pool holds for any one image.",
    )
    max_dormant_s: float = Field(
        default=DEFAULT_OVERWINTER_MAX_DORMANT_S,
        gt=0,
        description="Seconds a dormant Cell may sit before the Undertaker's sweep destroys it.",
    )
    disk_budget_mb: int = Field(
        default=DEFAULT_OVERWINTER_DISK_BUDGET_MB,
        gt=0,
        description="Disk, in megabytes, the manifest allots to the whole pool; accounted as "
        "Forage (codingrules section 8.12: 'Snapshots are accounted as Forage').",
    )


class VirtualCellsSection(BaseModel):
    """``[virtual_cells]``: the default shape of a freshly provisioned Virtual Cell, and the pool.

    ``backend`` is ``None`` by default: "virtual backend unset => no virtual side" (roadmap step
    5.7's own build instructions), so a manifest that never sets it keeps placement on the Real
    side alone, exactly as before this section existed.
    """

    model_config = _MODEL_CONFIG

    backend: Literal["docker", "qemu", "fake"] | None = Field(
        default=None,
        description="Which hivemind.hive.CellBackend registry name to provision from; None means "
        "no Virtual side is configured at all.",
    )
    default_image: str = Field(
        default=DEFAULT_VIRTUAL_CELLS_IMAGE,
        description="The images/<name> a freshly provisioned Virtual Cell boots by default.",
    )
    cpu_cores: float = Field(
        default=DEFAULT_VIRTUAL_CELLS_CPU_CORES, gt=0, description="Logical cores to reserve."
    )
    memory_bytes: int = Field(
        default=DEFAULT_VIRTUAL_CELLS_MEMORY_BYTES, gt=0, description="Memory to reserve."
    )
    disk_bytes: int = Field(
        default=DEFAULT_VIRTUAL_CELLS_DISK_BYTES, gt=0, description="Disk to reserve."
    )
    network_policy: NetworkPolicyName = Field(
        default=DEFAULT_VIRTUAL_CELLS_NETWORK_POLICY,
        description="The default outbound network shape; converted to hivemind.hive.NetworkPolicy "
        "by the composition root. Never 'vpn_tor': that profile is Night Veil's own forced "
        "default, not a configurable one (hivemind.queen.placement.decide._place_night_veil).",
    )
    read_only_rootfs: bool = Field(
        default=False,
        description="Whether a Virtual Cell's root filesystem is mounted read-only, leaving only "
        "scratch and /tmp writable. False by default: a Virtual Cell is AccessLevel.FULL and "
        "disposable (hivemind.hive.VirtualCellSpec.read_only_rootfs).",
    )
    ready_timeout_s: float = Field(
        default=DEFAULT_READY_TIMEOUT_S,
        gt=0,
        description="Seconds to wait for a freshly provisioned Cell to report ready.",
    )
    max_cells: int = Field(
        default=DEFAULT_MAX_CELLS,
        gt=0,
        description="The most Virtual Cells this Hive may hold running at once.",
    )
    snapshot_retention_s: float = Field(
        default=DEFAULT_SNAPSHOT_RETENTION_S,
        gt=0,
        description="Seconds a Capping snapshot (hivemind.hive.snapshot) survives before the "
        "next sweep expires it (roadmap step 5.10); matches the manifest's own retention policy "
        "for anything else the Hive keeps only for a bounded window.",
    )
    snapshot_disk_budget_mb: int = Field(
        default=DEFAULT_SNAPSHOT_DISK_BUDGET_MB,
        gt=0,
        description="Disk, in megabytes, one Cell's own live snapshots may hold; the oldest is "
        "evicted before a new snapshot that would exceed this is recorded (hivemind.hive.snapshot."
        "SnapshotLedger.room_for). Accounted as Forage disk, mirroring "
        "VirtualCellsOverwinterSection.disk_budget_mb's own accounting.",
    )
    overwinter: VirtualCellsOverwinterSection = Field(
        default_factory=VirtualCellsOverwinterSection,
        description="The Overwintering pool's own bounds (docs/adr/0029).",
    )
    listen_host: str = Field(
        default=DEFAULT_VIRTUAL_CELLS_LISTEN_HOST,
        description="The interface hivemind.queen.cell_gate.CellListener binds its WebSocket "
        "server on; loopback by default, matching every other Waggle listener.",
    )
    listen_port: Annotated[int, Field(ge=0, le=65535)] = Field(
        default=DEFAULT_VIRTUAL_CELLS_LISTEN_PORT,
        description="The port to bind; 0 lets the OS choose one (waggle.transport.websocket_"
        "server.WebSocketServer's own default).",
    )
    advertise_url: str | None = Field(
        default=None,
        description="The ws:// or wss:// URL a provisioned Cell is actually told to dial, when "
        "it differs from listen_host/listen_port (e.g. a Docker container reaching the host "
        "gateway alias, or a QEMU guest reaching the SLIRP gateway). None means the bound "
        "listen_host/listen_port is reachable from the Cell directly, or (backend = 'docker') "
        "that the composition root's own docker-host helper computes one "
        "(hivemind.cli.compose.virtual_cells).",
    )
    qemu_base_image: Path | None = Field(
        default=None,
        description="The prebuilt qcow2 every Cell boots from (backend = 'qemu' only; "
        "hivemind.hive.backends.qemu.QemuBackendConfig.base_image). Required when backend is "
        "'qemu'; the composition root raises a ConfigurationError if it is still unset then.",
    )
    qemu_vm_root: Path | None = Field(
        default=None,
        description="The directory every QEMU VM's own working directory lives under (backend = "
        "'qemu' only; hivemind.hive.backends.qemu.QemuBackendConfig.vm_root, and the matching "
        "ProcessQemuRunner's own vm_root). Required when backend is 'qemu'.",
    )
    control_subnet: str | None = Field(
        default=None,
        description="(backend = 'docker' only; roadmap step 10.6a) A private IPv4 CIDR for the "
        "Hive's internal control network: every Docker Cell whose link does not ride Tor joins it "
        "beside its own network, its Waggle link rides it, and so isolation can cut the Cell's "
        "egress and keep the link (hivemind.hive.backends.docker.network). listen_host must be "
        "its first host, the gateway, and advertise_url, if set, must name it too. None: no "
        "control network, and a Docker Cell's egress cannot be cut.",
    )

    @property
    def control_gateway(self) -> str | None:
        """The control subnet's first host, where the listener binds and Cells dial, or None."""
        if self.control_subnet is None:
            return None
        return str(next(ipaddress.ip_network(self.control_subnet, strict=True).hosts()))

    @model_validator(mode="after")
    def _control_subnet_fits(self) -> VirtualCellsSection:
        """Refuse a control subnet the Cells could not use: wrong backend, range or listener."""
        if self.control_subnet is None:
            return self
        network = ipaddress.ip_network(self.control_subnet, strict=True)
        if self.backend != "docker":
            raise ValueError("[virtual_cells] control_subnet is Docker's alone: set backend.")
        # A public range would have the host answer Cells on an address the world may route to.
        if network.version != _IPV4 or not network.is_private:
            raise ValueError(f"control_subnet {network} must be a private IPv4 network.")
        if network.num_addresses < MIN_CONTROL_SUBNET_ADDRESSES:
            raise ValueError(f"control_subnet {network} is smaller than a /29.")
        gateway = self.control_gateway
        # The listener must be on the one address a Cell can reach there, and a Cell must be
        # told that address, or the first cut takes its link with its egress.
        if self.listen_host != gateway:
            raise ValueError(f"listen_host must be the control gateway {gateway}.")
        if self.advertise_url is not None and urlsplit(self.advertise_url).hostname != gateway:
            raise ValueError(f"advertise_url must name the control gateway {gateway}.")
        return self
