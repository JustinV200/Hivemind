"""Build what the Queen and the backends need to honour Night Veil, from a loaded manifest.

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

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.deps` (the placement policy and both locality sets) and
    `hivemind.cli.compose.virtual_cell_backends` (the link). Calls into `hivemind.cell`,
    `hivemind.cli.stores` (provider_configs), `hivemind.hive` (NetworkPolicy, NightVeilLink),
    `hivemind.llm.registry` (runs_in_process, runs_locally), `hivemind.manifest` and
    `hivemind.queen.placement` (NightVeilConstraints) only.

Key invariants:
    - Nothing here fills in a missing hidden-service address or proxy: an unset value stays
      unset, so the refusal names it rather than a guessed default being dialled.
    - A provider is local only by its configuration (`runs_locally`), never by its name.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the tier's boundary.
    - hivemind.queen.placement.policy for NightVeilConstraints and check_night_veil.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel
from hivemind.cli.stores import provider_configs
from hivemind.hive import NetworkPolicy
from hivemind.hive.backends.bootstrap import NightVeilLink
from hivemind.llm.registry import runs_in_process, runs_locally
from hivemind.manifest import HiveManifest
from hivemind.manifest.schema.security import TierProfile
from hivemind.queen.placement import NightVeilConstraints

_VPN_TOR_EGRESS = "vpn_tor"  # The one `egress_profile` Night Veil can honour (ADR-0030).
_TOR_SOCKS_SCHEME = "socks5h://"  # Tor's SOCKS port resolves names: a .onion never meets DNS.
_SCHEME_SEPARATOR = "://"

__all__ = [
    "in_process_providers",
    "local_providers",
    "night_veil_constraints",
    "night_veil_link",
    "tor_socks_url",
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


def _profile(manifest: HiveManifest) -> TierProfile | None:
    """Return the Night Veil tier profile, or None when the manifest configures none."""
    # The manifest keys tiers by the wire enum; the hivemind-side tier converts to it.
    return manifest.security.tiers.get(CombShieldLevel.NIGHT_VEIL.to_wire())
