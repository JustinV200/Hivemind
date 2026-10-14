"""Provide CellAttestation: attest a freshly provisioned Virtual Cell at its own Comb Shield tier.

A Virtual Cell is attested from its image before it is ready, and what that means depends on the
tier it was provisioned at (ADR-0030, ADR-0057): a MEADOW Cell is not attested at all; a PROPOLIS
Cell is attested by the tunnel checks and Tor's absence (`hivemind.hive.propolis`); a NIGHT_VEIL
Cell by Night Veil's fourteen (`hivemind.hive.night_veil`). `CellAttestation` is that choice, made
once and by tier: a table from the tier to the function that attests it, so the provider
(`hivemind.queen.cell_gate.provider`) asks one question of a Cell, "attest this", and never learns
which tier needs what, and a tier added later is one more entry and not another branch. Each tier
brings a probe factory, never a default: a probe needs a live session on the very Cell being
attested, which only a composition root can open, and a fake that quietly stood in for a real one
would attest a Cell nobody looked at. A tier that needs attestation and has no probe configured is
refused, not skipped.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive`. Built by the composition root
    (`hivemind.cli.compose.virtual_cells`) and held by `hivemind.queen.cell_gate.provider.
    LifecycleVirtualCellProvider`, which calls `attest` after a Cell's link is found and before
    `mark_ready`. Calls into `hive.night_veil` and `hive.propolis` (each tier's attestation),
    `hivemind.cell` (Cell, CombShieldLevel), `hivemind.common.errors` and `hivemind.pheromone`.

Key invariants:
    - `attest` returns None for MEADOW alone; every other tier either yields an `Attestation`
      (recorded as one `cell.attested` event, red or green) or raises.
    - A tier that needs attestation and has no probe factory is refused with a
      `ConfigurationError`, never attested against nothing.
    - A probe factory is called once per Cell, with that Cell; it is never shared across Cells.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md and
      docs/adr/0057-propolis-is-verified-by-looking.md for the two tiers' attestation.
    - hivemind.queen.cell_gate.provider for the one caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from hivemind.cell import Cell, CombShieldLevel
from hivemind.common.errors import ConfigurationError
from hivemind.hive.night_veil import Attestation, NightVeilProbe, attest_cell
from hivemind.hive.propolis import PropolisProbe, attest_propolis_cell
from hivemind.pheromone import TrailRecorder

# Builds the probe for the Cell being attested. A callable and not a probe, since a real probe holds
# a session bound to *this* Cell, which is not one that can be shared across every provision.
NightVeilProbeFactory = Callable[[Cell], NightVeilProbe]
PropolisProbeFactory = Callable[[Cell], PropolisProbe]

__all__ = ["CellAttestation", "NightVeilProbeFactory", "PropolisProbeFactory"]


class CellAttestation:
    """Attest a Virtual Cell once, at the tier it was provisioned at, and record the verdict."""

    def __init__(
        self,
        trail: TrailRecorder,
        night_veil: NightVeilProbeFactory,
        propolis: PropolisProbeFactory | None = None,
    ) -> None:
        """Build the attestation from the probe factory each tier that needs one brings.

        Args:
            trail: The Queen-side trail-writer identity every `cell.attested` event is written
                with.
            night_veil: Builds the probe a freshly provisioned NIGHT_VEIL Cell is attested by.
            propolis: Builds the probe a freshly provisioned PROPOLIS Cell is attested by; None
                (no probe configured) refuses every PROPOLIS Cell rather than letting one pass.
        """
        self._trail = trail
        self._night_veil = night_veil
        self._propolis = propolis
        # The tier -> attestation table; MEADOW is deliberately absent (attested by nothing).
        self._by_tier: dict[CombShieldLevel, Callable[[Cell], Awaitable[Attestation]]] = {
            CombShieldLevel.NIGHT_VEIL: self._attest_night_veil,
            CombShieldLevel.PROPOLIS: self._attest_propolis,
        }

    async def attest(self, cell: Cell) -> Attestation | None:
        """Attest `cell` at its own tier, recording one `cell.attested` event.

        Args:
            cell: The freshly provisioned Cell; its `comb_shield` picks the attestation.

        Returns:
            The Attestation (`passed`, and `red` naming every failing check when it is not), or
            None for a tier that needs none (MEADOW). The caller decides what a red one means.

        Raises:
            ConfigurationError: The tier needs a probe and none is configured.
            HiveMindError: The probe could not run (a session that never opened, a denied
                command the probe did not already turn into a failed check).
        """
        attest_tier = self._by_tier.get(cell.comb_shield)
        if attest_tier is None:
            return None  # MEADOW: the broad baseline, vouched for by no one and checked by no one.
        return await attest_tier(cell)

    async def _attest_night_veil(self, cell: Cell) -> Attestation:
        """Attest a NIGHT_VEIL Cell with the probe `night_veil` builds for it."""
        return await attest_cell(self._night_veil(cell), self._trail, cell.id)

    async def _attest_propolis(self, cell: Cell) -> Attestation:
        """Attest a PROPOLIS Cell with the probe `propolis` builds for it, or refuse it."""
        if self._propolis is None:
            raise ConfigurationError(
                f"Cell {cell.id} was provisioned at PROPOLIS, but no PROPOLIS attestation probe "
                "is configured: a Cell that cannot attest its tier is refused, not passed."
            )
        return await attest_propolis_cell(self._propolis(cell), self._trail, cell.id)
