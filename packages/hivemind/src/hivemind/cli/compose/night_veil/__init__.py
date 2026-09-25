"""Build what the Queen and the backends need to honour Night Veil, from a loaded manifest.

Roadmap step 10.3a and codingrules section 12, split by responsibility (codingrules 5.2) once the
tier's retention boundary grew its side channels: `boundary` turns `[security.tiers.NIGHT_VEIL]`
into the placement policy and the link every backend hands a Night Veil Cell, and builds the
boundary itself (the `VeiledTrail` every Queen-side writer records through, and the teardown
purge); `side_channels` attaches every store besides the trail that the purge must clear (the
Queen's memory tables, the Brood Chamber, the Forage ledger, the backends' snapshot images) once
the composition root has them. It was a single module until the side channels arrived; importers
keep naming this face.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.deps`, `.hive`, `.virtual_cell_backends`, `.virtual_cells` and
    `hivemind.cli.readback.virtual`.

Key invariants:
    - This face holds re-exports only (codingrules 5.4).

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the tier's boundary.
    - hivemind.pheromone.retention for the boundary these build.

Public API:
    - night_veil_constraints, night_veil_link, tor_socks_url, in_process_providers,
      local_providers, veil_trail, build_night_veil: the tier's profile and boundary (boundary).
    - attach_side_channels, NightVeilStores: every store the teardown purge clears
      (side_channels).
"""

from hivemind.cli.compose.night_veil.boundary import (
    build_night_veil,
    in_process_providers,
    local_providers,
    night_veil_constraints,
    night_veil_link,
    tor_socks_url,
    veil_trail,
)
from hivemind.cli.compose.night_veil.side_channels import NightVeilStores, attach_side_channels

__all__ = [
    "NightVeilStores",
    "attach_side_channels",
    "build_night_veil",
    "in_process_providers",
    "local_providers",
    "night_veil_constraints",
    "night_veil_link",
    "tor_socks_url",
    "veil_trail",
]
