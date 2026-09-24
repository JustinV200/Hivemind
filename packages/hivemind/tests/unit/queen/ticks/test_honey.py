"""Tests for hivemind.queen.ticks.honey: Nectar deposits over Waggle, and the dispatch hook.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/honey.py (codingrules section 3); the query half is split by
    feature (14.2) into test_honey_query.py. Every test drives the real handler against a real
    SQLite Honey Store, reading the Queen's replies off the Warden's own raw end of the link.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.honey for the module under test.
    - builders.honey_wire for the access bundle, the raw link end and the deposit metadata.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.honey import open_test_honey_store
from builders.honey_wire import (
    WireEnd,
    make_deposit_meta,
    make_honey_access,
    make_honey_link,
    make_honey_query,
)
from builders.queen import make_queen_deps
from builders.supervision import make_inbox_item

from hivemind.cell import Cell, CellKind, CombShieldLevel, HoneyClearance
from hivemind.honey_store import (
    DepositSource,
    HoneyStoreError,
    IntakeResult,
    NectarIntake,
    NectarState,
    SqliteHoneyStore,
)
from hivemind.manifest import HoneyStoreSection
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.inbox import to_inbox_item
from hivemind.queen.ticks.honey import HONEY_UNAVAILABLE_CODE, handle_honey_item
from hivemind.queen.ticks.liveness import handle_infrastructure_item
from hivemind.supervision.attendant import InboxItem
from hivemind.workers.nectar import split_deposit
from waggle.clock import FakeClock
from waggle.ids import WardenId
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages.base import MAX_CHUNK_BYTES, WaggleMessage
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.honey import HoneyResponse, NectarDeposit

_FINDING = b"The widget factory's staging config lives at /etc/widgets/staging.toml."


@dataclass(frozen=True, slots=True)
class _Rig:
    """One Queen with a real Honey Store and one attached Warden link read by hand."""

    deps: QueenDeps
    link: WardenLink
    warden: WireEnd
    store: SqliteHoneyStore

    @property
    def wardens(self) -> dict[WardenId, WardenLink]:
        return {self.link.warden_id: self.link}

    def item(self, payload: WaggleMessage) -> InboxItem:
        """Wrap `payload` as the Warden sends it, and as the Queen's inbox would receive it."""
        return to_inbox_item(self.warden.envelope_for(payload), self.link.warden_id)


async def _rig(tmp_path: Path, cell: Cell | None = None, *, with_honey: bool = True) -> _Rig:
    clock = FakeClock()
    store = await open_test_honey_store(tmp_path, clock)
    active = cell if cell is not None else make_cell(kind=CellKind.VIRTUAL, clock=clock)
    access = make_honey_access(store, clock) if with_honey else None
    deps, _link, _end = make_queen_deps(clock, cell=active, honey=access)
    link, warden = make_honey_link(active, clock, deps.identity.hive_id, deps.identity.node_id)
    return _Rig(deps=deps, link=link, warden=warden, store=store)


def _chunks(rig: _Rig, content: bytes = _FINDING, **meta: object) -> tuple[NectarDeposit, ...]:
    """Cut `content` into chunks from a Worker on the rig's own Cell."""
    fields: dict[str, object] = {"cell_id": rig.link.cell.id, **meta}
    return split_deposit(content, make_deposit_meta(rig.deps.clock, **fields))


async def _assert_nothing_sent(rig: _Rig) -> None:
    """Close the Queen's end: anything she sent is still delivered first, then the stream ends."""
    await rig.link.transport.close()
    with pytest.raises(StopAsyncIteration):
        await rig.warden.next()


async def test_handle_honey_item_leaves_any_other_payload_to_the_ordinary_dispatch(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path)

    handled = await handle_honey_item(rig.deps, rig.wardens, make_inbox_item(payload=None))

    assert handled is False


async def test_a_single_chunk_deposit_is_stored_with_the_tier_the_queen_records(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path)
    # The sender claims NIGHT_VEIL; the Queen's record of the Cell (MEADOW) is what counts.
    (chunk,) = _chunks(rig, origin_tier=WireCombShieldLevel.NIGHT_VEIL)

    handled = await handle_honey_item(rig.deps, rig.wardens, rig.item(chunk))

    assert handled is True
    (nectar,) = await rig.store.pending_nectar(10)
    assert nectar.sha256 == hashlib.sha256(_FINDING).hexdigest()
    assert nectar.origin_tier is CombShieldLevel.MEADOW
    assert nectar.state is NectarState.RECEIVED
    assert nectar.clearance is HoneyClearance.C1
    await _assert_nothing_sent(rig)


async def test_a_borrowed_cells_deposit_is_labelled_c2_whatever_it_declared(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, make_cell(kind=CellKind.REAL))
    (chunk,) = _chunks(rig)

    await handle_honey_item(rig.deps, rig.wardens, rig.item(chunk))

    (nectar,) = await rig.store.pending_nectar(10)
    assert nectar.clearance is HoneyClearance.C2


async def test_a_two_chunk_deposit_is_stored_once_its_final_chunk_arrives(tmp_path: Path) -> None:
    rig = await _rig(tmp_path)
    content = b"w" * (MAX_CHUNK_BYTES + 100)
    first, last = _chunks(rig, content)

    await handle_honey_item(rig.deps, rig.wardens, rig.item(first))
    assert await rig.store.pending_nectar(10) == ()
    await handle_honey_item(rig.deps, rig.wardens, rig.item(last))

    (nectar,) = await rig.store.pending_nectar(10)
    assert await rig.store.nectar_content(nectar.id) == content
    assert nectar.size_bytes == len(content)


async def test_a_refused_chunk_is_answered_with_its_stable_code_correlated_to_it(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path)
    # Another Cell's deposit: the relaying Warden may only relay its own Cell's Nectar.
    (chunk,) = _chunks(rig, cell_id=make_cell(clock=rig.deps.clock).id)
    item = rig.item(chunk)

    await handle_honey_item(rig.deps, rig.wardens, item)

    reply = await rig.warden.next()
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == "hivemind.honey_store.cell_mismatch"
    assert reply.payload.failed_kind == "honey.nectar_deposit"
    assert reply.payload.is_retryable is False
    assert reply.correlation_id == item.id
    assert await rig.store.pending_nectar(10) == ()


async def test_a_deposit_with_no_honey_store_is_refused_as_unavailable(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, with_honey=False)
    (chunk,) = _chunks(rig)
    item = rig.item(chunk)

    await handle_honey_item(rig.deps, rig.wardens, item)

    reply = await rig.warden.next()
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == HONEY_UNAVAILABLE_CODE
    assert reply.correlation_id == item.id


class _FailingIntake(NectarIntake):
    """An intake whose store fails under it: the one non-refusal error a chunk can meet."""

    async def receive_chunk(
        self, deposit: NectarDeposit, source: DepositSource
    ) -> IntakeResult | None:
        raise HoneyStoreError(f"The store failed taking in a chunk from {source.cell_id}.")


async def test_a_store_failure_during_intake_is_answered_never_raised(tmp_path: Path) -> None:
    rig = await _rig(tmp_path)
    assert rig.deps.honey is not None
    identity = rig.deps.honey.identity
    failing = _FailingIntake(
        rig.store, identity, rig.deps.clock, HoneyStoreSection(), HoneyClearance.C1
    )
    deps = _with_intake(rig.deps, failing)
    (chunk,) = _chunks(rig)

    await handle_honey_item(deps, rig.wardens, rig.item(chunk))

    reply = await rig.warden.next()
    assert isinstance(reply.payload, ErrorMessage)
    assert reply.payload.code == HoneyStoreError.code


async def test_an_item_from_a_warden_no_longer_attached_is_handled_and_dropped(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path)

    handled = await handle_honey_item(rig.deps, {}, rig.item(make_honey_query(rig.deps.clock)))

    assert handled is True
    await _assert_nothing_sent(rig)


async def test_a_reply_to_a_warden_whose_link_closed_is_dropped_never_raised(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path)
    item = rig.item(make_honey_query(rig.deps.clock, requester=rig.link.warden_id, task_id=None))
    await rig.warden.transport.close()  # The Warden detaches after its query arrived.

    handled = await handle_honey_item(rig.deps, rig.wardens, item)  # must not raise

    assert handled is True


async def test_the_infrastructure_dispatch_answers_a_honey_query_ahead_of_autopilot(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path)
    item = rig.item(make_honey_query(rig.deps.clock, requester=rig.link.warden_id, task_id=None))

    handled = await handle_infrastructure_item(rig.deps, rig.wardens, item, {}, {})

    reply = await rig.warden.next()
    assert handled is True
    assert isinstance(reply.payload, HoneyResponse)
    assert reply.correlation_id == item.id


def _with_intake(deps: QueenDeps, intake: NectarIntake) -> QueenDeps:
    """Return `deps` whose Honey Store takes chunks in through `intake`."""
    assert deps.honey is not None
    return dataclasses.replace(deps, honey=dataclasses.replace(deps.honey, intake=intake))
