"""Tests for hivemind.wardens.local_pool.hosting.launch: the argv template and the health wait.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/launch.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.launch for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.hosting import drive, make_server_config

from hivemind.llm import FakeLLMProvider
from hivemind.wardens.local_pool.hosting.launch import launch_spec, wait_healthy
from waggle.clock import FakeClock


def test_launch_spec_substitutes_the_port_and_scratch_placeholders() -> None:
    config = make_server_config(port=18_090)

    scratch = Path("/scratch/x")

    spec = launch_spec(config, scratch)

    assert spec.argv == ("scripted-server", "--port", "18090", "--scratch", str(scratch))
    assert spec.log_path == Path("model-server-box.log")


def test_launch_spec_leaves_braces_that_are_not_placeholders_alone() -> None:
    config = make_server_config(argv=("scripted-server", '--opts={"n": {port}}'), port=7)

    assert launch_spec(config, Path("/s")).argv[1] == '--opts={"n": 7}'


async def _alive() -> bool:
    return True


async def test_wait_healthy_returns_true_as_soon_as_the_probe_passes() -> None:
    clock = FakeClock()

    assert await wait_healthy(FakeLLMProvider(), _alive, clock, 5.0) is True


async def test_wait_healthy_gives_up_when_the_probe_never_passes() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    provider.set_outage(True)

    assert await drive(clock, wait_healthy(provider, _alive, clock, 2.0)) is False


async def test_wait_healthy_waits_through_a_slow_start() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    provider.set_outage(True)

    async def ready_after_a_while() -> bool:
        if clock.monotonic() >= 1.0:
            provider.set_outage(False)
        return True

    assert await drive(clock, wait_healthy(provider, ready_after_a_while, clock, 10.0)) is True


async def test_wait_healthy_stops_at_once_when_the_process_has_died() -> None:
    clock = FakeClock()

    async def dead() -> bool:
        return False

    assert await wait_healthy(FakeLLMProvider(), dead, clock, 5.0) is False


async def test_a_probe_that_raises_counts_as_not_ready() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()

    async def broken() -> object:
        raise ConnectionResetError("half started")

    provider.health = broken  # type: ignore[method-assign, assignment]

    assert await drive(clock, wait_healthy(provider, _alive, clock, 1.0)) is False
