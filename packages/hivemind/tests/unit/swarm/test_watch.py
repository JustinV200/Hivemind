"""Tests for hivemind.swarm.watch: looking at an idle device through its Pollen Packet, read-only.

Fits into the Hive:
    Mirrors src/hivemind/swarm/watch.py (codingrules section 3: tests/unit mirrors src/ one-to-one).
    The probe asks a scripted device over a real link, so the request that reaches the device is
    read straight off the device's side, which is where the properties that matter are visible: the
    Warden is the sender, the request names only the four kinds and the roots, a reply about
    another device is an error and not data, and nothing a device reports is logged or recorded.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.watch for the module under test.
    - docs/waggle/gateway-contract.md section 9.4 for what the packet answers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
import structlog.testing
from builders.swarm import LinkRig, make_device_party, make_link_rig
from pydantic import ValidationError

from hivemind.pheromone import TrailQuery
from hivemind.swarm import watch
from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from hivemind.swarm.gateway import GatewayHub
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.watch import (
    MISMATCH_CODE,
    OBSERVE_REASON,
    PollenWatchProbe,
    readable_roots,
)
from waggle.messages.swarm import Observation, ObservationKind, Observe
from waggle.messages.swarm.watch import DEFAULT_OBSERVE_ITEMS, ProcessSample


@pytest.fixture
async def rig() -> AsyncIterator[LinkRig]:
    """A started link to a scripted device."""
    link = await make_link_rig()
    yield link
    await link.close()


def _asked(rig: LinkRig) -> list[Observe]:
    """The `swarm.observe` requests the device received, in order."""
    return [e.payload for e in rig.device.state.received if isinstance(e.payload, Observe)]


async def test_the_probe_returns_the_observation_the_packet_sent(rig: LinkRig) -> None:
    sample = ProcessSample(pid=1, name="init", cpu_share=0.5, rss_bytes=1024, started_at=None)
    rig.device.script.observation = Observation(
        device_id=rig.node.device_id,
        observed_at=rig.clock.now(),
        processes=(sample,),
        resources=None,
        log_lines=(),
        file_changes=(),
        is_truncated=False,
        reason="One process.",
    )

    seen = await PollenWatchProbe(rig.node, rig.hub).observe(
        [ObservationKind.PROCESS_LIST], [], None
    )

    assert seen.processes == (sample,) and seen.device_id == rig.node.device_id


async def test_the_request_is_made_as_the_devices_warden_and_names_only_what_was_asked(
    rig: LinkRig,
) -> None:
    since = rig.clock.now() - timedelta(minutes=5)

    await PollenWatchProbe(rig.node, rig.hub, max_items=7).observe(
        [ObservationKind.LOGS, ObservationKind.FILE_CHANGES], [Path("/var/log")], since
    )

    (asked,) = _asked(rig)
    sender = next(e.sender for e in rig.device.state.received if isinstance(e.payload, Observe))
    assert sender == rig.node.warden_id
    assert asked.device_id == rig.node.device_id and asked.reason == OBSERVE_REASON
    assert asked.kinds == (ObservationKind.LOGS, ObservationKind.FILE_CHANGES)
    assert asked.roots == ("/var/log",) and asked.since_at == since and asked.max_items == 7


async def test_a_probe_asks_for_the_default_number_of_items_unless_told_otherwise(
    rig: LinkRig,
) -> None:
    await PollenWatchProbe(rig.node, rig.hub).observe([ObservationKind.RESOURCE_USE], [], None)

    assert _asked(rig)[0].max_items == DEFAULT_OBSERVE_ITEMS


async def test_a_device_that_is_not_connected_cannot_be_observed(rig: LinkRig) -> None:
    probe = PollenWatchProbe(rig.node, GatewayHub(rig.clock))  # A hub that has no link in it.

    with pytest.raises(GatewayUnavailableError):
        await probe.observe([ObservationKind.PROCESS_LIST], [], None)


@pytest.mark.parametrize(
    ("kinds", "roots", "cursor"),
    [
        ([], [], False),  # Nothing asked for.
        ([ObservationKind.LOGS, ObservationKind.LOGS], ["/var/log"], False),  # A kind twice.
        ([ObservationKind.LOGS], [], False),  # Logs with no root to read.
        ([ObservationKind.PROCESS_LIST], ["/var/log"], False),  # A root nothing reads.
        ([ObservationKind.LOGS], ["/var/log"], True),  # A cursor with no file changes.
    ],
    ids=["no-kinds", "duplicate-kind", "logs-without-root", "root-without-need", "stray-cursor"],
)
async def test_a_request_that_does_not_make_sense_is_refused_before_anything_is_sent(
    rig: LinkRig, kinds: list[ObservationKind], roots: list[str], cursor: bool
) -> None:
    since = rig.clock.now() if cursor else None

    with pytest.raises(ValidationError):
        await PollenWatchProbe(rig.node, rig.hub).observe(kinds, roots, since)

    assert _asked(rig) == []


async def test_a_reply_about_another_device_is_an_error_and_not_data(rig: LinkRig) -> None:
    rig.device.script.observation = Observation(
        device_id=make_device_party(rig.clock).device_id,
        observed_at=rig.clock.now(),
        processes=(),
        resources=None,
        log_lines=(),
        file_changes=(),
        is_truncated=False,
        reason="Someone else.",
    )

    with pytest.raises(DeviceRequestError) as caught:
        await PollenWatchProbe(rig.node, rig.hub).observe([ObservationKind.PROCESS_LIST], [], None)

    assert caught.value.device_code == MISMATCH_CODE


async def test_a_refusal_from_the_packet_carries_its_own_code(rig: LinkRig) -> None:
    rig.device.script.refusals[Observe] = "pollen.capability_denied"

    with pytest.raises(DeviceRequestError) as caught:
        await PollenWatchProbe(rig.node, rig.hub).observe([ObservationKind.PROCESS_LIST], [], None)

    assert caught.value.device_code == "pollen.capability_denied"


async def test_a_packet_that_does_not_answer_in_time_is_a_timeout(
    rig: LinkRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch, "OBSERVE_REPLY_TIMEOUT_S", 0.05)
    rig.device.script.silent.add(Observe)

    with pytest.raises(DeviceTimeoutError):
        await PollenWatchProbe(rig.node, rig.hub).observe([ObservationKind.PROCESS_LIST], [], None)


async def test_nothing_the_device_reports_is_logged_or_recorded(rig: LinkRig) -> None:
    rig.device.script.observation = Observation(
        device_id=rig.node.device_id,
        observed_at=rig.clock.now(),
        processes=(
            ProcessSample(pid=7, name="secret-job", cpu_share=1.0, rss_bytes=1, started_at=None),
        ),
        resources=None,
        log_lines=(),
        file_changes=(),
        is_truncated=False,
        reason="One process.",
    )

    with structlog.testing.capture_logs() as captured:
        await PollenWatchProbe(rig.node, rig.hub).observe([ObservationKind.PROCESS_LIST], [], None)

    assert "secret-job" not in str(captured)
    assert await rig.trail.query(TrailQuery()) == ()


def _watching(rig: LinkRig, **overrides: object) -> SwarmNode:
    """The rig's device, as the registry would hold it with `overrides` applied."""
    return rig.node.model_copy(update=overrides)


def test_a_device_with_no_directory_named_has_none_to_ask_for(rig: LinkRig) -> None:
    assert readable_roots(rig.node) == ()


def test_the_directories_the_operator_named_are_asked_for_in_their_order(rig: LinkRig) -> None:
    node = _watching(rig, watch_roots=("/srv/b", "/var/log/a"))

    assert readable_roots(node) == ("/srv/b", "/var/log/a")


def test_a_directory_the_nodes_capabilities_do_not_cover_is_left_out_and_the_rest_kept(
    rig: LinkRig,
) -> None:
    node = _watching(
        rig,
        watch_roots=("/srv/ok/a", "/srv/not-ok", "/srv/ok/b"),
        granted_capabilities=("fs:read:/srv/ok/**", f"watch:{rig.node.cell_id}"),
    )

    # The packet would refuse a whole look at the device for the middle one.
    assert readable_roots(node) == ("/srv/ok/a", "/srv/ok/b")


async def test_the_probe_carries_the_roots_it_is_given_and_the_packet_sees_exactly_them(
    rig: LinkRig,
) -> None:
    node = _watching(rig, watch_roots=("/srv/ok", "/var/log/a"))

    await PollenWatchProbe(node, rig.hub).observe(
        [ObservationKind.LOGS, ObservationKind.FILE_CHANGES], readable_roots(node), None
    )

    (asked,) = _asked(rig)
    assert asked.roots == ("/srv/ok", "/var/log/a")
