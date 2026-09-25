"""Build the Night Veil tier's placement policy, link and retention boundary from a manifest.

Roadmap step 10.3a closes a phase 5 gap: the production composition root never turned
`[security.tiers.NIGHT_VEIL]` into `PlacementPolicy.night_veil`, so every Night Veil task failed
placement whatever the operator configured. `night_veil_constraints` builds it (None when the
manifest names no Night Veil profile, which `hivemind.queen.placement.policy.check_night_veil`
then refuses loudly); `night_veil_link` builds the `NightVeilLink` every backend's
`QueenEndpoint` carries, so a Night Veil Cell is handed the hidden service and the Tor proxy at
provisioning (`hivemind.hive.backends.bootstrap.cell_endpoint`) and a Hive with an incomplete
profile provisions none. `tor_socks_url` reads the profile's `tor_socks` as the URL a Cell's
Waggle transport dials through: a bare `host:port` names Tor's own SOCKS port, which resolves
names itself, so it is spoken as `socks5h`. `in_process_providers` and `local_providers` name the
`[llm.providers]` that serve locally, for the grant and slot-binding points' local-only rule.

The tier's retention boundary is built here too (codingrules section 12, ADR-0030). `veil_trail`
wraps the Hive's durable trail in the `VeiledTrail` every Queen-side writer records through, once,
when a Virtual side exists (only a Virtual Cell can be Night Veil); `build_night_veil` builds the
rest of the boundary around that same trail (or around a plain one, for an offline `hive cells`
command): the purge, whose durable half deletes with its own connection to the Hive's file, opened
at the first purge, and whose side channels are attached once the stores exist
(`hivemind.cli.compose.night_veil.side_channels`).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.night_veil`. Called
    by `hivemind.cli.compose.deps` (the placement policy and both locality sets) and
    `hivemind.cli.compose.virtual_cell_backends` (the link), and by `hivemind.cli.compose.hive` and
    `hivemind.cli.compose.virtual_cells` (the boundary). Calls into `hivemind.cell`,
    `hivemind.cli.stores` (provider_configs), `hivemind.hive` (NetworkPolicy, NightVeilLink,
    NightVeilBoundary), `hivemind.llm.registry` (runs_in_process, runs_locally),
    `hivemind.manifest`, `hivemind.pheromone` (the retention boundary) and
    `hivemind.queen.placement` (NightVeilConstraints) only.

Key invariants:
    - Nothing here fills in a missing hidden-service address or proxy: an unset value stays
      unset, so the refusal names it rather than a guessed default being dialled.
    - A provider is local only by its configuration (`runs_locally`), never by its name.
    - One Hive has one set of ephemeral segments: `build_night_veil` reuses the segments of a
      trail `veil_trail` already wrapped, so the Queen and the Virtual side share one boundary.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the tier's boundary.
    - hivemind.queen.placement.policy for NightVeilConstraints and check_night_veil.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel
from hivemind.cli.stores import provider_configs
from hivemind.hive import NetworkPolicy
from hivemind.hive.backends.bootstrap import NightVeilLink
from hivemind.hive.night_veil import NightVeilBoundary
from hivemind.llm.registry import runs_in_process, runs_locally
from hivemind.manifest import HiveManifest
from hivemind.manifest.schema.security import TierProfile
from hivemind.pheromone import (
    EphemeralSegments,
    LazySqliteSegmentPurge,
    MemoryPheromoneTrail,
    MemorySegmentPurge,
    NightVeilTeardownPurge,
    PheromoneTrail,
    SegmentPurge,
    SideChannels,
    TrailRecorder,
    VeiledTrail,
)
from hivemind.queen.placement import NightVeilConstraints
from waggle.clock import Clock

_VPN_TOR_EGRESS = "vpn_tor"  # The one `egress_profile` Night Veil can honour (ADR-0030).
_TOR_SOCKS_SCHEME = "socks5h://"  # Tor's SOCKS port resolves names: a .onion never meets DNS.
_SCHEME_SEPARATOR = "://"

__all__ = [
    "build_night_veil",
    "in_process_providers",
    "local_providers",
    "night_veil_constraints",
    "night_veil_link",
    "tor_socks_url",
    "veil_trail",
]


def tor_socks_url(tor_socks: str) -> str:
    """Return the profile's `tor_socks` as a proxy URL; empty stays empty.

    Args:
        tor_socks: `[security.tiers.NIGHT_VEIL] tor_socks`: a URL, or a bare `host:port`.

    Returns:
        The value unchanged when it names a scheme or is empty; otherwise `socks5h://<value>`.
    """
    if not tor_socks or _SCHEME_SEPARATOR in tor_socks:
        return tor_socks
    return f"{_TOR_SOCKS_SCHEME}{tor_socks}"


def night_veil_constraints(manifest: HiveManifest) -> NightVeilConstraints | None:
    """Build `PlacementPolicy.night_veil` from the manifest's Night Veil tier profile.

    Args:
        manifest: The loaded manifest; `security.tiers` is read.

    Returns:
        The profile's placement-facing shape, or None when no Night Veil profile is configured.
    """
    profile = _profile(manifest)
    if profile is None:
        return None
    # Anything but vpn_tor cannot route Night Veil traffic; check_night_veil says so by name.
    policy = (
        NetworkPolicy.VPN_TOR
        if profile.egress_profile == _VPN_TOR_EGRESS
        else NetworkPolicy.EGRESS_ONLY
    )
    return NightVeilConstraints(
        required_network_policy=policy,
        hive_stand_onion_address=profile.hidden_service_address,
        socks_proxy_url=tor_socks_url(profile.tor_socks),
        locale_profile=profile.locale_profile,
    )


def night_veil_link(manifest: HiveManifest) -> NightVeilLink | None:
    """Build the link every backend hands a Night Veil Cell, or None when none is configured.

    Args:
        manifest: The loaded manifest; `security.tiers` is read.

    Returns:
        The hidden service's Waggle URL and the Tor proxy, or None when the profile, its
        hidden-service address or its proxy is missing (no Night Veil Cell is then minted).
    """
    profile = _profile(manifest)
    if profile is None:
        return None
    return NightVeilLink.from_profile(
        profile.hidden_service_address, tor_socks_url(profile.tor_socks)
    )


def in_process_providers(manifest: HiveManifest) -> frozenset[str]:
    """Name the providers whose model runs inside whichever process binds it.

    Args:
        manifest: The loaded manifest; `[llm.providers]` is read.

    Returns:
        Every provider of an in-process kind: local to any Cell that binds it (the Queen's view).
    """
    configs = provider_configs(manifest)
    return frozenset(name for name, config in configs.items() if runs_in_process(config))


def local_providers(manifest: HiveManifest) -> frozenset[str]:
    """Name the providers that serve from this machine: in process, or on its loopback.

    Args:
        manifest: The loaded manifest; `[llm.providers]` is read.

    Returns:
        Every provider `runs_locally` accepts, for the Hive Stand's own Warden.
    """
    configs = provider_configs(manifest)
    return frozenset(name for name, config in configs.items() if runs_locally(config))


def veil_trail(manifest: HiveManifest, trail: PheromoneTrail, clock: Clock) -> PheromoneTrail:
    """Return the trail every Queen-side writer records through: veiled when a Virtual side exists.

    Args:
        manifest: The loaded manifest; `[virtual_cells] backend` is read.
        trail: The Hive's durable trail.
        clock: Stamps the ephemeral segments' own exports.

    Returns:
        `trail` itself when no Virtual backend is configured (no Cell can be Night Veil), else a
        `VeiledTrail` over it with fresh, empty ephemeral segments.
    """
    if manifest.virtual_cells.backend is None:
        return trail
    return _veiled(trail, clock)


def build_night_veil(
    manifest: HiveManifest, trail: PheromoneTrail, clock: Clock
) -> NightVeilBoundary:
    """Build the Night Veil boundary around `trail`, sharing its segments if it is already veiled.

    Args:
        manifest: The loaded manifest; the Hive's id, node and database file are read.
        trail: The `veil_trail` a running Hive records through, or a plain durable trail (an
            offline `hive cells` command), which gets a boundary of its own.
        clock: Stamps every record the purge makes.

    Returns:
        The boundary the lifecycle, the provider, the segment receiver and the Queen share.
    """
    veiled = trail if isinstance(trail, VeiledTrail) else _veiled(trail, clock)
    recorder = TrailRecorder(
        trail=veiled.durable, clock=clock, hive_id=manifest.hive.id, node_id=manifest.hive.node_id
    )
    purge = NightVeilTeardownPurge(
        _segment_purge(manifest, veiled.durable),
        SideChannels(),  # Attached once the stores exist (`attach_side_channels`).
        recorder,
        ephemeral=veiled.segments,
    )
    return NightVeilBoundary(
        segments=veiled.segments, veiled=veiled, purge=purge, recorder=recorder
    )


def _veiled(trail: PheromoneTrail, clock: Clock) -> VeiledTrail:
    """Wrap `trail` in a `VeiledTrail` over fresh, empty ephemeral segments."""
    return VeiledTrail(trail, EphemeralSegments(clock))


def _segment_purge(manifest: HiveManifest, durable: PheromoneTrail) -> SegmentPurge:
    """Return the durable half of the purge: the in-memory trail's own, or the Hive's SQLite file.

    The composition root knows which store it opened: a test's in-memory trail is purged in
    place; the Hive's own trail lives in `[hive] db`, which the purge opens itself, lazily.
    """
    if isinstance(durable, MemoryPheromoneTrail):
        return MemorySegmentPurge(durable)
    return LazySqliteSegmentPurge(manifest.resolve_path(manifest.hive.db))


def _profile(manifest: HiveManifest) -> TierProfile | None:
    """Return the Night Veil tier profile, or None when the manifest configures none."""
    # The manifest keys tiers by the wire enum; the hivemind-side tier converts to it.
    return manifest.security.tiers.get(CombShieldLevel.NIGHT_VEIL.to_wire())
