"""Tests for hivemind.queen.cluster.health: HealthPoller and next_probe_at's backoff schedule.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/health.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.health for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from hivemind.llm import FakeLLMProvider, HealthState
from hivemind.queen.cluster.health import (
    DEFAULT_HEALTHY_PROBE_INTERVAL_S,
    ClusterBackoff,
    HealthPoller,
    next_probe_at,
)
from waggle.clock import FakeClock


def test_next_probe_at_is_now_for_the_first_probe() -> None:
    clock = FakeClock()
    now = clock.now()

    assert next_probe_at(0, now) == now


def test_next_probe_at_grows_the_gap_exponentially() -> None:
    clock = FakeClock()
    now = clock.now()
    backoff = ClusterBackoff(initial_s=5.0, max_s=300.0, factor=2.0)

    first = next_probe_at(1, now, backoff)
    second = next_probe_at(2, now, backoff)
    third = next_probe_at(3, now, backoff)

    assert (first - now).total_seconds() == 5.0
    assert (second - now).total_seconds() == 10.0
    assert (third - now).total_seconds() == 20.0


def test_next_probe_at_never_exceeds_the_max() -> None:
    clock = FakeClock()
    now = clock.now()
    backoff = ClusterBackoff(initial_s=5.0, max_s=12.0, factor=2.0)

    tenth = next_probe_at(10, now, backoff)

    assert (tenth - now).total_seconds() == 12.0


async def test_health_poller_is_due_for_a_never_probed_provider() -> None:
    poller = HealthPoller()
    clock = FakeClock()

    assert poller.is_due("anthropic", clock.now())


async def test_health_poller_probe_calls_only_health_never_complete_or_stream() -> None:
    provider = FakeLLMProvider(name="anthropic")
    provider.set_outage(True)
    poller = HealthPoller()
    clock = FakeClock()

    reading = await poller.probe("anthropic", lambda _name: provider, clock.now())

    assert reading.state is HealthState.DOWN
    assert provider.calls == []  # Never awaited complete()/stream(): only health().


async def test_health_poller_reschedules_after_a_down_reading() -> None:
    provider = FakeLLMProvider(name="anthropic")
    provider.set_outage(True)
    poller = HealthPoller(backoff=ClusterBackoff(initial_s=5.0, max_s=300.0, factor=2.0))
    clock = FakeClock()
    now = clock.now()

    await poller.probe("anthropic", lambda _name: provider, now)

    assert not poller.is_due("anthropic", now)
    assert not poller.is_due("anthropic", now + timedelta(seconds=4.9))
    assert poller.is_due("anthropic", now + timedelta(seconds=5.0))


async def test_health_poller_schedules_the_steady_probe_after_a_healthy_reading() -> None:
    provider = FakeLLMProvider(name="anthropic")
    provider.set_outage(True)
    poller = HealthPoller()
    clock = FakeClock()
    now = clock.now()
    await poller.probe("anthropic", lambda _name: provider, now)  # Advances past attempt 0.

    provider.set_outage(False)
    reading = await poller.probe("anthropic", lambda _name: provider, now)

    assert reading.state is HealthState.HEALTHY
    assert poller.failed_probes("anthropic") == 0  # The earlier failure is forgotten.
    # Steady state: not hammered every tick, but still watched at the slow cadence.
    assert not poller.is_due("anthropic", now)
    assert poller.is_due("anthropic", now + timedelta(seconds=DEFAULT_HEALTHY_PROBE_INTERVAL_S))


async def test_health_poller_counts_consecutive_failed_probes() -> None:
    provider = FakeLLMProvider(name="anthropic")
    provider.set_outage(True)
    poller = HealthPoller()
    clock = FakeClock()

    assert poller.failed_probes("anthropic") == 0
    await poller.probe("anthropic", lambda _name: provider, clock.now())
    await poller.probe("anthropic", lambda _name: provider, clock.now())

    assert poller.failed_probes("anthropic") == 2
    poller.reset("anthropic")
    assert poller.failed_probes("anthropic") == 0


def test_health_poller_reset_clears_a_provider_never_probed() -> None:
    poller = HealthPoller()

    poller.reset("anthropic")  # Must not raise for an unknown provider.
