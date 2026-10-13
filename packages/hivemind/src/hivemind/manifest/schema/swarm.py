"""Define the ``[swarm]`` section: the listener devices dial, and the thresholds that move them.

The Swarm is the mesh of enrolled devices (existing machines the Hive borrows as Real Cells), each
carrying a Pollen Packet (the thin gateway installed on it) that dials the Hive Stand (the machine
the Queen, the central orchestrator, runs on). ``[swarm]`` declares the one thing that is a place:
the swarm listener, where those packets connect and are admitted by a one-time invite (``enabled``,
``listen_host``, ``listen_port``, the ``advertise_url`` a packet is told to dial, and the
certificate and key a non-loopback listener must serve), how long an invite lives, how a device's
liveness is judged (the heartbeat cadence and the number of beats it may miss, the dead-man limit
a device with no Warden on it enforces on itself, and how long the Queen holds a lost device's
tasks before deciding), the access level an invite gets when the operator names none, and two
sub-tables of thresholds: ``[swarm.ladder]`` (when moving a device's Warden onto it, colonizing it,
is worth doing) and ``[swarm.nuc]`` (when giving it a model server of its own is). The schema
checks only what a value is, and the one rule that keeps a secret off the wire in the clear: a
listener on any address other than loopback serves ``wss://``, so it needs both halves of a
certificate. A device packet pins that certificate's fingerprint from the invite, so a self-signed
one is fine. Everything here defaults to off or to loopback: a manifest that never mentions
``[swarm]`` has no listener at all.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``; read by ``hivemind.swarm`` (Layer 3),
    ``hivemind.queen.swarm_gate`` and ``hivemind.cli`` when they compose the listener, the invite
    and the ladder decisions. Calls into waggle (the access level, the endpoint rule) only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - ``tls_cert`` and ``tls_key`` are both set or both empty, and both set whenever
      ``listen_host`` is not a loopback host: a plaintext swarm listener can only ever be on the
      machine it serves (``waggle.uris``' rule for ``ws://``, applied to the bind itself).
    - ``advertise_url``, when set, is something Waggle may dial (``waggle.check_waggle_uri``);
      empty means the invite builder derives it from the listener's own address.
    - No secret lives here: the certificate and key are paths to files, never their contents, and
      the invite token is minted per invite and kept only as a hash (codingrules section 13).

See Also:
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the listener, the
      invite and the trust model these fields parameterise.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder these thresholds decide.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the unreachable grace.
    - docs/manifests/full.toml for every key written out.
    - waggle.uris for the endpoint rule ``advertise_url`` and ``listen_host`` follow.
"""

from __future__ import annotations

import ipaddress
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from waggle.messages.labels import AccessLevel
from waggle.uris import check_waggle_uri, is_loopback_host

DEFAULT_SWARM_LISTEN_HOST = "127.0.0.1"  # Loopback: a manifest that enables the Swarm and says
# nothing else still listens only on the machine it serves, and every device is then local.
DEFAULT_SWARM_LISTEN_PORT = 8720  # The port the Hive Stand's own default address also names.
DEFAULT_INVITE_TTL_MINUTES = 15.0  # The Entrance's invite window (ADR-0041), so one habit fits.
DEFAULT_SWARM_HEARTBEAT_INTERVAL_S = 15.0  # How often a packet reports; a device is not a Warden.
DEFAULT_SWARM_HEARTBEAT_MISS_LIMIT = 4  # A minute of silence at the default interval.
DEFAULT_DEAD_MAN_S = 120.0  # Twice that minute: a flapping link must not strand a device's lease.
DEFAULT_UNREACHABLE_GRACE_S = 300.0  # Five minutes a lost device's tasks wait, neither failed
# nor re-placed, before the Queen decides what to do with them (ADR-0053).
DEFAULT_SWARM_ACCESS = AccessLevel.FULL  # What an invite grants when the operator names no level
# (the operator may always grant less; the cap rides on the invite, never on the wire).
DEFAULT_RELIEF_PRESSURE = 0.75  # Seat or memory pressure past which the Hive Stand wants relief.
DEFAULT_MIN_FREE_MEMORY_BYTES = 1_073_741_824  # 1 GiB: room for a Warden, a sub-bee and headroom.
DEFAULT_NUC_SEAT_PRESSURE = 0.75  # Hive Stand seat pressure past which a Nuc is worth building.
MAX_PORT = 65535  # The highest TCP port; 0 is allowed and means "let the OS choose" (tests).
MAX_HOST_CHARS = 253  # The longest fully qualified domain name the DNS wire format allows.
MAX_PATH_TEXT_CHARS = 4_096  # A certificate or key path; PATH_MAX on Linux.
MAX_ADVERTISE_URL_CHARS = 512  # Matches the wire's bound for a URL a device is told to dial.

# A DNS-style host name: labels of letters, digits and hyphens, joined by dots. Anything else that
# is not an IP literal (a scheme, a port, a path, whitespace) is rejected, so `listen_host` can
# only ever be a host.
_HOST_NAME_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")

__all__ = [
    "DEFAULT_DEAD_MAN_S",
    "DEFAULT_INVITE_TTL_MINUTES",
    "DEFAULT_MIN_FREE_MEMORY_BYTES",
    "DEFAULT_NUC_SEAT_PRESSURE",
    "DEFAULT_RELIEF_PRESSURE",
    "DEFAULT_SWARM_ACCESS",
    "DEFAULT_SWARM_HEARTBEAT_INTERVAL_S",
    "DEFAULT_SWARM_HEARTBEAT_MISS_LIMIT",
    "DEFAULT_SWARM_LISTEN_HOST",
    "DEFAULT_SWARM_LISTEN_PORT",
    "DEFAULT_UNREACHABLE_GRACE_S",
    "SwarmLadderSection",
    "SwarmNucSection",
    "SwarmSection",
]

# A frozen, extras-forbidding config matching every other section model (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class SwarmLadderSection(BaseModel):
    """``[swarm.ladder]``: when moving a device's Warden onto the device is worth it."""

    model_config = _MODEL_CONFIG

    relief_pressure: float = Field(
        default=DEFAULT_RELIEF_PRESSURE,
        gt=0,
        le=1,
        description="Hive Stand seat or memory pressure, as a fraction of its capacity, past "
        "which the Queen may colonize a device to take a Warden off it; a task that must survive "
        "short outages, or the operator's own `promote`, can colonize below it.",
    )
    min_free_memory_bytes: int = Field(
        default=DEFAULT_MIN_FREE_MEMORY_BYTES,
        ge=0,
        description="Free memory, in bytes, a device must report before the Queen will move a "
        "Warden onto it, on top of the Forage footprint of a Warden plus one sub-bee; 0 turns "
        "the floor off and leaves the footprint as the only test.",
    )


class SwarmNucSection(BaseModel):
    """``[swarm.nuc]``: when giving a colonized device a model server of its own is worth it."""

    model_config = _MODEL_CONFIG

    seat_pressure: float = Field(
        default=DEFAULT_NUC_SEAT_PRESSURE,
        gt=0,
        le=1,
        description="Hive Stand model-seat pressure, as a fraction of its seats, past which the "
        "Queen may promote a colonized device to a Nuc so its sub-bees stop competing for them; "
        "a task's need to keep working offline, or the operator's own `promote`, can promote "
        "below it.",
    )


class SwarmSection(BaseModel):
    """``[swarm]``: the swarm listener, invites, liveness and the ladder's thresholds."""

    model_config = _MODEL_CONFIG

    enabled: bool = Field(
        default=False,
        description="Whether the Hive accepts Pollen Packets at all; false starts no listener "
        "and enrols no device.",
    )
    listen_host: str = Field(
        default=DEFAULT_SWARM_LISTEN_HOST,
        max_length=MAX_HOST_CHARS,
        description="The address the swarm listener binds: an IP literal or a host name. Any "
        "address but loopback needs tls_cert and tls_key, because the listener then serves "
        "wss://.",
    )
    listen_port: int = Field(
        default=DEFAULT_SWARM_LISTEN_PORT,
        ge=0,
        le=MAX_PORT,
        description="The TCP port the swarm listener binds; 0 lets the OS choose (tests).",
    )
    advertise_url: str = Field(
        default="",
        max_length=MAX_ADVERTISE_URL_CHARS,
        description="The URL a device is told to dial, printed in its invite: a wss:// URL, or "
        "ws:// only on loopback (waggle.uris). Empty derives it from listen_host and "
        "listen_port; set it when devices reach the Hive Stand through a different name.",
    )
    tls_cert: str = Field(
        default="",
        max_length=MAX_PATH_TEXT_CHARS,
        description="Path to the PEM certificate chain the listener serves, resolved relative to "
        "the manifest's own directory; empty means no TLS. A self-signed certificate is fine: "
        "an invite carries its fingerprint and the packet pins it instead of trusting a CA.",
    )
    tls_key: str = Field(
        default="",
        max_length=MAX_PATH_TEXT_CHARS,
        description="Path to the PEM private key for tls_cert; a file path, never the key "
        "itself. Set exactly when tls_cert is.",
    )
    invite_ttl_minutes: float = Field(
        default=DEFAULT_INVITE_TTL_MINUTES,
        gt=0,
        description="How long an unredeemed device invite stays valid; an invite is also "
        "single use.",
    )
    heartbeat_interval_s: float = Field(
        default=DEFAULT_SWARM_HEARTBEAT_INTERVAL_S,
        gt=0,
        description="Seconds between a packet's device heartbeats; sent to a device in its "
        "enrolment and in every node update.",
    )
    heartbeat_miss_limit: int = Field(
        default=DEFAULT_SWARM_HEARTBEAT_MISS_LIMIT,
        ge=1,
        description="Missed device heartbeats after which the Queen marks a device unreachable.",
    )
    dead_man_s: float = Field(
        default=DEFAULT_DEAD_MAN_S,
        gt=0,
        description="Seconds a device with no Warden on it may be out of contact before its "
        "packet kills what its leases started, removes their scratch and releases them; the "
        "limit travels to the device in its enrolment.",
    )
    unreachable_grace_s: float = Field(
        default=DEFAULT_UNREACHABLE_GRACE_S,
        ge=0,
        description="Seconds the Queen holds an unreachable device's tasks, neither failed nor "
        "re-placed, before deciding; 0 decides at once.",
    )
    default_access: AccessLevel = Field(
        default=DEFAULT_SWARM_ACCESS,
        description="The access level an invite grants when `hive swarm invite` names none: "
        "READ_ONLY, SCRATCH or FULL. It is a cap, never a promise: the operator may grant less "
        "at approval.",
    )
    ladder: SwarmLadderSection = Field(
        default_factory=SwarmLadderSection,
        description="Thresholds for colonizing a device (moving its Warden onto it).",
    )
    nuc: SwarmNucSection = Field(
        default_factory=SwarmNucSection,
        description="Thresholds for promoting a colonized device to a Nuc.",
    )

    @field_validator("listen_host")
    @classmethod
    def _listen_host_is_a_host(cls, value: str) -> str:
        """Reject a ``listen_host`` that is not an IP literal or a plain host name."""
        # A scheme, a port or a path here would bind something other than what the operator
        # meant, so the value must be a bare host: an IP literal (v4 or v6, no brackets) or a
        # DNS-style name. The listener, not this schema, resolves a name.
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            return value
        # A name must spell at least one letter: digits and dots alone are an address that failed
        # to parse (a mistyped octet), and reading it as a name would bind who knows what.
        is_name = _HOST_NAME_PATTERN.fullmatch(value) is not None and any(
            character.isalpha() for character in value
        )
        if not is_name:
            raise ValueError(f"[swarm] listen_host {value!r} is not an IP address or a host name.")
        return value

    @field_validator("advertise_url")
    @classmethod
    def _advertise_url_is_dialable(cls, value: str) -> str:
        """Reject a non-empty ``advertise_url`` that Waggle could never dial."""
        # The same rule every other Waggle endpoint in the Hive follows (waggle.uris): wss://
        # anywhere, ws:// only on loopback. Applying it here fails a bad invite URL at load time,
        # not on the first device's dial. Empty means "derive it", which has nothing to check.
        return check_waggle_uri(value) if value else value

    @model_validator(mode="after")
    def _tls_pair_and_loopback_rule(self) -> SwarmSection:
        """Require the certificate and key together, and both on any non-loopback listener."""
        # One half of a pair serves nothing, so it is refused as a half-written setting rather
        # than silently falling back to plaintext.
        if bool(self.tls_cert) != bool(self.tls_key):
            raise ValueError(
                "[swarm] tls_cert and tls_key must be set together or both left empty; "
                f"got tls_cert {self.tls_cert!r} and tls_key {self.tls_key!r}."
            )
        # Plaintext is acceptable only where the traffic never leaves the machine (waggle.uris):
        # any other bind carries enrolment, signed commands and file bytes across a network, so
        # it must serve wss://.
        if not is_loopback_host(self.listen_host) and not self.tls_cert:
            raise ValueError(
                f"[swarm] listen_host {self.listen_host!r} is not a loopback address, so the "
                "listener serves wss:// and needs both tls_cert and tls_key."
            )
        return self
