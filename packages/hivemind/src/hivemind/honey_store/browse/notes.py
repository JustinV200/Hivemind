"""Turn a note the human proposes from a Honey folder into a Cell Wax proposal or a queued note.

The Honey browser never writes Honey (ripened knowledge in the Honey Store, the Hive's cold
tier); every write reaches it through the Queen's process (roadmap 7.10). Proposing a note is how
a human adds to it from a folder: from a Cell's folder the note is about that Cell, so it becomes
a Cell Wax proposal (a caution the Queen alone writes or rejects), which this module only
describes -- the caller files it through the memory tier's own wax proposal path, since this
package may not import `hivemind.memory`. From anywhere else the note is queued with
`HoneyStore.add_proposal` and a `honey.note_proposed` event, for the House Bee (the maintenance
Worker) to take in as HUMAN-origin Nectar, which intake labels C2 whatever else is said (anything
from a human message is C2, codingrules 8.9).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Called by `hivemind.honey_store.browse.browser.HoneyBrowser.propose_note`.
    Calls into this package's own store, scope and identity, `hivemind.cell`, its `paths`,
    `sources` and `errors`, and `waggle` only.

Key invariants:
    - A queued note and its `honey.note_proposed` event commit together (the store's contract);
      a Cell Wax proposal writes nothing here at all.
    - The event carries the scope and two lengths, never the note's title or text.
    - A note's title and text are checked here against what the House Bee's intake and the wax
      model will accept, so a proposal never queues something that cannot be drained.

See Also:
    - hivemind.honey_store.store.protocol.HoneyStore.add_proposal for the queue.
    - hivemind.memory.cell_wax.writes.propose_wax for the wax path a CellWaxProposal is filed on.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.errors import BrowseInputError, BrowsePathError
from hivemind.honey_store.browse.paths import CELL_SCOPE_PREFIX, parse_path
from hivemind.honey_store.browse.sources import BrowserDeps
from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.scope import HIVE_SCOPE
from waggle.errors import InvalidIdError
from waggle.ids import CellId, IdKind, parse_id
from waggle.messages.cell.wax import MAX_WAX_TEXT_CHARS
from waggle.messages.honey.exchange import MAX_TITLE_CHARS

NOTE_PROPOSED_KIND = "honey.note_proposed"  # The trail event a queued note records.
# A proposed note is a human message, so it is Royal by provenance (codingrules 8.9): intake
# labels HUMAN-origin Nectar C2 regardless, and a wax proposal from a Cell folder says the same.
NOTE_CLEARANCE = HoneyClearance.C2
MAX_NOTE_TEXT_CHARS = 20_000  # A few pages of the human's own notes; a document is a deposit.

__all__ = [
    "MAX_NOTE_TEXT_CHARS",
    "NOTE_CLEARANCE",
    "NOTE_PROPOSED_KIND",
    "CellWaxProposal",
    "NoteProposal",
    "QueuedHoneyNote",
    "propose_note",
]


@dataclass(frozen=True, slots=True)
class CellWaxProposal:
    """A note proposed from a Cell's folder: the caller files it as a PROPOSED Cell Wax note."""

    path: str  # The folder it was proposed from, normalised.
    cell_id: CellId  # The Cell the note is about.
    text: str  # The caution itself.
    reason: str  # Why: the note's title and where it was proposed.
    clearance: HoneyClearance  # NOTE_CLEARANCE: a human message.


@dataclass(frozen=True, slots=True)
class QueuedHoneyNote:
    """A note proposed anywhere else: queued for the House Bee to take in as HUMAN Nectar."""

    path: str  # The folder it was proposed from, normalised.
    proposal_id: str  # The queued proposal's own id.
    scope: str  # The scope its Honey will be filed under.
    clearance: HoneyClearance  # NOTE_CLEARANCE: what intake will label it.


# What proposing a note produced: a Cell Wax proposal to file, or a note already queued.
type NoteProposal = CellWaxProposal | QueuedHoneyNote


async def propose_note(deps: BrowserDeps, path: str, title: str, text: str) -> NoteProposal:
    """Propose a note from the folder at `path`.

    Args:
        deps: The store to queue on, and the identity and clock the event is stamped with.
        path: Where the note is proposed; a document path counts as its folder.
        title: A one-line label, at most `MAX_TITLE_CHARS`.
        text: The note itself; at most `MAX_NOTE_TEXT_CHARS`, or `MAX_WAX_TEXT_CHARS` from a
            Cell's folder.

    Returns:
        A CellWaxProposal from a Cell's folder (nothing written yet), else the QueuedHoneyNote
        already on the House Bee's queue.

    Raises:
        BrowsePathError: `path` does not parse, or names a Cell whose id is not a Cell id.
        BrowseInputError: The title or text is empty or too long.
    """
    title, text = _checked(title, text)
    target = parse_path(path)
    scope = target.scope
    # Anything inside a Cell's folder (the folder, a row, its wax) is about that Cell.
    if scope is not None and scope.startswith(CELL_SCOPE_PREFIX):
        return _wax_proposal(target.path, scope, title, text)
    # Everywhere else keeps its own scope; the root, the index folders and Bee Bread have none of
    # their own, so the note joins shared knowledge.
    home = scope if scope is not None else HIVE_SCOPE
    event = honey_event(
        deps.identity,
        deps.clock,
        NOTE_PROPOSED_KIND,
        deps.identity.hive_id,
        scope=home,
        title_chars=len(title),
        text_chars=len(text),
    )
    # One transaction for the queued row and its event; local SQLite, milliseconds.
    proposal_id = await deps.store.add_proposal(home, title, text, event)
    return QueuedHoneyNote(
        path=target.path, proposal_id=proposal_id, scope=home, clearance=NOTE_CLEARANCE
    )


def _checked(title: str, text: str) -> tuple[str, str]:
    """Return the stripped title and text, refusing either when empty or too long."""
    title, text = title.strip(), text.strip()
    if not title:
        raise BrowseInputError("title", "it is empty")
    # Nectar titles are bounded on the wire; the House Bee's drain would refuse a longer one.
    if len(title) > MAX_TITLE_CHARS:
        raise BrowseInputError("title", f"it is over {MAX_TITLE_CHARS} characters")
    if not text:
        raise BrowseInputError("text", "it is empty")
    if len(text) > MAX_NOTE_TEXT_CHARS:
        raise BrowseInputError("text", f"it is over {MAX_NOTE_TEXT_CHARS} characters")
    return title, text


def _wax_proposal(path: str, scope: str, title: str, text: str) -> CellWaxProposal:
    """Describe the Cell Wax proposal a note from a Cell's folder becomes."""
    ident = scope.removeprefix(CELL_SCOPE_PREFIX)
    try:
        # A scope id may be any letters and digits; a Cell Wax note needs a real Cell id.
        cell_id = CellId(parse_id(ident, IdKind.CELL))
    except InvalidIdError as exc:
        raise BrowsePathError(path, f"{ident!r} is not a Cell id, so no Cell Wax fits") from exc
    # Wax text is bounded on the wire; the manifest's own cap may be lower still (propose_wax).
    if len(text) > MAX_WAX_TEXT_CHARS:
        raise BrowseInputError("text", f"Cell Wax is at most {MAX_WAX_TEXT_CHARS} characters")
    reason = f"{title} (proposed by the human from the Honey folder {path})"
    return CellWaxProposal(
        path=path, cell_id=cell_id, text=text, reason=reason, clearance=NOTE_CLEARANCE
    )
