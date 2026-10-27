"""Gather a Nurse Bee's evidence: read the touched files through the Warden's session and the Guard.

ADR-0061: the Warden alone builds a `NurseRequest`, from what it holds and never from what the
Worker says about itself. The files are the one part that needs work: every path a verified
action touched or the claim names is read through the Warden's own `CellSession` (so its lease,
never the Worker's, decides what is reachable), passed through the Guard's untrusted-content
scanner, and cut to the manifest's evidence budget. `read_evidence` does that; `touched_paths`
picks the paths, and `spend_left` works out the budget pre-check's figure from the grant.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Called by the accept path
    (`hivemind.wardens.ticks.results`). Calls into `hivemind.cell` (CellSession, its errors),
    `hivemind.guard.scanner` (ContentScanner, ScanSite), `hivemind.memory` (render_untrusted,
    within_scan), `hivemind.supervision.capping` (NurseFile, the bounds) and waggle only.

Key invariants:
    - Every file's text has passed the scanner before it lands in a NurseFile: a PASS is cut to
      what was scanned, a LABEL is fenced under a harder label, a DROP is withheld with a notice.
    - The files together hold at most `max_chars` characters; a file cut to fit is `truncated`.
    - A file the lease cannot reach, that is gone, is a directory, or is binary is skipped, never
      an error: the Nurse still sees its action. Any other Cell error propagates, as for an
      acceptance check, because the device itself went away.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.workers.tools.screen for the same scan a Worker's tool results pass through.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import CellSession, PathNotAllowedError
from hivemind.guard.scanner import ContentScanner, ScanAction, ScanSite
from hivemind.memory import UntrustedText, render_untrusted, within_scan
from hivemind.supervision.capping import NurseFile
from hivemind.supervision.capping.checks import MAX_NURSE_FILES
from waggle.messages.capping import ProposedAction
from waggle.messages.forage import GrantIssued
from waggle.messages.task import ArtifactRef

FILE_LABEL = "touched_file untrusted"  # The fence label a flagged file reaches the Nurse under.
BINARY_PROBE_BYTES = 1_024  # A NUL in the first KiB marks a file the Nurse cannot read as text.
# What a file the lease cannot reach, or that is gone or a directory, raises from get_file.
_UNREADABLE = (PathNotAllowedError, FileNotFoundError, IsADirectoryError, PermissionError)

__all__ = ["EvidenceReader", "read_evidence", "spend_left", "touched_paths"]


@dataclass(frozen=True, slots=True)
class EvidenceReader:
    """How the Warden reads the touched files: its session, the Guard's scanner and the budget."""

    session: CellSession  # The Warden's own session, never the Worker's.
    scanner: ContentScanner  # The Guard's untrusted-content scanner.
    site: Callable[[str], ScanSite]  # Builds the scan site for one path (the Warden reads it).
    max_chars: int  # The manifest's [nurse] max_evidence_chars.


def touched_paths(
    actions: Sequence[ProposedAction], artifacts: Sequence[ArtifactRef]
) -> tuple[str, ...]:
    """Return the paths to read: what verified actions touched, then the claimed artifacts.

    Args:
        actions: The attempt's verified actions, oldest first.
        artifacts: The claim's artifacts.

    Returns:
        Each path once, in first-seen order, at most MAX_NURSE_FILES of them.
    """
    paths = [path for action in actions for path in action.paths]
    paths += [artifact.path for artifact in artifacts]
    return tuple(dict.fromkeys(paths))[:MAX_NURSE_FILES]


async def read_evidence(reader: EvidenceReader, paths: Iterable[str]) -> tuple[NurseFile, ...]:
    """Read, scan and cut each path in turn until the evidence budget is spent.

    Args:
        reader: The Warden's session, scanner and budget.
        paths: The paths to read, in priority order.

    Returns:
        One NurseFile per readable path, in order, together within `reader.max_chars`.

    Raises:
        hivemind.cell.CellError: The session itself failed (closed, or the device went).
    """
    files: list[NurseFile] = []
    budget = reader.max_chars
    for path in paths:
        if budget <= 0:
            break  # Later paths are still named by their actions; their text would not fit.
        # Latency class: one session read per file, local disk or one round trip to a device.
        try:
            raw = await reader.session.get_file(Path(path))
        except _UNREADABLE:
            continue
        if b"\x00" in raw[:BINARY_PROBE_BYTES]:
            continue  # Binary: no text a reviewer could read.
        text = raw.decode("utf-8", errors="replace")
        # Only the part that fits is scanned and shown; the fence may lengthen it, so cut again.
        shown = await _screen(reader, path, text[:budget])
        cut = len(text) > budget or len(shown) > budget
        files.append(NurseFile(path=path, text=shown[:budget], truncated=cut))
        budget -= min(len(shown), budget)
    return tuple(files)


def spend_left(grant: GrantIssued | None, spent_by_attempt: float) -> float | None:
    """Return what the task's grant has left after this attempt's own spend, or None uncapped.

    Args:
        grant: The grant the task ran under, or None when the Warden no longer holds it.
        spent_by_attempt: The claim's spend, not yet on the grant's own `spent`.

    Returns:
        The remaining budget, never below zero; None when there is no grant to bound it.
    """
    if grant is None:
        return None
    return max(0.0, grant.spend_budget - grant.spent - spent_by_attempt)


async def _screen(reader: EvidenceReader, path: str, text: str) -> str:
    """Scan `text` as the Warden reading `path`, and return what the Nurse may see of it."""
    verdict = await reader.scanner.scan(text, reader.site(path))
    if verdict.action is ScanAction.PASS:
        return within_scan(text, verdict)
    # Flagged or dropped: a harder fence, or a withheld notice in place of the text.
    return render_untrusted(UntrustedText(label=FILE_LABEL, text=text, verdict=verdict))
