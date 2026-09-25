"""Tests for hivemind.entrance.auth.travel.lock: a new network means step-up and a notice.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/travel/lock.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.travel.lock for the module under test.
"""

from __future__ import annotations

import pytest
from builders.entrance import (
    ADDRESS,
    LOOPBACK,
    REMOTE,
    admitted_program,
    auth_rig,
    memory_enrolment,
    program_login,
)

from hivemind.entrance.auth import FakePeerEndpointSource, TravelLock, open_travel_lock
from hivemind.entrance.errors import TravelLockUnavailableError
from hivemind.manifest import EntranceSection

_HOME = "203.0.113.0/24"
_CAFE = "198.51.100.0/24"
_KIND = "guard.entrance_travel_lock"


async def test_a_device_on_a_network_it_never_used_must_step_up_and_all_are_told() -> None:
    source = FakePeerEndpointSource({ADDRESS: _HOME})
    auth = await auth_rig(source=source)
    device, signer = await admitted_program(auth.enrolment)

    first = await program_login(auth, device, signer, REMOTE)

    assert first.session.needs_step_up
    assert first.session.network == _HOME
    assert (await auth.store.get_device(device.id)).last_network == _HOME
    (event,) = await auth.enrolment.events(_KIND)
    assert (event.subject_id, event.payload) == (
        device.id,
        {"network": _HOME, "address": ADDRESS, "known_networks": 0},
    )
    assert auth.enrolment.notifier.notices[-1].event_id == event.id
    assert await auth.store.logins.networks(device.id) == frozenset()


async def test_a_known_network_passes_and_a_new_one_is_flagged_again() -> None:
    source = FakePeerEndpointSource({ADDRESS: _HOME})
    auth = await auth_rig(source=source)
    device, signer = await admitted_program(auth.enrolment)
    travel = auth.deps.guards.travel
    assert travel is not None
    await travel.trust(device.id, _HOME)

    home = await program_login(auth, device, signer, REMOTE)
    source.place(ADDRESS, _CAFE)
    cafe = await program_login(auth, device, signer, REMOTE)

    assert (home.session.needs_step_up, cafe.session.needs_step_up) == (False, True)
    (event,) = await auth.enrolment.events(_KIND)
    assert (event.payload["network"], event.payload["known_networks"]) == (_CAFE, 1)


async def test_an_unplaceable_peer_is_never_trusted() -> None:
    source = FakePeerEndpointSource({ADDRESS: None})
    auth = await auth_rig(source=source)
    device, signer = await admitted_program(auth.enrolment)
    travel = auth.deps.guards.travel
    assert travel is not None

    opened = await program_login(auth, device, signer, REMOTE)
    await travel.trust(device.id, opened.session.network)

    assert opened.session.needs_step_up
    assert opened.session.network is None
    assert await auth.store.logins.networks(device.id) == frozenset()


async def test_the_loopback_listener_is_never_judged() -> None:
    source = FakePeerEndpointSource({ADDRESS: _HOME})
    auth = await auth_rig(source=source)
    device, signer = await admitted_program(auth.enrolment)

    opened = await program_login(auth, device, signer, LOOPBACK)

    assert not opened.session.needs_step_up
    assert source.asked == []
    assert await auth.enrolment.events(_KIND) == ()


def test_open_travel_lock_is_off_by_default_and_refuses_outside_vpn_mode() -> None:
    rig = memory_enrolment()
    source = FakePeerEndpointSource()

    off = open_travel_lock(EntranceSection(), rig.deps, source)
    on = open_travel_lock(EntranceSection(expose="vpn", travel_lock=True), rig.deps, source)

    assert off is None
    assert isinstance(on, TravelLock)
    with pytest.raises(TravelLockUnavailableError, match="not 'vpn'"):
        open_travel_lock(EntranceSection(expose="lan", travel_lock=True), rig.deps, source)
