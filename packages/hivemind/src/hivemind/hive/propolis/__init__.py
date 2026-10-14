"""Attest a Propolis Cell from its image before CellReady: the tunnel checks, and Tor is off.

The Propolis tier (the Comb Shield tier that requires a VPN and disables Tor) is, on a Real Cell,
the operator's word that the device already runs a tunnel, checked before each lease. On a Virtual
Cell (a VM or container the Hive provisions) it is a claim about an image, and the Hive attests it
the way ADR-0030 has it attest Night Veil's: before `CellReady`, by looking and never by
configuring. The checks are the Real Cell's own (the default route through the tunnel, DNS
through it, no direct IPv6 path, direct egress refused: `hivemind.cell.tunnel`) plus one more, that
Tor is not running in the Cell. This package holds that attestation: `probe` (`PropolisProbe`,
`PROPOLIS_CHECK_NAMES`) is the Protocol that reports the checks; `session_probe`
(`SessionPropolisProbe`) makes them over the Cell's own `CellSession` and `fake`
(`FakePropolisProbe`) answers from a table; `tor` is the Tor check's fixed reads and pure judge;
`trail` (`attest_propolis_cell`) runs the probe, judges it and records one `cell.attested` event
naming the tier, win or lose. Nothing here installs anything.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive`. Called by `hivemind.hive.attestation.
    CellAttestation`, which `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider` asks
    before a PROPOLIS Cell is marked ready. Calls into `hivemind.cell` (CellSession, OsFamily,
    CombShieldLevel), `hivemind.cell.tunnel` (the shared checks), `hivemind.hive.night_veil` (the
    result types and the recording half), `hivemind.pheromone` and waggle.

Key invariants:
    - A PROPOLIS Cell is attested by the same commands and judges a Real Cell's tunnel is checked
      by, from `hivemind.cell.tunnel`, so the two cannot drift.
    - `attest_propolis_cell` always records the full per-check result, red or green, and a check
      the probe left out or called not applicable is red.
    - `PropolisProbe` is the only seam a caller needs: `FakePropolisProbe` and
      `SessionPropolisProbe` are interchangeable behind it (codingrules section 8.1).

See Also:
    - docs/adr/0057-propolis-is-verified-by-looking.md for the decision this package implements.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for "readiness is attestation
      of an image", which this carries to the second tier.
    - hivemind.hive.night_veil for the Night Veil attestation this is modelled on.

Public API:
    - PropolisProbe, PROPOLIS_CHECK_NAMES: the Protocol every check is reported through (probe).
    - FakePropolisProbe: an in-memory, all-green-by-default implementation (fake).
    - SessionPropolisProbe: the real implementation, over a CellSession (session_probe).
    - CHECK_TOR, TOR_PORTS, TOR_PROCESS_NAMES, check_tor_disabled, judge_tor_disabled: the Tor
      check (tor).
    - attest_propolis_cell: the effectful edge -- run, judge, record one cell.attested event
      (trail).
"""

from hivemind.hive.propolis.fake import FakePropolisProbe
from hivemind.hive.propolis.probe import PROPOLIS_CHECK_NAMES, PropolisProbe
from hivemind.hive.propolis.session_probe import SessionPropolisProbe
from hivemind.hive.propolis.tor import (
    CHECK_TOR,
    TOR_PORTS,
    TOR_PROCESS_NAMES,
    check_tor_disabled,
    judge_tor_disabled,
)
from hivemind.hive.propolis.trail import attest_propolis_cell

__all__ = [
    "CHECK_TOR",
    "PROPOLIS_CHECK_NAMES",
    "TOR_PORTS",
    "TOR_PROCESS_NAMES",
    "FakePropolisProbe",
    "PropolisProbe",
    "SessionPropolisProbe",
    "attest_propolis_cell",
    "check_tor_disabled",
    "judge_tor_disabled",
]
