"""Attest a Night Veil Cell from its image before CellReady, and keep its boundary until teardown.

ADR-0030: "Readiness is attestation of an image, never configuration of a Cell." This package holds
every piece of that attestation: `results` (`CheckStatus`, `CheckResult`, `Attestation`,
`CHECK_NAMES`, `attest`) is the pure judgement; `probe` (`NightVeilProbe`) is the Protocol one check
per codingrules 8.7 requirement implements; `fake` (`FakeNightVeilProbe`) and `session_probe`
(`SessionNightVeilProbe`, over `session_commands`' own fixed, documented commands) are its two
implementations; `runner` (`run_checks`) calls every check on a probe; `trail` (`attest_cell`) is
the effectful edge that runs the checks, judges them and records one `cell.attested` event, win or
lose. Nothing here installs anything: every check reads the Cell's own already-provisioned state
(codingrules section 8.7: "attestation checks an image rather than configuring a Cell at runtime").
`boundary` (`NightVeilBoundary`) is the tier's other hive-side half, its retention boundary
(codingrules section 12): it opens a Night Veil Cell's ephemeral segment when the lifecycle
provisions one, purges it on every path the Cell ends, and sweeps the ones a Queen restart finds
gone, over `hivemind.pheromone.retention`.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive`. Attestation is called by `hivemind.queen.cell_gate.
    provider.LifecycleVirtualCellProvider.acquire` before a NIGHT_VEIL Cell is handed to
    placement; the boundary by `hivemind.hive.lifecycle.CellLifecycle` and the Absconding pass.
    Calls into `hivemind.cell` (CellSession, ExecSpec, run, CombShieldLevel), `hivemind.hive.
    models`, `hivemind.pheromone` (CellEvent, TrailRecorder, the retention boundary) and waggle.

Key invariants:
    - `attest`'s "no partial pass" holds however a probe is reached: `attest_cell` always records
      the full per-check result, red or green, before returning (`trail.py`'s own key invariant).
    - `NightVeilProbe` is the only seam a caller needs: `FakeNightVeilProbe` and
      `SessionNightVeilProbe` are interchangeable behind it (codingrules section 8.1).

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the design this package
      implements.
    - .claude/codingrules.md section 8.7 for the exact list of checks this package runs.
    - .claude/roadmap.md step 5.7b for this package's own roadmap bullet.

Public API:
    - CheckStatus, CheckResult, Attestation, CHECK_NAMES, attest: the pure judgement (results).
    - NightVeilProbe: the Protocol every check implements (probe).
    - FakeNightVeilProbe: an in-memory, all-green-by-default implementation (fake).
    - SessionProbeConfig, SessionNightVeilProbe: the real implementation, over a CellSession
      (session_commands, session_probe).
    - run_checks: call every check on a probe, collecting results by name (runner).
    - attest_cell: the effectful edge -- run, judge, record one cell.attested event (trail).
    - NightVeilBoundary, provisioned_facts, failure_facts, end_night_veil, adopt_night_veil,
      sweep_night_veil, night_veil_cells, is_night_veil_cell, with_tier_label, tier_from_labels,
      TIER_LABEL: the retention boundary's open, purge and restart sweep (boundary).
"""

from hivemind.hive.night_veil.boundary import (
    TIER_LABEL,
    NightVeilBoundary,
    adopt_night_veil,
    end_night_veil,
    failure_facts,
    is_night_veil_cell,
    night_veil_cells,
    provisioned_facts,
    sweep_night_veil,
    tier_from_labels,
    with_tier_label,
)
from hivemind.hive.night_veil.fake import FakeNightVeilProbe
from hivemind.hive.night_veil.probe import NightVeilProbe
from hivemind.hive.night_veil.results import (
    CHECK_NAMES,
    Attestation,
    CheckResult,
    CheckStatus,
    attest,
)
from hivemind.hive.night_veil.runner import run_checks
from hivemind.hive.night_veil.session_commands import SessionProbeConfig
from hivemind.hive.night_veil.session_probe import SessionNightVeilProbe
from hivemind.hive.night_veil.trail import attest_cell

__all__ = [
    "CHECK_NAMES",
    "TIER_LABEL",
    "Attestation",
    "CheckResult",
    "CheckStatus",
    "FakeNightVeilProbe",
    "NightVeilBoundary",
    "NightVeilProbe",
    "SessionNightVeilProbe",
    "SessionProbeConfig",
    "adopt_night_veil",
    "attest",
    "attest_cell",
    "end_night_veil",
    "failure_facts",
    "is_night_veil_cell",
    "night_veil_cells",
    "provisioned_facts",
    "run_checks",
    "sweep_night_veil",
    "tier_from_labels",
    "with_tier_label",
]
