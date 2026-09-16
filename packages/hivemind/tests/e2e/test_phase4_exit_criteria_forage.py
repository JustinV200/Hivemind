"""End-to-end: roadmap phase 4's exit criteria -- forage requests, seats/liveness and throttle.

`.claude/roadmap.md` phase 4 exit criteria, split out of `test_phase4_exit_criteria.py` once that
module reached the test-file LOC cap: a Warden asking for more sub-bees than the Hive Stand can
bear is denied with a reason; the same request under headroom is granted by autopilot with no
awake episode; six Drones on a two-seat provider never have more than two calls in flight, ordered
by tempo; a Warden whose heartbeat stops has its grant back in the pool after expiry; a 429 with a
retry-after zeroes a source's headroom until the window passes, with the fallback absorbing the
work; a provider that publishes no limits keeps `None` on both rate fields throughout.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 4 exit criteria, verbatim, for every bullet this module proves.
    - hivemind.queen.ticks.forage for handle_forage_request, bullets 1-2's own dispatch point.
    - hivemind.queen.ticks.liveness for check_liveness, bullet 4's own grant-expiry sweep.
    - hivemind.llm.fanner.lane for FannerLane, bullets 3, 5 and 6's own seat/throttle mechanics.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from builders.forage import make_capacity, make_grant, make_source
from builders.llm import make_bound, make_request
from builders.queen import make_queen_deps

from hivemind.forage.grant_state import GrantState
from hivemind.forage.map import ForageMap
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.errors import RateLimitedError
from hivemind.llm.fake import FakeLLMProvider, text_response
from hivemind.llm.fanner import Fanner, FannerDeps, NullLlmEventRecorder
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.forage.ledger import ForageLedger, LedgerRecorder
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.ticks.forage import handle_forage_request
from hivemind.queen.ticks.liveness import WardenLiveness, check_liveness
from waggle.clock import FakeClock
from waggle.ids import WardenId, new_message_id
from waggle.messages.forage import ForageReply, ForageRequest
from waggle.messages.forage.values import ForageDelta, ForageOutcome, ForageRequestKind
from waggle.messages.labels import AccuracyBar as WireAccuracyBar
from waggle.messages.labels import Tempo as WireTempo

pytestmark = pytest.mark.e2e

_EMPTY_DELTA = ForageDelta(
    seats=0, source_id=None, spend=0.0, tokens=0, sub_bees=0, slot=None, minimum_grade=None
)
_WIRE_TEMPO = WireTempo(latency_budget_s=None, accuracy=WireAccuracyBar.NORMAL)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Forage requests: denied with a reason; granted by autopilot with no awake episode
# ──────────────────────────────────────────────────────────────────────────────


def _sub_bee_request(grant_id: str, wanted: int, reason: str) -> ForageRequest:
    """Build one SUB_BEES ForageRequest wire message."""
    return ForageRequest(
        grant_id=grant_id,
        kind=ForageRequestKind.SUB_BEES,
        wanted=_EMPTY_DELTA.model_copy(update={"sub_bees": wanted}),
        task_id=None,
        tempo=_WIRE_TEMPO,
        reason=reason,
    )


async def test_a_warden_asking_for_more_sub_bees_than_the_hive_stand_can_bear_is_denied_with_a_reason() -> (  # noqa: E501
    None
):
    """No headroom, nothing to shrink: an outright DENY, with a reason on the trail."""
    deps, link, warden_end = make_queen_deps()
    await deps.ledger.report_capacity(link.cell.id, make_capacity(max_sub_bees=2))
    grant = make_grant(clock=deps.clock, holder=link.warden_id, max_sub_bees=0)
    await deps.ledger.record_grant(grant)
    wire_request = _sub_bee_request(grant.id, 999, "need many more bees than exist")

    await handle_forage_request(
        deps, {link.warden_id: link}, link, wire_request, new_message_id(deps.clock)
    )

    reply: ForageReply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    assert reply.reason  # The wire reply always carries one.

    denied = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "forage.denied"]
    assert len(denied) == 1
    assert denied[0].payload.get("reason")  # roadmap 4.7's own exit bar: a reason on the trail.


async def test_the_same_request_under_headroom_is_granted_by_autopilot_with_no_awake_episode() -> (
    None
):
    """Within headroom: GRANT at once, with no awake episode and no model call in between."""
    provider = FakeLLMProvider(capabilities=None)
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    await deps.ledger.report_capacity(link.cell.id, make_capacity(max_sub_bees=8))
    grant = make_grant(clock=deps.clock, holder=link.warden_id, max_sub_bees=0)
    await deps.ledger.record_grant(grant)
    wire_request = _sub_bee_request(grant.id, 1, "need one more bee")

    await handle_forage_request(
        deps, {link.warden_id: link}, link, wire_request, new_message_id(deps.clock)
    )

    reply: ForageReply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.GRANTED
    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    assert "forage.granted" in kinds
    assert "queen.awake" not in kinds  # Autopilot only: no awake episode fired.
    assert "memory.episode" not in kinds
    assert provider.calls == []  # No fake-provider call happened between request and grant.


# ──────────────────────────────────────────────────────────────────────────────
# 4. Seats: two seats, six Drones, tempo order; a stopped heartbeat frees its grant
# ──────────────────────────────────────────────────────────────────────────────

_SIX_TEMPOS = (
    AccuracyBar.NORMAL,
    AccuracyBar.NORMAL,
    AccuracyBar.LOW,
    AccuracyBar.HIGH,
    AccuracyBar.CRITICAL,
    AccuracyBar.LOW,
)
# The two seats free at once go to whichever two Drones arrive first (d0, d1); the remaining four
# queue and are admitted strictly by tempo priority (CRITICAL, HIGH, NORMAL, LOW; ties by arrival):
# d4 (CRITICAL), d3 (HIGH), d2 (LOW, arrived before d5), d5 (LOW).
_EXPECTED_ORDER = ("d0", "d1", "d4", "d3", "d2", "d5")


class _BlockingProvider:
    """A provider whose `complete` blocks on an `asyncio.Event` until the test releases it.

    Mirrors `tests.unit.llm.fanner.test_lane_seats`'s own fixture of the same purpose: lets a
    test hold every call open at once, so a burst of six really does overlap.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []  # Each call's own label, in the order the provider saw them.
        self._released = asyncio.Event()

    @property
    def name(self) -> str:
        return "fake"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        label = request.messages[0].parts[0].text  # type: ignore[union-attr]
        self.calls.append(label)
        await self._released.wait()
        return text_response(f"done: {label}")

    def release(self) -> None:
        """Unblock every call currently waiting (and every future one) on this provider."""
        self._released.set()


def _labelled_request(label: str) -> LLMRequest:
    """Build a WORKER-slot LLMRequest whose own text is `label`, so a call is identifiable."""
    from hivemind.llm.models import Message, Role

    return make_request(messages=(Message.text(Role.USER, label),))


async def test_six_drones_on_a_two_seat_provider_never_have_more_than_two_calls_in_flight() -> None:
    """Two seats cap concurrency at two; the Fanner's own queue admits the rest by tempo."""
    clock = FakeClock()
    ledger = ForageLedger()
    await ledger.seats.set_capacity("src_1", 2)
    source = make_source(source_id="src_1", provider="fake", model="test-model", seats=6)
    fanner = Fanner(
        FannerDeps(
            map=ForageMap([source], clock=clock),
            seats={"fake": 2},
            limits={},
            clock=clock,
            recorder=LedgerRecorder(ledger),
        )
    )
    provider = _BlockingProvider()
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lanes = [fanner.lane(Tempo(accuracy=bar)) for bar in _SIX_TEMPOS]

    tasks = [
        asyncio.ensure_future(lane.complete(bound, _labelled_request(f"d{i}")))
        for i, lane in enumerate(lanes)
    ]
    await asyncio.sleep(0)  # Let every task queue; only 2 can hold a seat and reach the provider.

    assert len(provider.calls) == 2  # At most two model calls in flight at any moment.
    assert ledger.seats.in_use("src_1") == 2  # The Forage view: seats at 2 of 2.
    assert ledger.seats.free("src_1") == 0

    provider.release()
    await asyncio.gather(*tasks)

    assert provider.calls == list(_EXPECTED_ORDER)  # The Fanner's queue ordered them by tempo.
    assert ledger.seats.in_use("src_1") == 0  # Every seat released once every call finished.


async def test_a_warden_whose_heartbeat_stops_has_its_grant_back_in_the_pool_after_expiry() -> None:
    """Advance the clock past grant_ttl_s with no renewing heartbeat: the sweep frees it."""
    clock = FakeClock()
    deps, link, _warden_end = make_queen_deps(clock=clock)
    await deps.ledger.report_capacity(link.cell.id, make_capacity(max_sub_bees=4))
    grant = make_grant(
        state=GrantState.ACTIVE,  # revoke() only ever moves ACTIVE -> REVOKED (Appendix C).
        clock=clock,
        holder=link.warden_id,
        max_sub_bees=2,
        expires_at=clock.now() + timedelta(seconds=deps.grant_ttl_s),
    )
    await deps.ledger.record_grant(grant)
    headroom_before = deps.ledger.headroom().sub_bees

    clock.advance(deps.grant_ttl_s + 1.0)  # Past expiry; no Heartbeat ever renewed it.
    liveness: dict[WardenId, WardenLiveness] = {}
    await check_liveness(deps, (link,), liveness, HumanInbox())

    assert deps.ledger.grant(grant.id) is None  # The grant is gone: back in the pool.
    assert deps.ledger.headroom().sub_bees == headroom_before + 2  # Headroom restored.
    revoked = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "forage.revoked"]
    assert len(revoked) == 1
    assert revoked[0].payload.get("cause") == "EXPIRED"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Throttle: a 429 with retry-after zeroes headroom; an unmetered provider stays None
# ──────────────────────────────────────────────────────────────────────────────

_RETRY_AFTER_S = 30.0


async def test_a_429_with_retry_after_zeroes_the_sources_headroom_until_the_window_passes() -> None:
    """A 429+retry-after masks the source, spills to the fallback, then lifts after the window."""
    clock = FakeClock()
    hosted = FakeLLMProvider(name="hosted")
    hosted.script(RateLimitedError("hosted", retry_after_s=_RETRY_AFTER_S))
    fallback_provider = FakeLLMProvider(name="fallback")
    fallback_provider.script(text_response("ok"), text_response("ok again"))
    fallback_bound = make_bound(binding="fallback", provider=fallback_provider, model="test-model")
    bound = make_bound(
        binding="hosted", provider=hosted, model="test-model", fallback=fallback_bound
    )
    source = make_source(source_id="hosted-src", provider="hosted", model="test-model", seats=1)
    fmap = ForageMap([source], clock=clock)
    fanner = Fanner(
        FannerDeps(
            map=fmap,
            seats={"hosted": 1, "fallback": 1},
            limits={},
            clock=clock,
            recorder=NullLlmEventRecorder(),
        )
    )
    lane = fanner.lane(Tempo(accuracy=AccuracyBar.NORMAL))

    response = await lane.complete(bound, make_request())

    assert response.text == "ok"  # The next source in the chain absorbed the work.
    assert len(hosted.calls) == 1  # Nothing else was ever routed to the throttled source.
    assert fmap.get("hosted-src").abundance.seats_free == 0  # Headroom at zero on the map.
    assert fmap.get("hosted-src").abundance.throttled_until is not None

    # A second call, while still throttled: still nothing reaches the hosted provider again.
    await lane.complete(bound, make_request())
    assert len(hosted.calls) == 1

    clock.advance(_RETRY_AFTER_S + 1.0)  # Past the window.
    assert fmap.get("hosted-src").abundance.seats_free == source.spec.seats  # Selectable again.
    assert fmap.get("hosted-src").abundance.throttled_until is None


async def test_a_provider_that_publishes_no_limits_keeps_none_on_both_rate_fields_throughout() -> (
    None
):
    """No `rate_limit` on the response: both rate fields stay `None`, before and after calls."""
    clock = FakeClock()
    provider = FakeLLMProvider(name="unmetered")
    provider.script(text_response("ok"), text_response("ok again"))
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    source = make_source(source_id="src", provider="unmetered", model="test-model", seats=2)
    fmap = ForageMap([source], clock=clock)
    fanner = Fanner(
        FannerDeps(
            map=fmap,
            seats={"unmetered": 2},
            limits={},
            clock=clock,
            recorder=NullLlmEventRecorder(),
        )
    )
    lane = fanner.lane(Tempo(accuracy=AccuracyBar.NORMAL))

    before = fmap.get("src").abundance
    assert before.requests_per_minute_left is None
    assert before.tokens_per_minute_left is None

    await lane.complete(bound, make_request())
    await lane.complete(bound, make_request())

    after = fmap.get("src").abundance
    assert after.requests_per_minute_left is None
    assert after.tokens_per_minute_left is None
