"""Provide TravelLock: make a device seen on a network it never used step up, and tell everyone.

With ``travel_lock`` on (off by default; ``expose = "vpn"`` on Tailscale only), a device that logs
in on the remote listener from a network it has not used before must step up before anything else,
and every other device is notified (ADR-0033). ``TravelLock.network`` asks the
``PeerEndpointSource`` where the peer really connects from; ``check`` compares that with the
networks the device has been cleared on (``EntranceStore.logins``): a known network only moves its
last sighting, while a new one (or one the source cannot place, since unknown is never trusted) is
recorded as ``guard.entrance_travel_lock``, told to the ``SecurityNotifier``, and answered "this
session must step up". A network becomes known only when a person clears it: the session steps up
from it, or, for a device no person types at, a person confirms its ``NEW_NETWORK`` pending
confirmation (``trust``). The very first login counts too: a device that has used no network yet
has not used this one. The lock never approves anything.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.travel``. Built by
    ``open_travel_lock`` in the Entrance's composition root; called by the login flow (``network``
    and ``check``) and by step-up and a confirmed ``NEW_NETWORK`` (``trust``). Calls into the
    Entrance tables, the Pheromone Trail and the notifier through ``EnrolmentDeps``.

Key invariants:
    - Only the remote listener is judged: the loopback listener is the Hive Stand itself.
    - An unknown network (None) is never remembered, so it can never become "known".
    - The event is on the trail before the notice is sent, and neither carries a credential.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "travel lock".
    - hivemind.entrance.auth.travel.tailscale for the real endpoint source.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.session.models import Arrival, Listener
from hivemind.entrance.auth.travel.source import PeerEndpointSource
from hivemind.entrance.auth.travel.tailscale import tailscale_source
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.record import notify
from hivemind.entrance.errors import TravelLockUnavailableError
from hivemind.manifest import EntranceExposure, EntranceSection
from waggle.ids import DeviceId

TRAVEL_LOCK_KIND = "guard.entrance_travel_lock"  # A known device seen on a network it never used.

log = get_logger(__name__)

__all__ = ["TRAVEL_LOCK_KIND", "TravelLock", "open_travel_lock"]


class TravelLock:
    """Judge each remote login's network against the networks its device has been cleared on."""

    def __init__(self, deps: EnrolmentDeps, source: PeerEndpointSource) -> None:
        """Build the lock; prefer ``open_travel_lock``, which refuses where it cannot work.

        Args:
            deps: The Entrance tables (``logins``), the trail, the clock, the identity and the
                notifier.
            source: Where the peer behind an overlay address really connects from.
        """
        self._deps = deps
        self._source = source

    async def network(self, arrival: Arrival) -> str | None:
        """Return the network a login arrived from, as the lock judges it.

        Args:
            arrival: The login's listener and address.

        Returns:
            On the remote listener, what the endpoint source says (None when it cannot place the
            peer); on loopback, the address's own network.
        """
        if arrival.listener is not Listener.REMOTE:
            return arrival.network
        # Latency: one local-socket request to tailscaled, bounded by the source's own timeout.
        return await self._source.current_network(arrival.address)

    async def check(self, device: EnrolledDevice, arrival: Arrival, network: str | None) -> bool:
        """Decide whether a login's session must step up because its network is new.

        Args:
            device: The device that just proved itself.
            arrival: The login's listener and address.
            network: What ``network`` returned for it.

        Returns:
            True when the session must step up before anything else (the event is recorded and
            every other device told); False for the loopback listener or a known network.
        """
        # The loopback listener is the Hive Stand itself: nothing there has travelled.
        if arrival.listener is not Listener.REMOTE:
            return False
        records = self._deps.records
        # Latency: one local read.
        known = await records.store.logins.networks(device.id)
        if network is not None and network in known:
            await records.store.logins.remember_network(device.id, network, records.clock.now())
            return False
        payload: dict[str, JsonValue] = {
            "network": network,
            "address": arrival.trail_address,
            "known_networks": len(known),
        }
        event = records.identity.event(records.clock, TRAVEL_LOCK_KIND, device.id, payload)
        # Latency: one local trail write, then the notifier queues its notice and returns.
        await records.trail.record(event)
        await notify(self._deps, device.id, event)
        log.info("entrance.travel_lock", device_id=device.id, known_networks=len(known))
        return True

    async def trust(self, device_id: DeviceId, network: str | None) -> None:
        """Remember ``network`` for ``device_id`` once a person has cleared it.

        Args:
            device_id: The device.
            network: The network its flagged session came from; None (unknown) is never
                remembered.

        Raises:
            ValueError: ``network`` is not a canonical device network.
        """
        if network is None:
            return
        records = self._deps.records
        # Latency: one local upsert.
        await records.store.logins.remember_network(device_id, network, records.clock.now())


def open_travel_lock(
    section: EntranceSection, deps: EnrolmentDeps, source: PeerEndpointSource | None = None
) -> TravelLock | None:
    """Build the travel lock ``[entrance]`` asks for, refusing to run blind.

    Args:
        section: The manifest's ``[entrance]`` section.
        deps: The enrolment dependencies the lock records and notifies through.
        source: The endpoint source; None builds tailscaled's (``tailscale_source``).

    Returns:
        The lock, or None when ``travel_lock`` is off.

    Raises:
        TravelLockUnavailableError: ``travel_lock`` is on but ``expose`` is not ``vpn``, or
            tailscaled's socket cannot be used here.
    """
    if not section.travel_lock:
        return None
    # ADR-0033: only the Tailscale overlay shows real endpoints, so any other mode refuses.
    if section.expose is not EntranceExposure.VPN:
        raise TravelLockUnavailableError(f"expose is {section.expose.value!r}, not 'vpn'")
    return TravelLock(deps, source if source is not None else tailscale_source(section))
