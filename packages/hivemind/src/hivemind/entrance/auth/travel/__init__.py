"""Hold the travel lock: a device seen on a network it never used must step up, and all are told.

On the Tailscale overlay a device keeps its address wherever it roams, so the Hive Entrance (the
Hive's one HTTP door) asks tailscaled where each peer really connects from (ADR-0041). ``source``
defines ``PeerEndpointSource``; ``tailscale`` is the real one (tailscaled's local API over its Unix
socket) and the builder that refuses where it cannot see endpoints; ``fake`` places peers by hand
for tests; ``lock`` is ``TravelLock`` and ``open_travel_lock``, which judge a remote login's network
against the networks the device has been cleared on. Off by default; it never approves anything.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Built by the
    Entrance's composition root when ``travel_lock`` is on; called by login and step-up. Calls
    into ``httpx``, the Entrance tables, the trail and the notifier.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "travel lock".

Public API:
    - PeerEndpointSource: where a peer really connects from (source).
    - TailscaleEndpointSource, tailscale_source, unix_socket_client, TAILSCALED_SOCKET,
      LOCAL_API_BASE_URL, STATUS_PATH, LOCAL_API_TIMEOUT_S: tailscaled's local API (tailscale).
    - FakePeerEndpointSource: peers placed by hand (fake).
    - TravelLock, open_travel_lock, TRAVEL_LOCK_KIND: the lock itself (lock).
"""

from hivemind.entrance.auth.travel.fake import FakePeerEndpointSource
from hivemind.entrance.auth.travel.lock import TRAVEL_LOCK_KIND, TravelLock, open_travel_lock
from hivemind.entrance.auth.travel.source import PeerEndpointSource
from hivemind.entrance.auth.travel.tailscale import (
    LOCAL_API_BASE_URL,
    LOCAL_API_TIMEOUT_S,
    STATUS_PATH,
    TAILSCALED_SOCKET,
    TailscaleEndpointSource,
    tailscale_source,
    unix_socket_client,
)

__all__ = [
    "LOCAL_API_BASE_URL",
    "LOCAL_API_TIMEOUT_S",
    "STATUS_PATH",
    "TAILSCALED_SOCKET",
    "TRAVEL_LOCK_KIND",
    "FakePeerEndpointSource",
    "PeerEndpointSource",
    "TailscaleEndpointSource",
    "TravelLock",
    "open_travel_lock",
    "tailscale_source",
    "unix_socket_client",
]
