"""Define the ``[entrance]`` section: how the Hive Entrance listens, logs devices in and pushes.

The Hive Entrance is the Hive's one door (codingrules 8.15): an HTTP and WebSocket gateway on two
listeners, loopback (always on) and remote (only when exposed), serving the Landing Board (the
versioned API contract) to devices enrolled and approved at the Hive Stand. This module declares
every setting that door reads, with the defaults codingrules section 13 documents: where the
listeners bind, how the remote one is exposed (``loopback``, ``vpn``, ``lan`` or ``tunnel``; there
is deliberately no ``public`` value), session lifetimes, step-up and lockout thresholds, rate
limits, and the ``[entrance.push]`` and ``[entrance.voice]`` sub-tables. The schema checks only
what a value is: a loopback ``bind``, a specific (never wildcard) ``remote_bind``, parseable VPN
ranges. Whether a mode's prerequisites are all present (TLS and mutual TLS for ``lan`` and
``tunnel``, a ``remote_bind`` inside ``vpn_cidrs`` for ``vpn``) is decided when the Entrance
starts, by ``hivemind.entrance.expose``, so a manifest with a half-configured remote mode still
loads for every other command and ``hive serve`` refuses with the precise reason. One default
follows another field: ``mutual_tls``, when the manifest leaves it out, is true for ``lan`` and
``tunnel`` (which require it) and false for ``vpn`` (the overlay already authenticates every
packet, and a phone joining over it enrols by QR code with a passkey, never a certificate) and
``loopback`` (no remote listener at all); written out, it is taken as written.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``; read by ``hivemind.entrance`` (Layer 7)
    when it composes the listeners, auth and push. Calls into the standard library only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - ``mutual_tls`` is always a plain bool once a section is built: an omitted value is filled
      from ``expose`` before validation, so every reader sees what the mode asks for.
    - ``EntranceExposure`` has no public member, and ``remote_bind`` is never a wildcard address,
      so no manifest can put the Entrance on every interface (codingrules 8.15, ADR-0041); it may
      be a loopback address, because tunnel mode binds it there for the local tunnel client.
    - ``bind`` always names a loopback host: the loopback listener, where approval lives, can
      never be moved onto a routable address.
    - No secret lives here: the operator password, device keys, TLS private key material for
      the Hive's own CA and the VAPID key come from the Entrance tables, the secret store or
      ``HIVEMIND_*`` variables, never this section (codingrules section 13).

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for every decision
      these fields parameterise.
    - docs/adr/0042-landing-board-versioning-and-push.md for the push channels.
    - .claude/codingrules.md section 13 for the documented example of this section.
    - hivemind.entrance.expose for the start-time refusals this schema leaves to it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "DEFAULT_ENTRANCE_BIND",
    "DEFAULT_VPN_CIDRS",
    "EntranceExposure",
    "EntrancePushSection",
    "EntranceSection",
    "EntranceTlsSection",
    "EntranceVoiceSection",
    "split_host_port",
]

DEFAULT_ENTRANCE_BIND = "127.0.0.1:8710"  # codingrules 13's documented loopback listener address.
# Tailscale's IPv4 CGNAT range and its IPv6 unique-local prefix: the documented overlay (ADR-0041),
# so a Tailscale address passes `vpn` mode's check with no configuration at all.
DEFAULT_VPN_CIDRS = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")
_LOOPBACK_HOSTNAME = "localhost"  # The one hostname accepted as loopback; everything else is an IP.
_MAX_PORT = 65535  # The highest TCP port; 0 is allowed and means "let the OS choose" (tests).
_MAX_TUNNEL_ARGS = 64  # A tunnel client's argv; generous, but a bound keeps the manifest sane.
_MAX_VPN_CIDRS = 32  # Overlay ranges; a real Hive lists one or two.
_MAX_INTERFACE_CHARS = 64  # A network interface name; the OS limits are far shorter.
_MAX_RP_ID_CHARS = 253  # A WebAuthn relying party id is a DNS name, at most 253 characters.
_MAX_WEBHOOK_ALLOWLIST = 64  # Extra webhook destinations; a real Hive lists a handful.

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# The modes whose remote listener faces a network nobody vouches for, so an omitted mutual_tls
# means true there (and false everywhere else): the expose values, as the manifest spells them.
_MUTUAL_TLS_BY_DEFAULT = frozenset({"lan", "tunnel"})


class EntranceExposure(Enum):
    """How the remote listener is exposed; there is deliberately no public mode (ADR-0041)."""

    LOOPBACK = "loopback"  # No remote listener at all: the default.
    VPN = "vpn"  # Bound to an overlay (Tailscale or WireGuard) address only: the recommended mode.
    LAN = "lan"  # Bound to a LAN address; TLS and mutual TLS mandatory.
    TUNNEL = (
        "tunnel"  # Reached through a supervised TCP tunnel client; TLS and mutual TLS mandatory.
    )


class EntranceTlsSection(BaseModel):
    """``[entrance.tls]``: the remote listener's own certificate and key, by path."""

    model_config = _MODEL_CONFIG

    cert: str = Field(
        default="",
        description="Path to the remote listener's PEM certificate chain; empty means no TLS. "
        "Required for lan and tunnel, and for browsers in any mode (a passkey needs a secure "
        "origin), e.g. the output of `tailscale cert` for the overlay's DNS name.",
    )
    key: str = Field(
        default="",
        description="Path to the PEM private key for `cert`; a file path, never the key itself.",
    )


class EntrancePushSection(BaseModel):
    """``[entrance.push]``: which push channels the Entrance offers devices (ADR-0042)."""

    model_config = _MODEL_CONFIG

    webhooks: bool = Field(default=True, description="Offer signed webhooks to program devices.")
    web_push: bool = Field(
        default=True,
        description="Offer Web Push to browsers and phones; the VAPID key comes from "
        "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY (or the secret store) and its contact from "
        "HIVEMIND_ENTRANCE_VAPID_SUBJECT.",
    )
    webhook_allowlist: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_WEBHOOK_ALLOWLIST,
        description="Hosts or CIDR networks a webhook may target beyond the default rule (an "
        "https URL, or an address inside vpn_cidrs); never loopback, link-local or the Hive "
        "Stand's own addresses, whatever this lists (ADR-0042).",
    )


class EntranceVoiceSection(BaseModel):
    """``[entrance.voice]``: audio in at the Landing Board, transcribed on the TRANSCRIBER slot."""

    model_config = _MODEL_CONFIG

    enabled: bool = Field(
        default=True, description="Accept audio on the chat route and the chat WebSocket."
    )
    confirm_goals: bool = Field(
        default=True,
        description="Echo a spoken goal back to the device for confirmation before it becomes "
        "a task, so a misheard sentence never spends anything; answers and chat go straight "
        "through.",
    )
    keep_audio: bool = Field(
        default=False,
        description="Keep a clip after transcription as C2 Nectar with a retention window; "
        "false discards it once transcribed.",
    )
    max_clip_seconds: float = Field(
        default=120.0, gt=0, description="The longest clip accepted, in seconds."
    )
    audio_seconds_per_minute: float = Field(
        default=120.0,
        gt=0,
        description="Seconds of audio one device may send a minute: a token bucket of that many "
        "seconds, refilling at that rate, charged each clip's length before it is transcribed. "
        "At least max_clip_seconds, so the longest clip always fits a full bucket.",
    )
    keep_audio_hours: float = Field(
        default=24.0,
        gt=0,
        description="How long a kept clip stays as C2 Nectar before the retention sweep deletes "
        "it; read only when keep_audio is true.",
    )

    @model_validator(mode="after")
    def _budget_fits_a_clip(self) -> EntranceVoiceSection:
        """Refuse an audio budget smaller than the longest clip: that clip could never be heard."""
        if self.audio_seconds_per_minute < self.max_clip_seconds:
            raise ValueError(
                f"[entrance.voice] audio_seconds_per_minute ({self.audio_seconds_per_minute:g}) "
                f"is below max_clip_seconds ({self.max_clip_seconds:g}): a clip that long could "
                "never fit a device's audio budget."
            )
        return self


class EntranceSection(BaseModel):
    """``[entrance]``: the Hive Entrance's listeners, sessions, thresholds and sub-tables."""

    model_config = _MODEL_CONFIG

    bind: str = Field(
        default=DEFAULT_ENTRANCE_BIND,
        description="host:port of the loopback listener; always on, and always a loopback host.",
    )
    expose: EntranceExposure = Field(
        default=EntranceExposure.LOOPBACK,
        description="loopback | vpn | lan | tunnel; there is no public mode.",
    )
    remote_bind: str = Field(
        default="",
        description="host:port of the remote listener, a specific address (never a wildcard); "
        "required whenever expose is not loopback.",
    )
    public_url: str = Field(
        default="",
        description="The https URL devices use to reach the remote listener; the only origin "
        "CORS allows, the base of invite links, and the WebAuthn relying party for devices "
        "enrolled through it.",
    )
    tls: EntranceTlsSection = Field(
        default_factory=EntranceTlsSection, description="The remote listener's certificate."
    )
    mutual_tls: bool = Field(
        default=False,
        description="Require a client certificate from the Hive's own authority on the remote "
        "listener. Omitted, it follows expose: true for lan and tunnel, which refuse to start "
        "with it false; false for vpn, where the overlay already authenticates every packet "
        "(turn it on to demand certificates there too); false for loopback, which has no "
        "remote listener.",
    )
    operators: int = Field(
        default=1,
        ge=1,
        description="How many operators may exist; Brood 1.0 is single-operator, and `hive "
        "entrance operator add` refuses unless this is raised above one.",
    )
    steward_devices: bool = Field(
        default=False,
        description="Let a device holding entrance:steward approve others after full step-up.",
    )
    travel_lock: bool = Field(
        default=False,
        description="Force step-up and notify every device when a known device appears from a "
        "network it has not used before; never approves anything.",
    )
    session_ttl_hours: float = Field(
        default=12.0, gt=0, description="The longest a session lives, however active."
    )
    idle_timeout_minutes: float = Field(
        default=30.0, gt=0, description="A session unused this long is dead."
    )
    step_up_window_minutes: float = Field(
        default=5.0, gt=0, description="How long a step-up keeps a session stepped up."
    )
    step_up_spend: float = Field(
        default=5.0,
        ge=0,
        description="A goal budget in USD above which submitting it needs step-up.",
    )
    lockout_attempts: int = Field(
        default=5,
        ge=1,
        description="Consecutive failed logins, after a valid device proof, that lock the device.",
    )
    lockout_denials: int = Field(
        default=20,
        ge=1,
        description="Capability denials inside lockout_denial_window_s that lock the device.",
    )
    lockout_denial_window_s: float = Field(
        default=60.0, gt=0, description="The window lockout_denials is counted over, in seconds."
    )
    rate_limit_per_device: int = Field(
        default=60, ge=1, description="Requests per minute one device may make."
    )
    rate_limit_per_address: int = Field(
        default=30,
        ge=1,
        description="Requests per minute one network address may make; also bounds every "
        "unauthenticated route (enrolment, login challenges).",
    )
    request_skew_s: float = Field(
        default=60.0,
        gt=0,
        description="How far a signed request's timestamp may be from the Hive Stand's clock.",
    )
    invite_ttl_minutes: float = Field(
        default=15.0, gt=0, description="How long an unredeemed invite stays valid."
    )
    pending_ttl_hours: float = Field(
        default=24.0, gt=0, description="How long a redeemed, unapproved request waits."
    )
    vpn_cidrs: tuple[str, ...] = Field(
        default=DEFAULT_VPN_CIDRS,
        max_length=_MAX_VPN_CIDRS,
        description="The overlay's address ranges; in vpn mode remote_bind must fall inside one.",
    )
    vpn_interface: str = Field(
        default="",
        max_length=_MAX_INTERFACE_CHARS,
        description="The overlay's network interface remote_bind must be assigned to in vpn "
        "mode; empty means the platform's Tailscale interface (tailscale0 on Linux, Tailscale "
        "on Windows). The interface is what proves the overlay: Tailscale's IPv4 range is also "
        "carrier-grade NAT space an ordinary WAN address can fall in.",
    )
    rp_id: str = Field(
        default="",
        max_length=_MAX_RP_ID_CHARS,
        description="The WebAuthn relying party id for devices enrolled on the remote listener; "
        "empty means public_url's host. A passkey is bound to it, so it is fixed once the first "
        "remote device enrols and moves with the Hive Stand on Supersedure.",
    )
    tunnel_command: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_TUNNEL_ARGS,
        description="argv of a TCP-forwarding tunnel client run as a supervised child in tunnel "
        "mode, so TLS stays end to end; required for tunnel.",
    )
    push: EntrancePushSection = Field(
        default_factory=EntrancePushSection, description="Which push channels are offered."
    )
    voice: EntranceVoiceSection = Field(
        default_factory=EntranceVoiceSection, description="Audio in at the chat route."
    )

    @model_validator(mode="before")
    @classmethod
    def _mutual_tls_follows_the_mode(cls, data: object) -> object:
        """Fill an omitted ``mutual_tls`` from ``expose``: on for lan and tunnel, off otherwise."""
        if not isinstance(data, Mapping) or data.get("mutual_tls") is not None:
            return data
        expose = data.get("expose", EntranceExposure.LOOPBACK)
        # The enum, or the manifest's own spelling; anything else is left for the field to refuse.
        mode = expose.value if isinstance(expose, EntranceExposure) else expose
        return {**data, "mutual_tls": mode in _MUTUAL_TLS_BY_DEFAULT}

    @field_validator("bind")
    @classmethod
    def _bind_is_loopback(cls, value: str) -> str:
        """Reject a loopback listener address that is not a loopback host."""
        host, _ = split_host_port(value)
        # The loopback listener carries the approval routes; a routable address here would
        # expose them, which codingrules 8.15 forbids outright.
        if host != _LOOPBACK_HOSTNAME and not ipaddress.ip_address(host).is_loopback:
            raise ValueError(f"[entrance] bind {value!r} is not a loopback address.")
        return value

    @field_validator("remote_bind")
    @classmethod
    def _remote_bind_is_specific(cls, value: str) -> str:
        """Reject a remote listener address that is a wildcard or not an IP literal."""
        # Empty means "no remote listener configured"; expose.py refuses a remote mode then.
        if not value:
            return value
        host, _ = split_host_port(value)
        address = ipaddress.ip_address(host)
        # A wildcard listens on every interface, the open internet included (ADR-0041). A
        # loopback address is allowed here: tunnel mode binds the remote listener to loopback
        # for its local tunnel client, and expose.py checks each mode's own address rule.
        if address.is_unspecified:
            raise ValueError(f"[entrance] remote_bind {value!r} is a wildcard address.")
        return value

    @field_validator("vpn_cidrs")
    @classmethod
    def _vpn_cidrs_parse(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a VPN range that is not a valid network in CIDR form."""
        for cidr in value:
            ipaddress.ip_network(cidr, strict=True)
        return value

    @field_validator("rp_id")
    @classmethod
    def _rp_id_is_a_name(cls, value: str) -> str:
        """Reject a relying party id that is an IP literal or carries a scheme or port."""
        if not value:
            return value
        # WebAuthn refuses an IP address as a relying party, and an id is a bare host name.
        if "/" in value or ":" in value:
            raise ValueError(f"[entrance] rp_id {value!r} must be a bare DNS name.")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
        raise ValueError(f"[entrance] rp_id {value!r} is an IP address; WebAuthn needs a name.")

    @field_validator("public_url")
    @classmethod
    def _public_url_is_https(cls, value: str) -> str:
        """Reject a public URL that is not https (a passkey and web push need a secure origin)."""
        if value and not value.startswith("https://"):
            raise ValueError(f"[entrance] public_url {value!r} must be an https URL.")
        return value


def split_host_port(value: str) -> tuple[str, int]:
    """Split a ``host:port`` string, accepting a bracketed IPv6 host (``[::1]:8710``).

    Args:
        value: The address as written in the manifest.

    Returns:
        The host (brackets removed) and the port.

    Raises:
        ValueError: The string has no port, the port is not an integer in 0..65535, or the host
            is empty.
    """
    host, separator, port_text = value.rpartition(":")
    # rpartition finds no colon: there is no port at all.
    if not separator or not host:
        raise ValueError(f"{value!r} is not host:port.")
    # A bracketed IPv6 literal keeps its colons inside the brackets.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    port = int(port_text)
    if not 0 <= port <= _MAX_PORT:
        raise ValueError(f"{value!r} has a port outside 0..{_MAX_PORT}.")
    return host, port
