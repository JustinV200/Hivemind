"""Attest a Night Veil Cell deterministically from its image, before CellReady (roadmap step 5.7b).

ADR-0030: "Readiness is attestation of an image, never configuration of a Cell." This package holds
every piece of that attestation: `results` (`CheckStatus`, `CheckResult`, `Attestation`,
`CHECK_NAMES`, `attest`) is the pure judgement; `probe` (`NightVeilProbe`) is the Protocol one check
per codingrules 8.7 requirement implements; `fake` (`FakeNightVeilProbe`) and `session_probe`
(`SessionNightVeilProbe`, over `session_commands`' own fixed, documented commands) are its two
implementations; `runner` (`run_checks`) calls every check on a probe; `trail` (`attest_cell`) is
the effectful edge that runs the checks, judges them and records one `cell.attested` event, win or
lose. Nothing here installs anything: every check reads the Cell's own already-provisioned state
(codingrules section 8.7: "attestation checks an image rather than configuring a Cell at runtime").

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive`. Called by whatever gates `CellReady` for a
    NIGHT_VEIL Cell before it is handed to placement (a report item: the exact call site is
    `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider.acquire`, outside this
    dispatch's file list). Calls into `hivemind.cell` (CellSession, ExecSpec, run),
    `hivemind.pheromone` (CellEvent, TrailRecorder) and `waggle.ids` only.

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
"""

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
    "Attestation",
    "CheckResult",
    "CheckStatus",
    "FakeNightVeilProbe",
    "NightVeilProbe",
    "SessionNightVeilProbe",
    "SessionProbeConfig",
    "attest",
    "attest_cell",
    "run_checks",
]
