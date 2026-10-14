"""Record how a search for a lost link ended, and deposit what it found as Nectar.

The two things a search leaves behind (roadmap 11.9a, ADR-0056). `record_verdict` writes
`warden.diagnosed` on the Warden's own trail: the outcome, one stable word for why, how many
Drones were sent and how long the search took, and whether the findings were deposited; counts and
short words only, never a word a Drone wrote (codingrules 12). `deposit_findings` is where the
words go: one Nectar document, raw findings for the Honey Store's House Bee to ripen, sent up as
chunks by the Warden itself, so it belongs to no task of the Queen's and names no Worker. The link
is usually still down when the search ends, and the offline policy queues Nectar durably, so the
deposit reaches the Queen after the reconnection, ahead of nothing and behind everything queued
before it. It is C2, always: it describes the operator's machine and its network.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline.diagnose sub-package. Called by `.step` when a search finishes. Calls into
    `hivemind.cell` (the labels), `hivemind.wardens.offline.freeze` (`record_event`),
    `hivemind.wardens.ticks.basket.door` (`send_nectar`), `hivemind.workers.nectar` and waggle
    only.

Key invariants:
    - The trail event carries counts and words only; the findings go to the Honey Store alone.
    - The deposit is made once per search that sent a Drone, whatever the verdict: the Hive learns
      why a device drops even when no Drone could help.
    - A deposit that could not be sent says so in the event and never raises.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for why the findings are Nectar.
    - hivemind.wardens.offline.policy for the row that queues a chunk while the link is down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.wardens.offline.diagnose.decide import Finding, Verdict
from hivemind.wardens.offline.freeze import record_event
from hivemind.wardens.ticks.basket.door import send_nectar
from hivemind.workers.nectar import DepositMeta
from waggle.messages.honey import NectarKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hivemind.wardens.warden import Warden

DIAGNOSED_EVENT = "warden.diagnosed"  # The trail kind ADR-0056 names.
NECTAR_MEDIA_TYPE = "text/markdown"  # What a Drone's closing text is written as.
NECTAR_TITLE = "Why the link to the Queen was lost"  # One line for the browser and the ripener.
NOTHING_REPORTED = "The Drone reported nothing."  # A Drone that ended without a word to keep.

__all__ = ["Judged", "deposit_findings", "findings_document", "record_verdict"]


@dataclass(frozen=True, slots=True)
class Judged:
    """How a search ended, and the counts that go on the trail with it (codingrules 5.1's group).

    Attributes:
        verdict: The outcome.
        reason: The stable word for why (`decide_verdict`).
        attempts: How many Drones were sent.
        spent_s: Seconds from the link going to the search ending.
        deposited: Whether the findings were deposited.
    """

    verdict: Verdict
    reason: str
    attempts: int
    spent_s: float
    deposited: bool


def findings_document(verdict: Verdict, peer: str, findings: Sequence[Finding]) -> str:
    """Write the Nectar a finished search leaves: the outcome, then what each Drone reported.

    Args:
        verdict: How the search ended.
        peer: The Queen's host the link was to, or empty.
        findings: What each Drone reported, oldest first; empty when the search was stopped before
            any Drone said anything.

    Returns:
        The document, never empty.
    """
    where = f" to {peer}" if peer else ""
    parts = [f"# The link{where} was lost: {verdict.value}"]
    for number, finding in enumerate(findings, start=1):
        parts.append(f"## Drone {number}\n\n{finding.text.strip() or NOTHING_REPORTED}")
    if not findings:
        parts.append(NOTHING_REPORTED)
    return "\n\n".join(parts) + "\n"


async def deposit_findings(warden: Warden, document: str) -> bool:
    """Send `document` up as C2 Nectar the Warden itself deposits.

    Args:
        warden: The Warden whose Cell the findings are about.
        document: What `findings_document` wrote.

    Returns:
        True when every chunk was sent or queued behind the link; False when one was lost.
    """
    cell = warden._cell
    if cell is None:
        return False
    meta = DepositMeta(
        kind=NectarKind.FINDING,
        media_type=NECTAR_MEDIA_TYPE,
        title=NECTAR_TITLE,
        task_id=None,  # A search belongs to no task of the Queen's.
        cell_id=cell.id,
        worker_id=None,  # The Warden deposits this one.
        observed_at=warden._deps.clock.now(),
        clearance=HoneyClearance.C2.to_wire(),
        origin_tier=cell.comb_shield.to_wire(),
    )
    return await send_nectar(warden, document.encode("utf-8"), meta)


async def record_verdict(warden: Warden, judged: Judged) -> None:
    """Record `warden.diagnosed`: the outcome and the counts, never a word a Drone wrote.

    Args:
        warden: The Warden whose trail records it.
        judged: How the search ended.
    """
    await record_event(
        warden,
        DIAGNOSED_EVENT,
        outcome=judged.verdict.value,
        reason=judged.reason,
        attempts=judged.attempts,
        spent_s=round(judged.spent_s, 3),
        deposited=judged.deposited,
    )
