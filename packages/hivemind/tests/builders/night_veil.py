"""Build the Night Veil boundary over an in-memory trail, for tests below the composition root.

`hivemind.cli.compose.night_veil.build_night_veil` builds the real boundary from a manifest, over
the Hive's SQLite trail. A unit test of the lifecycle, the Queen's attach or the segment receiver
wants the same parts over a `MemoryPheromoneTrail` instead: `make_night_veil` wires them exactly
as the composition root does (the ephemeral segments, the `VeiledTrail` every writer records
through, and the purge that records past the boundary on the durable trail), with no side
channels. A test of an offline `hive cells` command builds the real boundary from a manifest
instead: `night_veil_manifest` is one with the fake Virtual backend and a complete Night Veil tier
profile, and `tier_spec` a Cell spec at a tier, labelled as the lifecycle labels every Cell.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - The purge's recorder writes to the durable trail, never to the `VeiledTrail`.

See Also:
    - hivemind.cli.compose.night_veil for the production wiring this mirrors.
    - hivemind.hive.night_veil.boundary for NightVeilBoundary.
"""

from __future__ import annotations

from pathlib import Path

from builders.cli import fake_manifest
from builders.forage import make_capacity

from hivemind.cell import CellIdentity, CombShieldLevel
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.hive.night_veil import NightVeilBoundary, with_tier_label
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import (
    EphemeralSegments,
    MemoryPheromoneTrail,
    MemorySegmentPurge,
    NightVeilCheckpoints,
    NightVeilTeardownPurge,
    SideChannels,
    TrailRecorder,
    VeiledTrail,
)
from waggle.clock import Clock

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion:8710"  # A valid v3 one.

__all__ = ["make_night_veil", "night_veil_manifest", "tier_spec"]


def make_night_veil(
    durable: MemoryPheromoneTrail,
    clock: Clock,
    identity: CellIdentity,
    checkpoints: NightVeilCheckpoints | None = None,
) -> NightVeilBoundary:
    """Build a NightVeilBoundary over `durable`, the way the composition root builds the real one.

    Args:
        durable: The Hive's trail; the boundary's `veiled` trail wraps it.
        clock: Stamps every record the purge makes.
        identity: The Queen's own Hive and node the purge records as.
        checkpoints: Where living Night Veil Cells' counts and ids are kept; hand the same store
            to a restarted Queen's boundary, as the Hive's file is. None keeps none.

    Returns:
        A boundary whose `veiled` trail a lifecycle or Queen under test records through.
    """
    segments = EphemeralSegments(clock, checkpoints)
    recorder = TrailRecorder(
        trail=durable, clock=clock, hive_id=identity.hive_id, node_id=identity.node_id
    )
    purge = NightVeilTeardownPurge(
        MemorySegmentPurge(durable), SideChannels(), recorder, ephemeral=segments
    )
    return NightVeilBoundary(
        segments=segments,
        veiled=VeiledTrail(durable, segments),
        purge=purge,
        recorder=recorder,
    )


def night_veil_manifest(tmp_path: Path) -> HiveManifest:
    """Return a `fake_manifest` with the fake Virtual backend and a complete Night Veil profile.

    Args:
        tmp_path: The test's own directory; the manifest and its `[hive] db` live under it.

    Returns:
        The loaded manifest: `build_virtual_cells` builds a Virtual side, boundary and all, from it.
    """
    manifest_path = fake_manifest(tmp_path)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(
            '\n[virtual_cells]\nbackend = "fake"\n'
            "\n[security.tiers.NIGHT_VEIL]\n"
            'egress_profile = "vpn_tor"\ncontrol_channel = "tor_hidden_service"\n'
            f'hidden_service_address = "{_ONION}"\ntor_socks = "127.0.0.1:9050"\n'
            'locale_profile = "C.UTF-8"\n'
        )
    return load_manifest(manifest_path)


def tier_spec(manifest: HiveManifest, tier: CombShieldLevel) -> VirtualCellSpec:
    """Return a Cell spec at `tier`, labelled with it as the lifecycle labels every Cell.

    Args:
        manifest: Names the Hive the Cell is provisioned for.
        tier: The Comb Shield level the Cell runs at.

    Returns:
        A spec a backend provisions as-is: Night Veil's image and network policy for that tier.
    """
    night_veil = tier is CombShieldLevel.NIGHT_VEIL
    return with_tier_label(
        VirtualCellSpec(
            image="night-veil-ubuntu" if night_veil else "base-ubuntu",
            cpu_cores=1,
            memory_bytes=512 * 1024 * 1024,
            disk_bytes=1024 * 1024 * 1024,
            network_policy=NetworkPolicy.VPN_TOR if night_veil else NetworkPolicy.EGRESS_ONLY,
            capacity=make_capacity(),
            hive_id=manifest.hive.id,
            comb_shield=tier,
        )
    )
