"""Define NetworkPolicy, VirtualCellSpec and CellReservation: one Virtual Cell, asked for and held.

A Virtual Cell (a VM or container the Hive provisions and later destroys or Overwinters, as
opposed to a Real Cell, an existing device the Hive borrows and leaves exactly as found) starts as
a `VirtualCellSpec`: everything a `hivemind.hive.backends.base.CellBackend` needs to create one --
its image, the resources to reserve, how long it may live, its outbound network policy, whether it
needs an Exoskeleton (a display, input and audio attachment on top of a plain terminal session),
the `hivemind.forage.ForageCapacity` the image promises once running, its security tier
(`CombShieldLevel`), how long to wait for it to become reachable, which Hive it belongs to, and
free-form labels. `NetworkPolicy` is the outbound network shape a backend must enforce: `NONE` (no
network at all, the safest default), `EGRESS_ONLY` (outbound only, no inbound ports -- Virtual
Cells never get any), `ALLOWLIST` (outbound restricted to `network_allowlist`), or `VPN_TOR`
(Night Veil only: OpenVPN plus Tor with direct egress blocked, roadmap step 5.7a). A provisioned
Virtual Cell is returned as a `hivemind.cell.Cell` of kind `VIRTUAL`, never this spec itself.

`CellReservation` is the part of a spec the Cell keeps: the cores, memory and disk its backend
reserves for it, and its sub-bee cap. Those are all a Virtual Cell ever has, and no other tenant
contends for them, so its `capacity` (load 0, every byte free) is the Cell's Forage capacity for
its whole disposable life. The Queen builds a spec's own `capacity` from one, and the Cell's
bootstrap ships it (`hivemind.hive.backends.bootstrap`) so the Cell's Warden reports the same
figures rather than probing the host it shares a kernel with: a container reads the host's cores,
memory and load average, so a busy Hive Stand used to make every Virtual Cell look busy too.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by
    `hivemind.hive.backends.base.CellBackend.provision` and by `queen.placement` (a later phase)
    to check Forage headroom before a Cell even exists; `CellReservation` also by the bootstrap
    and the in-Cell composition root (`hivemind.cli.in_cell`). Calls into hivemind.cell
    (CombShieldLevel), hivemind.forage (ForageCapacity, HostCapacity) and waggle (HiveId,
    OsFamily) only.

Key invariants:
    - network_allowlist is non-empty only when network_policy is ALLOWLIST; every other policy
      carries an empty tuple (an allowlist on a policy that does not consult it would silently do
      nothing, which is worse than refusing to construct the spec at all).
    - comb_shield is NIGHT_VEIL if and only if network_policy is VPN_TOR: Night Veil is always
      OpenVPN plus Tor with direct egress blocked (codingrules section 8.7), and VPN_TOR exists
      for no other tier.
    - labels never carries more than MAX_LABELS entries, and every key and value stays within its
      own character bound, so a backend's own tagging mechanism (Docker labels, cloud tags) never
      silently truncates what the Undertaker's orphan sweep later reads back.
    - `CellReservation.capacity` is a pure function of the reservation and the Cell's platform:
      the Queen's placement and the Cell's own report compute it the same way, from the same
      figures, so the two can never disagree about a Cell's headroom.

See Also:
    - .claude/codingrules.md section 8.7 for the Virtual Cell and Night Veil rules this model's
      validators enforce ahead of any backend seeing an invalid spec.
    - .claude/roadmap.md step 5.1 for the field list this module implements, and step 5.7a for the
      VPN_TOR network profile.
    - hivemind.cell.models for Cell, the record a CellBackend.provision returns from this spec.
    - hivemind.forage.models.capacity for ForageCapacity, the `capacity` field's type.
    - hivemind.hive.backends.base for CellBackend, the protocol that consumes this model.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.cell import CombShieldLevel
from hivemind.forage import ForageCapacity, HostCapacity
from waggle.ids import HiveId
from waggle.messages.labels import OsFamily

MAX_IMAGE_NAME_CHARS = 128  # Generous for an images/<name> directory name plus a tag suffix.
# The one image whose in-image kill-switch can hold a Night Veil Cell (roadmap step 5.3a); every
# backend refuses VPN_TOR on any other, so placement stamps it on every Night Veil spec.
NIGHT_VEIL_IMAGE = "night-veil-ubuntu"
MAX_LABEL_KEY_CHARS = (
    63  # Mirrors a familiar label-key limit so a Docker/cloud tag never truncates.
)
MAX_LABEL_VALUE_CHARS = 255  # Same reasoning as the key bound, for the value half of a label.
MAX_LABELS = 32  # Generous for hive_id plus a handful of caller-set tags.
MAX_ALLOWLIST_ENTRIES = 32  # Matches waggle.messages.reports.MAX_NETWORK_SCOPES for the same field.
MAX_ALLOWLIST_ENTRY_CHARS = 253  # RFC 1035 hostname length limit, same bound TaskNeeds uses.
DEFAULT_READY_TIMEOUT_S = 60.0  # Matches codingrules Appendix A.1's own worked example.

# codingrules 8.5: frozen, extra-forbidding config every model in this module shares.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "DEFAULT_READY_TIMEOUT_S",
    "MAX_ALLOWLIST_ENTRIES",
    "MAX_ALLOWLIST_ENTRY_CHARS",
    "MAX_IMAGE_NAME_CHARS",
    "MAX_LABELS",
    "MAX_LABEL_KEY_CHARS",
    "MAX_LABEL_VALUE_CHARS",
    "NIGHT_VEIL_IMAGE",
    "CellReservation",
    "NetworkPolicy",
    "VirtualCellSpec",
]


class NetworkPolicy(Enum):
    """The outbound network shape a CellBackend must enforce for one Virtual Cell.

    Virtual Cells never get inbound ports under any policy (codingrules section 15: "Blast radius
    defaults"); this enum only ever governs outbound reach.
    """

    NONE = "NONE"  # No inbound and no outbound network at all; the safest default.
    EGRESS_ONLY = "EGRESS_ONLY"  # Outbound allowed, unrestricted by an allowlist.
    ALLOWLIST = "ALLOWLIST"  # Outbound restricted to VirtualCellSpec.network_allowlist entries.
    VPN_TOR = "VPN_TOR"  # Night Veil only: OpenVPN plus Tor, direct egress blocked (roadmap 5.7a).


class VirtualCellSpec(BaseModel):
    """A request to provision one Virtual Cell: image, resources, lifetime, network and tier.

    Read by `hivemind.hive.backends.base.CellBackend.provision`; the resulting
    `hivemind.cell.Cell` is always `CellKind.VIRTUAL` and `AccessLevel.FULL` (`Cell`'s own
    validator enforces the latter). `capacity` is not measured after the fact the way a live
    Cell's own report is: it is what the image promises before the Cell exists, so `queen.
    placement` (a later phase) can check Forage headroom ahead of provisioning.
    """

    model_config = _MODEL_CONFIG

    image: Annotated[str, Field(min_length=1, max_length=MAX_IMAGE_NAME_CHARS)] = Field(
        description="The images/<name> this Cell boots: 'base-ubuntu', 'desktop-ubuntu', "
        "'night-veil-ubuntu'."
    )
    cpu_cores: Annotated[float, Field(gt=0)] = Field(
        description="Logical cores the backend must reserve for this Cell."
    )
    memory_bytes: Annotated[int, Field(gt=0)] = Field(
        description="Memory the backend must reserve for this Cell."
    )
    disk_bytes: Annotated[int, Field(gt=0)] = Field(
        description="Disk the backend must reserve for this Cell."
    )
    lifetime_s: Annotated[int, Field(gt=0)] | None = Field(
        default=None,
        description="Maximum seconds this Cell may run before automatic teardown; None means no "
        "forced expiry beyond the manifest's own Overwintering and disposability rules.",
    )
    network_policy: NetworkPolicy = Field(
        default=NetworkPolicy.NONE,
        description="What outbound network this Cell gets: NONE (the safest default, and this "
        "field's own default), EGRESS_ONLY, ALLOWLIST (see network_allowlist) or VPN_TOR (Night "
        "Veil only).",
    )
    network_allowlist: tuple[
        Annotated[str, Field(min_length=1, max_length=MAX_ALLOWLIST_ENTRY_CHARS)], ...
    ] = Field(
        default=(),
        max_length=MAX_ALLOWLIST_ENTRIES,
        description="Outbound destinations allowed when network_policy=ALLOWLIST; empty for "
        "every other policy.",
    )
    exoskeleton: bool = Field(
        default=False,
        description="Whether this Cell needs a display, input and audio attachment on top of its "
        "plain terminal session.",
    )
    read_only_rootfs: bool = Field(
        default=False,
        description="Whether the backend mounts the Cell's root filesystem read-only, leaving "
        "only scratch and /tmp writable. False by default: a Virtual Cell is AccessLevel.FULL "
        "(codingrules section 6.1) and disposable, so its whole filesystem is the bee's to use; "
        "the container's other least-privilege flags (no capabilities, no-new-privileges, "
        "non-root user, pids limit) do not change with this.",
    )
    capacity: ForageCapacity = Field(
        description="The ForageCapacity this image promises once running: host figures, seats "
        "and its own sub-bee cap, read by placement before the Cell exists."
    )
    comb_shield: CombShieldLevel = Field(
        default=CombShieldLevel.MEADOW,
        description="This Cell's security tier once provisioned; NIGHT_VEIL requires "
        "network_policy=VPN_TOR and vice versa.",
    )
    ready_timeout_s: Annotated[float, Field(gt=0)] = Field(
        default=DEFAULT_READY_TIMEOUT_S,
        description="Seconds to wait for the Cell to report ready before provision() raises "
        "CellProvisionError.",
    )
    hive_id: HiveId = Field(
        description="Which Hive this Cell belongs to; every backend stamps it as a label so the "
        "Undertaker's orphan sweep and `hive cells abscond` (roadmap steps 5.8, 5.13) can find "
        "Cells from backend labels alone."
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form tags a backend also stamps on what it creates, alongside hive_id; "
        "bounded so an unbounded label set can never make a sweep unpredictable.",
    )

    @model_validator(mode="after")
    def _allowlist_matches_policy(self) -> VirtualCellSpec:
        """Reject an allowlist that does not match network_policy in either direction.

        Returns:
            This VirtualCellSpec unchanged, once the combination is confirmed valid.

        Raises:
            ValueError: network_policy is ALLOWLIST with an empty network_allowlist, or
                network_policy is anything else with a non-empty one.
        """
        # ALLOWLIST with nothing listed would let no outbound destination through, which is
        # indistinguishable from NONE except by silently confusing a caller who meant to name one.
        if self.network_policy is NetworkPolicy.ALLOWLIST and not self.network_allowlist:
            raise ValueError(
                "network_policy=ALLOWLIST requires at least one network_allowlist entry; "
                "otherwise no outbound destination is ever reachable."
            )
        # A non-ALLOWLIST policy never consults network_allowlist; a caller who set one anyway
        # almost certainly meant a different policy, so this is refused rather than ignored.
        if self.network_policy is not NetworkPolicy.ALLOWLIST and self.network_allowlist:
            raise ValueError(
                "network_allowlist is only meaningful for network_policy=ALLOWLIST, not "
                f"{self.network_policy.value}."
            )
        return self

    @model_validator(mode="after")
    def _night_veil_requires_vpn_tor(self) -> VirtualCellSpec:
        """Reject comb_shield and network_policy that disagree about Night Veil.

        Returns:
            This VirtualCellSpec unchanged, once the combination is confirmed valid.

        Raises:
            ValueError: Exactly one of comb_shield=NIGHT_VEIL and network_policy=VPN_TOR is set.
        """
        is_night_veil = self.comb_shield is CombShieldLevel.NIGHT_VEIL
        is_vpn_tor = self.network_policy is NetworkPolicy.VPN_TOR
        # Night Veil is always OpenVPN plus Tor with direct egress blocked (codingrules 8.7), and
        # VPN_TOR is Night Veil's own routing profile and nothing else's, so the two must agree.
        if is_night_veil != is_vpn_tor:
            raise ValueError(
                "comb_shield=NIGHT_VEIL and network_policy=VPN_TOR must be set together: Night "
                "Veil Cells always route through OpenVPN plus Tor with direct egress blocked "
                f"(got comb_shield={self.comb_shield.value}, "
                f"network_policy={self.network_policy.value})."
            )
        return self

    @model_validator(mode="after")
    def _labels_within_bounds(self) -> VirtualCellSpec:
        """Reject a label set over MAX_LABELS, or a key/value over its own character bound.

        Returns:
            This VirtualCellSpec unchanged, once every label is confirmed within bounds.

        Raises:
            ValueError: More than MAX_LABELS entries, an empty or over-length key, or an
                over-length value.
        """
        if len(self.labels) > MAX_LABELS:
            raise ValueError(
                f"labels carries {len(self.labels)} entries, over the {MAX_LABELS} cap."
            )
        for key, value in self.labels.items():
            if not key or len(key) > MAX_LABEL_KEY_CHARS:
                raise ValueError(f"label key {key!r} must be 1-{MAX_LABEL_KEY_CHARS} characters.")
            if len(value) > MAX_LABEL_VALUE_CHARS:
                raise ValueError(
                    f"label value for key {key!r} exceeds {MAX_LABEL_VALUE_CHARS} characters."
                )
        return self


class CellReservation(BaseModel):
    """What a backend reserves for one Virtual Cell: all the Cell ever has, and never shared.

    Built from a spec (`of`) at provision time, shipped in the Cell's bootstrap, and turned into
    the Cell's Forage capacity (`capacity`) on both sides of its link (module docstring).
    """

    model_config = _MODEL_CONFIG

    cpu_cores: Annotated[float, Field(gt=0)] = Field(
        description="Logical cores reserved for the Cell (VirtualCellSpec.cpu_cores)."
    )
    memory_bytes: Annotated[int, Field(gt=0)] = Field(
        description="Memory reserved for the Cell (VirtualCellSpec.memory_bytes)."
    )
    disk_bytes: Annotated[int, Field(gt=0)] = Field(
        description="Disk reserved for the Cell (VirtualCellSpec.disk_bytes)."
    )
    max_sub_bees: Annotated[int, Field(ge=0)] = Field(
        description="The Cell's own sub-bee cap (VirtualCellSpec.capacity.max_sub_bees)."
    )

    @classmethod
    def of(cls, spec: VirtualCellSpec) -> CellReservation:
        """Return the reservation `spec` asks its backend for.

        Args:
            spec: The Cell about to be provisioned.
        """
        return cls(
            cpu_cores=spec.cpu_cores,
            memory_bytes=spec.memory_bytes,
            disk_bytes=spec.disk_bytes,
            max_sub_bees=spec.capacity.max_sub_bees,
        )

    def capacity(self, arch: str, os: OsFamily) -> ForageCapacity:
        """Return the Cell's Forage capacity: its reservation, dedicated, on its own platform.

        Args:
            arch: The Cell's CPU architecture, a platform fact (its probe, or the image's).
            os: The Cell's OS family, likewise.

        Returns:
            Whole reserved cores (at least one), no load, all memory and disk free, no GPU:
            nothing else runs on what a backend reserves for one Cell.
        """
        # Load is 0 by construction: a reservation is dedicated, so no other tenant contends for
        # it, and a container's own load average is the host's (the kernel is shared).
        host = HostCapacity(
            cores=max(1, int(self.cpu_cores)),
            memory_bytes=self.memory_bytes,
            memory_free_bytes=self.memory_bytes,
            disk_bytes=self.disk_bytes,
            disk_free_bytes=self.disk_bytes,
            cpu_load=0.0,
            gpus=(),
            arch=arch,
            os=os,
        )
        return ForageCapacity(host=host, local_seats=(), max_sub_bees=self.max_sub_bees)
