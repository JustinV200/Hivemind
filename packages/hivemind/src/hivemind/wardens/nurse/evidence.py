"""Gather a Nurse Bee's evidence: read the touched files through the Warden's session and the Guard.

ADR-0061: the Warden alone builds a `NurseRequest`, from what it holds and never from what the
Worker says about itself. The files are the one part that needs work: every path a verified
action touched or the claim names is read through the Warden's own `CellSession` (so its lease,
never the Worker's, decides what is reachable), passed through the Guard's untrusted-content
scanner, and cut to the manifest's evidence budget. `read_evidence` does that; `touched_paths`
picks the paths (a claimed artifact only where `bee_may_read` says the bee could read it itself,
asked of the Guard as the Worker's own `read_file` asks), and `spend_left` works out the budget
pre-check's figure from the grant.

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens.nurse`. Called by the accept path
    (`hivemind.wardens.ticks.results`). Calls into `hivemind.cell` (CellSession, its errors),
    `hivemind.guard` (Capability, Enforcer, PolicyRequest), `hivemind.guard.scanner`
    (ContentScanner, ScanSite), `hivemind.common` (PermissionDeniedError), `hivemind.memory`
    (render_untrusted, within_scan), `hivemind.supervision.capping` (NurseFile, the bounds) and
    waggle only.

Key invariants:
    - Every file's text has passed the scanner before it lands in a NurseFile: a PASS is cut to
      what was scanned, a LABEL is fenced under a harder label, a DROP is withheld with a notice.
    - The files together hold at most `max_chars` characters; a file cut to fit is `truncated`.
    - A claimed artifact is read only inside scratch or where the Guard lets the bee itself read
      it at `session_outside_scratch` (every floor, the Hive-state one first, then the policy and
      the bee's own set): the Worker names its artifacts, and the Warden's lease reaches further.
      The decision is read, never escalated: evidence never asks the human.
    - A file is skipped, never an error, when it cannot be read for any reason (out of the lease,
      gone, a directory, binary, refused or over size on a device, a dropped link): the machine
      checks already passed, so the evidence can thin out but never fail the attempt.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.workers.tools.screen for the same scan a Worker's tool results pass through.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import CellError, CellSession
from hivemind.common import PermissionDeniedError
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    EnforcementPoint,
    Enforcer,
    PolicyContext,
    PolicyRequest,
    PrincipalRef,
)
from hivemind.guard.scanner import ContentScanner, ScanAction, ScanSite
from hivemind.memory import UntrustedText, render_untrusted, within_scan
from hivemind.supervision.capping import NurseFile
from hivemind.supervision.capping.checks import MAX_NURSE_FILES
from waggle.errors import TransportError
from waggle.messages.capping import ProposedAction
from waggle.messages.forage import GrantIssued
from waggle.messages.task import ArtifactRef

FILE_LABEL = "touched_file untrusted"  # The fence label a flagged file reaches the Nurse under.
BINARY_PROBE_BYTES = 1_024  # A NUL in the first KiB marks a file the Nurse cannot read as text.
UTF8_MAX_BYTES = (
    4  # The widest utf-8 character: a budget of N chars never needs more than 4N bytes.
)
# Everything get_file raises for one file it will not hand over: out of the lease (PathNotAllowed),
# a device's wall or size limit or a bad transfer (Cell and permission errors), a dropped link,
# and the OS's own (gone, a directory, no access).
_UNREADABLE = (CellError, PermissionDeniedError, TransportError, OSError)

__all__ = [
    "BeeReads",
    "EvidenceReader",
    "bee_may_read",
    "read_evidence",
    "spend_left",
    "touched_paths",
]


@dataclass(frozen=True, slots=True)
class EvidenceReader:
    """How the Warden reads the touched files: its session, the Guard's scanner and the budget."""

    session: CellSession  # The Warden's own session, never the Worker's.
    scanner: ContentScanner  # The Guard's untrusted-content scanner.
    site: Callable[[str], ScanSite]  # Builds the scan site for one path (the Warden reads it).
    max_chars: int  # The manifest's [nurse] max_evidence_chars.


@dataclass(frozen=True, slots=True)
class BeeReads:
    """The parts of the request a bee's own read_file makes outside scratch, and who decides it."""

    enforcer: Enforcer  # The Warden's Enforcer, the one its Workers' tools are handed.
    principal: PrincipalRef  # The Worker, under its assignment's role; never the Warden.
    held: CapabilitySet  # The sub-bee's own set, never the Warden's wider one.
    context: PolicyContext  # The Cell the bee ran on, as its tools describe it.
    scratch_dir: Path  # The session's scratch directory, where a relative path lands.


async def touched_paths(
    actions: Sequence[ProposedAction],
    artifacts: Sequence[ArtifactRef],
    may_read: Callable[[str], Awaitable[bool]],
) -> tuple[str, ...]:
    """Return the paths to read: what verified actions touched, then the readable artifacts.

    A verified action's paths already passed the bee's own Capping gate. A claimed artifact is
    only the Worker's word, so it is kept only where `may_read` says the bee could read it.

    Args:
        actions: The attempt's verified actions, oldest first.
        artifacts: The claim's artifacts.
        may_read: Whether the bee itself may read a path (`bee_may_read`).

    Returns:
        Each path once, in first-seen order, at most MAX_NURSE_FILES of them.
    """
    paths = [path for action in actions for path in action.paths]
    for artifact in artifacts:
        if await may_read(artifact.path):
            paths.append(artifact.path)
    return tuple(dict.fromkeys(paths))[:MAX_NURSE_FILES]


async def bee_may_read(bee: BeeReads, path: str) -> bool:
    """Return whether the bee could read `path` itself, decided as its own read_file decides.

    Outside scratch, read_file asks the Enforcer at `session_outside_scratch` as the Worker: the
    floors refuse first (the Hive-state floor refuses its database, secret store and manifest
    whatever the set holds), then the access level, the deny list and the bee's own set.

    Args:
        bee: The Enforcer and the Worker's request parts.
        path: The path as the Worker named it.

    Returns:
        True inside scratch, else whether the Guard allows it; a refusal is `guard.denied` on the
        trail, and its escalation is never acted on (evidence is skipped, never asked about).
    """
    joined = Path(path) if Path(path).is_absolute() else bee.scratch_dir / path
    resolved = joined.resolve(strict=False)
    scratch = bee.scratch_dir.resolve(strict=False)
    if resolved == scratch or scratch in resolved.parents:
        return True
    request = PolicyRequest(
        principal=bee.principal,
        point=EnforcementPoint.SESSION_OUTSIDE_SCRATCH,
        needed=Capability(family=CapabilityFamily.FS_READ, scope=resolved.as_posix()),
        held=bee.held,
        context=bee.context,
    )
    # A local policy decision, plus one local trail write on a refusal.
    return (await bee.enforcer.check(request)).allowed


async def read_evidence(reader: EvidenceReader, paths: Iterable[str]) -> tuple[NurseFile, ...]:
    """Read, scan and cut each path in turn until the evidence budget is spent.

    Args:
        reader: The Warden's session, scanner and budget.
        paths: The paths to read, in priority order.

    Returns:
        One NurseFile per readable path, in order, together within `reader.max_chars`. A path
        that cannot be read for any reason is left out, never raised.
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
        # Decode only what could fit, never a huge file whole; a cut character becomes U+FFFD.
        head = raw[: budget * UTF8_MAX_BYTES]
        text = head.decode("utf-8", errors="replace")
        # Only the part that fits is scanned and shown; the fence may lengthen it, so cut again.
        shown = await _screen(reader, path, text[:budget])
        cut = len(head) < len(raw) or len(text) > budget or len(shown) > budget
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
