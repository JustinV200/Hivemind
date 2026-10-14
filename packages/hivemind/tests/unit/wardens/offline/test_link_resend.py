"""Tests for hivemind.wardens.offline.link: the frames a link nobody knew was dead swallowed.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/link.py (codingrules section 3), split by feature (14.2)
    from test_link.py. A cable pulled out from under a device leaves the connection looking alive
    until the keepalive gives up, and every frame sent in that time returns as sent and is lost.
    Over a scripted Queen that can pull the cable the same way (`ScriptedQueen.silence`, then
    `drop` for the keepalive giving up), these tests state what the link then does: it sends the
    frames that must arrive again after the reconnection, in their order and under their ids, before
    its queue; it sends nothing a goodbye or a stale stretch makes pointless; it keeps its memory
    within bounds; and it remembers what it sends again, so a second silent death is covered too.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.link and .policy (`must_arrive`) for the modules under test.
    - builders.offline for the scripted Queen and the rig that drives the link.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.offline import LinkRig

from hivemind.wardens.offline import LinkPhase
from waggle.envelope import Envelope
from waggle.messages.task import TaskStage
from waggle.transport.recent import RESEND_HORIZON_S, UNNOTICED_S, RecentFrames, RecentLimits


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[LinkRig]:
    """A link over a scripted Queen, already pumping; closed and reaped after the test."""
    started = LinkRig(tmp_path)
    started.start()
    yield started
    await started.stop()


def _ids(envelopes: list[Envelope]) -> list[str]:
    """The envelope ids of `envelopes`, in order."""
    return [str(envelope.id) for envelope in envelopes]


async def _online(rig: LinkRig) -> None:
    """Wait for the first connection to be up and announced."""
    await rig.until(lambda: rig.link.is_online and "cell.heartbeat" in rig.queen.kinds)


async def _send_into_the_dark(rig: LinkRig, *envelopes: Envelope) -> None:
    """Pull the cable without a sound, then send: each send returns, and nobody hears a frame."""
    rig.queen.silence()
    for envelope in envelopes:
        await rig.link.send(envelope)  # Nothing is raised, queued or noticed.
    await rig.until(lambda: len(rig.queen.swallowed) == len(envelopes))
    assert rig.link.is_online and rig.outbox.queued() == 0


async def _keepalive_gives_up(rig: LinkRig) -> None:
    """The link is found dead, forty seconds on, and the Queen cannot be reached yet."""
    rig.queen.block_dials()
    rig.queen.drop()
    await rig.until(lambda: not rig.link.is_online)


def _after_the_second_announcement(rig: LinkRig) -> list[Envelope]:
    """Everything the Queen heard after the Warden announced itself on the second connection."""
    marks = [i for i, kind in enumerate(rig.queen.kinds) if kind == "cell.heartbeat"]
    return rig.queen.received[marks[1] + 1 :]


async def test_frames_sent_into_a_dead_link_come_again_in_order_before_the_queue(
    rig: LinkRig,
) -> None:
    await _online(rig)
    wire = rig.wire
    result, alarm, checkpoint = wire.result(), wire.alarm(), wire.progress()

    await _send_into_the_dark(rig, result, alarm, checkpoint)
    await _keepalive_gives_up(rig)
    request = wire.forage_request()
    await rig.link.send(request)  # Known down now: queued, and sent after what was swallowed.
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and len(_after_the_second_announcement(rig)) == 4)

    heard = _after_the_second_announcement(rig)
    # The very envelopes the Queen never heard, in their order, and only then the queue's.
    assert _ids(heard[:3]) == _ids([result, alarm, checkpoint]) == _ids(rig.queen.swallowed)
    assert heard[3].kind == "forage.request" and heard[3].payload == request.payload
    assert rig.phases[-2:] == [LinkPhase.RECOVERING, LinkPhase.ONLINE]


async def test_only_the_frames_the_table_says_must_arrive_are_sent_again(rig: LinkRig) -> None:
    await _online(rig)
    wire = rig.wire
    result, question, nectar = wire.result(), wire.question(), wire.nectar()
    heartbeat, working = wire.heartbeat(), wire.progress(TaskStage.WORKING)
    grow, chunk = wire.cell_request(), wire.trail_segment()

    # Everything went out as it would live. A heartbeat, a status, a request to grow and a trail
    # chunk are not worth sending again; a result, a question and a chunk of Nectar are.
    await _send_into_the_dark(rig, result, heartbeat, question, working, grow, nectar, chunk)
    await _keepalive_gives_up(rig)
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and len(_after_the_second_announcement(rig)) == 3)

    assert _ids(_after_the_second_announcement(rig)) == _ids([result, question, nectar])


async def test_a_request_is_sent_again_under_its_own_id_not_in_a_fresh_envelope(
    rig: LinkRig,
) -> None:
    await _online(rig)
    request, question = rig.wire.forage_request(), rig.wire.question()

    await _send_into_the_dark(rig, request, question)
    await _keepalive_gives_up(rig)
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and len(_after_the_second_announcement(rig)) == 2)

    # The queue sends a request fresh because it was never heard; these may have been, and an
    # answer to a copy the window dropped would never come, so the id is the one the Queen knows.
    assert _ids(_after_the_second_announcement(rig)) == _ids([request, question])


async def test_what_had_arrived_is_sent_again_too_because_the_sender_cannot_tell(
    rig: LinkRig,
) -> None:
    await _online(rig)
    result = rig.wire.result()
    await rig.link.send(result)
    await rig.until(lambda: result.id in _ids(rig.queen.received))

    rig.queen.drop()  # Found dead; this frame had in fact been heard.
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 2)
    await rig.until(lambda: _ids(rig.queen.received).count(str(result.id)) == 2)

    # The Queen's replay window drops the second copy; this side cannot know to leave it out.
    assert _ids(_after_the_second_announcement(rig)) == [str(result.id)]


async def test_a_queen_that_said_goodbye_is_owed_nothing_again(rig: LinkRig) -> None:
    await _online(rig)
    result = rig.wire.result()
    await rig.link.send(result)
    await rig.until(lambda: result.id in _ids(rig.queen.received))

    await rig.queen.close_current()  # A close frame: she read everything before it.
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 2)

    assert _ids(rig.queen.received).count(str(result.id)) == 1
    assert _after_the_second_announcement(rig) == []


async def test_a_stretch_older_than_the_keepalive_could_hide_is_not_sent_again(
    rig: LinkRig,
) -> None:
    await _online(rig)
    old, recent = rig.wire.result(), rig.wire.alarm()
    await rig.link.send(old)
    rig.clock.advance(UNNOTICED_S + 1.0)  # The link was alive and quiet for longer than that.
    await rig.link.send(recent)
    await rig.until(lambda: recent.id in _ids(rig.queen.received))

    rig.queen.drop()
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 2)

    # `old` was sent so long before the loss that a keepalive could not have missed it.
    assert _ids(rig.queen.received).count(str(old.id)) == 1
    assert _ids(_after_the_second_announcement(rig)) == [str(recent.id)]


async def test_a_stretch_the_queens_window_has_forgotten_is_not_sent_again(rig: LinkRig) -> None:
    await _online(rig)
    result = rig.wire.result()
    await _send_into_the_dark(rig, result)
    await _keepalive_gives_up(rig)

    # The outage outlasts the window she remembers a frame for: sent now, one she did hear would
    # be handled twice, so the link lets it go.
    rig.clock.advance(RESEND_HORIZON_S + 1.0)
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 2)

    assert _after_the_second_announcement(rig) == []


async def test_the_memory_is_bounded_and_the_oldest_frames_go_first(tmp_path: Path) -> None:
    rig = LinkRig(tmp_path, recent=RecentFrames(RecentLimits(max_frames=2)))
    rig.start()
    await _online(rig)
    first, second, third = rig.wire.result(), rig.wire.alarm(), rig.wire.nectar()

    await _send_into_the_dark(rig, first, second, third)
    await _keepalive_gives_up(rig)
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and len(_after_the_second_announcement(rig)) == 2)

    assert _ids(_after_the_second_announcement(rig)) == _ids([second, third])
    await rig.stop()


async def test_a_frame_sent_again_is_remembered_for_a_second_silent_death(rig: LinkRig) -> None:
    await _online(rig)
    result = rig.wire.result()
    await _send_into_the_dark(rig, result)
    await _keepalive_gives_up(rig)
    rig.queen.silence()  # The new connection is no better: it takes the re-send and hears none.
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 2)
    await rig.until(lambda: len(rig.queen.swallowed) == 3)  # The first, the announcement, again.
    assert _ids(rig.queen.received).count(str(result.id)) == 0

    rig.queen.drop()  # Found dead once more.
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 3)
    await rig.until(lambda: result.id in _ids(rig.queen.received))

    assert _ids(rig.queen.received).count(str(result.id)) == 1


async def test_a_connection_that_dies_while_the_frames_go_out_gets_them_all_on_the_next(
    rig: LinkRig,
) -> None:
    await _online(rig)
    first, second = rig.wire.result(), rig.wire.alarm()
    await _send_into_the_dark(rig, first, second)
    rig.after.fail_times = 1  # The next connection dies once its re-send is out, mid-sequence.
    await _keepalive_gives_up(rig)

    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 3)

    # The abandoned connection heard both; the one that stuck is sent them again, in order, so
    # nothing a half-finished recovery lost is lost for good (the Queen's window drops a copy).
    heard = _ids(rig.queen.received)
    assert heard.count(str(first.id)) == 2 and heard.count(str(second.id)) == 2
    assert heard[-2:] == _ids([first, second])


async def test_what_the_queue_replays_is_remembered_for_the_next_loss(rig: LinkRig) -> None:
    await _online(rig)
    queued = rig.wire.result()
    await _keepalive_gives_up(rig)
    await rig.link.send(queued)  # Known down: queued, and replayed on the way back.
    rig.queen.release_dials()
    await rig.until(lambda: rig.link.is_online and queued.id in _ids(rig.queen.received))

    # The replay went into a connection that may be dead too; this time it is.
    rig.queen.silence()
    rig.queen.drop()
    await rig.until(lambda: rig.link.is_online and rig.queen.dials == 3)

    assert _ids(rig.queen.received).count(str(queued.id)) == 2
