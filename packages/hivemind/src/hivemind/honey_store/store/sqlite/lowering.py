"""SQL and transactions for `honey_lowerings`: filing, reading, noting and deciding label lowerings.

A lowering proposal asks to lower one Nectar's label (a raw deposit in the Honey Store and the Honey
rows ripened from it) that only the Real Cell floor holds up (ADR-0034). This module is the store's
side of it. `select_lowering_candidates` finds what may be proposed, mirroring the pure rule
(`hivemind.honey_store.lowering.rules.lowering_target`) in SQL so an ineligible row never takes a
place under a pass's bound. `add_lowering_transaction` files one proposal, copying the Ripener's
staged reason onto it. `apply_lowering_transaction` is the one place a label goes down: it re-reads
the Nectar, re-runs the rule, and only when the proposal's target still stands lowers the Nectar and
every Honey row of it still at the old label, reads them back (the postcondition) and records the
decision; otherwise it rejects the proposal as no longer eligible. Every state change passes
`hivemind.honey_store.lowering.state.assert_transition` inside its own transaction, beside the
events its caller builds from the outcome.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.cell`, `hivemind.common.sqlite` (transaction), `hivemind.honey_store`'s
    clearance, errors, models and `lowering` (its models, rule and state machine), the store's
    protocol and `.nectar` sibling, `hivemind.pheromone` (insert_event) and `waggle` only.

Key invariants:
    - A label is lowered only inside `apply_lowering_transaction`, only when `lowering_target` of
      the Nectar as it stands in that transaction still equals the proposal's target and its label
      still equals the proposal's origin label, and only together with its LOWERED edge and event.
    - A lowering reads its rows back before committing; if the Nectar or any Honey row of it still
      carries the old label, the whole transaction rolls back (`LoweringPostconditionError`).
    - One proposal per Nectar, ever: filing onto a Nectar that has one writes nothing.
    - No event payload or proposal column here ever holds the deposit's own text.

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the decision.
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.lowering for the rule, the state machine and the service that files
      and decides proposals.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from pydantic import TypeAdapter

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.honey_store.clearance import HUMAN_ONLY_ORIGINS, LabelApprover
from hivemind.honey_store.errors import (
    LoweringIneligibleError,
    LoweringNotFoundError,
    LoweringPostconditionError,
    NectarNotFoundError,
)
from hivemind.honey_store.lowering.models import (
    LOWERING_ID_PREFIX,
    LoweringDecision,
    LoweringFiling,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.rules import lowering_target
from hivemind.honey_store.lowering.state import LoweringState, assert_transition
from hivemind.honey_store.models import Nectar, NectarState
from hivemind.honey_store.store.protocol import LoweringEvents
from hivemind.honey_store.store.sqlite.nectar import _optional, _row_to_nectar, select_nectar
from hivemind.pheromone import HoneyEvent, insert_event
from waggle.clock import Clock
from waggle.ulid import RANDOMNESS_BYTES, encode_ulid

# No __all__: internal collaborator of hivemind.honey_store.store.sqlite.store only (see
# hivemind.honey_store.store.sqlite.nectar's identical note).

NO_LONGER_ELIGIBLE_NOTE = (  # The note on a proposal the apply transaction rejected unasked.
    "No longer eligible: its Nectar's label or labelling facts changed since it was filed."
)

# Reads and writes the verdict_reasons column: a JSON array of short strings (codingrules 9:
# pydantic is the only thing that reads JSON).
_REASONS = TypeAdapter(tuple[str, ...])
# One placeholder per origin only the human may lower; built from the rule's own constant, never
# caller input, so the f-string below carries no injection risk despite ruff's S608 pattern.
_ORIGIN_PLACEHOLDERS = ", ".join("?" for _ in HUMAN_ONLY_ORIGINS)
# lowering_target in SQL, clause for clause; a NULL fact (a row from before 0003) compares as
# unknown, so it never matches. Oldest first, like every other queue in the store.
_SELECT_CANDIDATES_SQL = f"""
SELECT * FROM honey_nectar AS n
WHERE n.state = ? AND n.tainted = 0 AND n.origin_tier <> ?
  AND n.origin NOT IN ({_ORIGIN_PLACEHOLDERS})
  AND n.floor_clearance_rank >= n.clearance_rank
  AND n.declared_clearance_rank < n.clearance_rank
  AND n.ripener_clearance_rank < n.clearance_rank
  AND NOT EXISTS (SELECT 1 FROM honey_lowerings AS l WHERE l.nectar_id = n.id)
ORDER BY n.received_at, n.id LIMIT ?
"""  # noqa: S608
_SELECT_REASON_SQL = "SELECT ripener_reason FROM honey_nectar WHERE id = ?"
_SELECT_FOR_NECTAR_SQL = "SELECT 1 FROM honey_lowerings WHERE nectar_id = ?"
_INSERT_SQL = (
    "INSERT INTO honey_lowerings (id, nectar_id, from_clearance, to_clearance, state, "
    "ripener_reason, proposed_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_BY_ID_SQL = "SELECT * FROM honey_lowerings WHERE id = ?"
_SELECT_BY_STATE_SQL = (
    "SELECT * FROM honey_lowerings WHERE state = ? ORDER BY proposed_at, id LIMIT ?"
)
# The judge's queue: a note on a PROPOSED proposal hands it to the human.
_SELECT_PENDING_SQL = (
    "SELECT * FROM honey_lowerings WHERE state = ? AND note = '' ORDER BY proposed_at, id LIMIT ?"
)
# Only a PROPOSED proposal is noted: one decided meanwhile keeps its record exactly as decided.
_NOTE_SQL = (
    "UPDATE honey_lowerings SET note = ?, attempts = attempts + ? WHERE id = ? AND state = ?"
)
_DECIDE_SQL = (
    "UPDATE honey_lowerings SET state = ?, approver = ?, verdict_reasons = ?, rubric_id = ?, "
    "human_reason = ?, note = ?, decided_at = ? WHERE id = ?"
)
_LOWER_NECTAR_SQL = "UPDATE honey_nectar SET clearance = ?, clearance_rank = ? WHERE id = ?"
# Only rows still at the old label: a row a human raised above it, or lowered below it, keeps its
# own label (ADR-0034: "every Honey row of it still at the old label").
_LOWER_HONEY_SQL = (
    "UPDATE honey SET clearance = ?, clearance_rank = ? WHERE nectar_id = ? AND clearance_rank = ?"
)
_READ_BACK_NECTAR_SQL = "SELECT clearance_rank FROM honey_nectar WHERE id = ?"
_READ_BACK_HONEY_SQL = "SELECT COUNT(*) AS n FROM honey WHERE nectar_id = ? AND clearance_rank = ?"


def select_lowering_candidates(connection: sqlite3.Connection, limit: int) -> tuple[Nectar, ...]:
    """Return eligible Nectar no proposal names yet, oldest first, at most `limit`."""
    origins = sorted(origin.value for origin in HUMAN_ONLY_ORIGINS)
    params = (NectarState.RIPENED.value, CombShieldLevel.NIGHT_VEIL.value, *origins, limit)
    rows = connection.execute(_SELECT_CANDIDATES_SQL, params).fetchall()
    return tuple(_row_to_nectar(row) for row in rows)


def add_lowering_transaction(
    connection: sqlite3.Connection, filing: LoweringFiling, events: LoweringEvents, clock: Clock
) -> LoweringProposal | None:
    """File one PROPOSED proposal and its events in one transaction; None if one already exists."""
    with transaction(connection):
        reason_row = connection.execute(_SELECT_REASON_SQL, (filing.nectar_id,)).fetchone()
        if reason_row is None:
            raise NectarNotFoundError(filing.nectar_id)
        # One proposal per Nectar, ever (ADR-0034): the judge is asked once, whatever the outcome.
        if connection.execute(_SELECT_FOR_NECTAR_SQL, (filing.nectar_id,)).fetchone() is not None:
            return None
        proposal = LoweringProposal(
            id=_new_lowering_id(clock),
            nectar_id=filing.nectar_id,
            from_label=filing.from_label,
            to_label=filing.to_label,
            state=LoweringState.PROPOSED,
            ripener_reason=reason_row["ripener_reason"] or "",
            proposed_at=clock.now(),
        )
        connection.execute(_INSERT_SQL, _insert_params(proposal))
        _record(connection, events(proposal))
        return proposal


def select_lowerings(
    connection: sqlite3.Connection, state: LoweringState, limit: int
) -> tuple[LoweringProposal, ...]:
    """Return proposals in `state`, oldest first, at most `limit`."""
    rows = connection.execute(_SELECT_BY_STATE_SQL, (state.value, limit)).fetchall()
    return tuple(_row_to_proposal(row) for row in rows)


def select_pending_lowerings(
    connection: sqlite3.Connection, limit: int
) -> tuple[LoweringProposal, ...]:
    """Return PROPOSED proposals with no note (the judge's queue), oldest first, at most `limit`."""
    rows = connection.execute(_SELECT_PENDING_SQL, (LoweringState.PROPOSED.value, limit))
    return tuple(_row_to_proposal(row) for row in rows.fetchall())


def select_lowering(connection: sqlite3.Connection, proposal_id: LoweringId) -> LoweringProposal:
    """Return one stored proposal, or raise LoweringNotFoundError."""
    row = connection.execute(_SELECT_BY_ID_SQL, (proposal_id,)).fetchone()
    if row is None:
        raise LoweringNotFoundError(proposal_id)
    return _row_to_proposal(row)


def note_lowering_transaction(
    connection: sqlite3.Connection, proposal_id: LoweringId, note: str, attempted: bool
) -> LoweringProposal:
    """Set a PROPOSED proposal's note, counting an attempt when asked; return it as stored."""
    with transaction(connection):
        params = (note, int(attempted), proposal_id, LoweringState.PROPOSED.value)
        connection.execute(_NOTE_SQL, params)
        return select_lowering(connection, proposal_id)


def apply_lowering_transaction(
    connection: sqlite3.Connection, decision: LoweringDecision, events: LoweringEvents
) -> LoweringProposal:
    """Lower the proposal's Nectar if its target still stands, else reject it; one transaction."""
    with transaction(connection):
        proposal = select_lowering(connection, decision.proposal_id)
        # The request itself must be an edge the table allows before anything is read or written:
        # a judge never lowers a REJECTED proposal, nobody moves a LOWERED one.
        assert_transition(proposal.state, LoweringState.LOWERED, decision.approver, proposal.id)
        nectar = select_nectar(connection, proposal.nectar_id)
        if _target_stands(nectar, proposal):
            decided = _lower(connection, proposal, decision)
        else:
            decided = _reject_ineligible(connection, proposal, decision)
        _record(connection, events(decided))
        return decided


def reject_lowering_transaction(
    connection: sqlite3.Connection, decision: LoweringDecision, events: LoweringEvents
) -> LoweringProposal:
    """Reject a PROPOSED proposal (a judge's REJECT, the human's denial) and record it; one txn."""
    with transaction(connection):
        proposal = select_lowering(connection, decision.proposal_id)
        decided = _decide(connection, proposal, LoweringState.REJECTED, decision, proposal.note)
        _record(connection, events(decided))
        return decided


def _target_stands(nectar: Nectar, proposal: LoweringProposal) -> bool:
    """Return whether the rule, run on the Nectar as it stands now, still says what was filed.

    A merge that raised the declared label, a raise, a taint or a changed reading since filing
    each make it say something else, and the label then stays where it is (ADR-0034).
    """
    same_label = nectar.clearance is proposal.from_label
    return same_label and lowering_target(nectar) is proposal.to_label


def _lower(
    connection: sqlite3.Connection, proposal: LoweringProposal, decision: LoweringDecision
) -> LoweringProposal:
    """Lower the Nectar and its rows still at the old label, check it landed, record LOWERED."""
    old, new = proposal.from_label, proposal.to_label
    connection.execute(_LOWER_NECTAR_SQL, (new.value, new.rank, proposal.nectar_id))
    connection.execute(_LOWER_HONEY_SQL, (new.value, new.rank, proposal.nectar_id, old.rank))
    _check_lowered(connection, proposal)
    return _decide(connection, proposal, LoweringState.LOWERED, decision, proposal.note)


def _check_lowered(connection: sqlite3.Connection, proposal: LoweringProposal) -> None:
    """Read the rows back inside the transaction: the postcondition ADR-0034 names.

    Raises:
        LoweringPostconditionError: The Nectar is not at the target, or a Honey row of it is
            still at the old label; raised inside the transaction, so nothing commits.
    """
    nectar_rank = connection.execute(_READ_BACK_NECTAR_SQL, (proposal.nectar_id,)).fetchone()
    left = connection.execute(
        _READ_BACK_HONEY_SQL, (proposal.nectar_id, proposal.from_label.rank)
    ).fetchone()
    if nectar_rank["clearance_rank"] != proposal.to_label.rank or left["n"]:
        raise LoweringPostconditionError(proposal.id)


def _reject_ineligible(
    connection: sqlite3.Connection, proposal: LoweringProposal, decision: LoweringDecision
) -> LoweringProposal:
    """Reject a proposal whose target no longer stands, or refuse when it is already REJECTED."""
    # A REJECTED proposal the human approved has no edge to record the loss on: refuse instead,
    # and write nothing.
    if proposal.state is not LoweringState.PROPOSED:
        raise LoweringIneligibleError(proposal.id)
    return _decide(connection, proposal, LoweringState.REJECTED, decision, NO_LONGER_ELIGIBLE_NOTE)


def _decide(
    connection: sqlite3.Connection,
    proposal: LoweringProposal,
    state: LoweringState,
    decision: LoweringDecision,
    note: str,
) -> LoweringProposal:
    """Move `proposal` to `state` for `decision`'s approver, after the table allows the edge."""
    assert_transition(proposal.state, state, decision.approver, proposal.id)
    verdict = decision.verdict
    # A judge's verdict replaces the verdict fields; the human's decision keeps them, so a human
    # overturning a rejection still shows what the judge said.
    decided = proposal.model_copy(
        update={
            "state": state,
            "approver": decision.approver,
            "verdict_reasons": verdict.reasons if verdict is not None else proposal.verdict_reasons,
            "rubric_id": verdict.rubric_id if verdict is not None else proposal.rubric_id,
            "human_reason": decision.human_reason or proposal.human_reason,
            "note": note,
            "decided_at": decision.decided_at,
        }
    )
    connection.execute(_DECIDE_SQL, _decide_params(decided))
    return decided


def _decide_params(decided: LoweringProposal) -> tuple[object, ...]:
    """Build `_DECIDE_SQL`'s parameters from the decided proposal, in its declared order."""
    approver = decided.approver.value if decided.approver is not None else None
    decided_at = decided.decided_at.isoformat() if decided.decided_at is not None else None
    return (
        decided.state.value,
        approver,
        _REASONS.dump_json(decided.verdict_reasons).decode(),
        decided.rubric_id,
        decided.human_reason,
        decided.note,
        decided_at,
        decided.id,
    )


def _record(connection: sqlite3.Connection, events: Sequence[HoneyEvent]) -> None:
    """Insert each event the caller built from the outcome, inside the open transaction."""
    for event in events:
        insert_event(connection, event)


def _insert_params(proposal: LoweringProposal) -> tuple[object, ...]:
    """Build `_INSERT_SQL`'s parameters for a freshly filed proposal, in its declared order."""
    return (
        proposal.id,
        proposal.nectar_id,
        proposal.from_label.value,
        proposal.to_label.value,
        proposal.state.value,
        proposal.ripener_reason,
        proposal.proposed_at.isoformat(),
    )


def _row_to_proposal(row: sqlite3.Row) -> LoweringProposal:
    """Decode one `honey_lowerings` row back into a LoweringProposal."""
    return LoweringProposal(
        id=LoweringId(row["id"]),
        nectar_id=row["nectar_id"],
        from_label=HoneyClearance(row["from_clearance"]),
        to_label=HoneyClearance(row["to_clearance"]),
        state=LoweringState(row["state"]),
        approver=_optional(LabelApprover, row["approver"]),
        ripener_reason=row["ripener_reason"],
        verdict_reasons=_REASONS.validate_json(row["verdict_reasons"]),
        rubric_id=row["rubric_id"],
        human_reason=row["human_reason"],
        attempts=row["attempts"],
        note=row["note"],
        proposed_at=datetime.fromisoformat(row["proposed_at"]),
        decided_at=_optional(datetime.fromisoformat, row["decided_at"]),
    )


def _new_lowering_id(clock: Clock) -> LoweringId:
    """Mint a fresh `lowering_`-prefixed ULID, the way the store mints its note proposals' ids."""
    timestamp_ms = int(clock.now().timestamp() * 1000)
    randomness = secrets.token_bytes(RANDOMNESS_BYTES)
    return LoweringId(f"{LOWERING_ID_PREFIX}{encode_ulid(timestamp_ms, randomness)}")
