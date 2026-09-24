"""Test helpers for hivemind.entrance.push: a stepping clock, a static resolver, HTTP recording.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped: the
    push tests need a clock whose backoff sleeps finish at once, a resolver answering from a
    table (no DNS in tests), an ``httpx.MockTransport`` handler that records requests and answers
    from a script, a Web Push user agent's keys, and subscriptions and devices with sensible
    defaults. None of these is a production fake of a Protocol the push package defines.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.entrance.push for the package under test.
    - builders.entrance.make_device for the device records used here.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx
from builders.entrance import make_device
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from hivemind.entrance.auth import b64url_encode
from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice
from hivemind.entrance.push import (
    ChannelKind,
    DestinationGuard,
    DestinationPolicy,
    Subscription,
    WebPushKeys,
    decrypt,
    new_subscription_id,
)
from hivemind.entrance.push.destinations import IPAddress, IPNetwork
from waggle.clock import FakeClock
from waggle.ids import DeviceId, new_device_id

START = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # A fixed "now" every push test starts from.
HOOK_HOST = "hook.example.net"  # A program's webhook receiver.
HOOK_URL = f"https://{HOOK_HOST}/hive/notices?token=t0k3n"  # The token must never be logged.
HOOK_ADDRESS = "203.0.113.10"  # TEST-NET-3: where the receiver's name resolves.
PUSH_HOST = "push.example.net"  # A browser vendor's push service.
PUSH_URL = f"https://{PUSH_HOST}/wpush/v2/gAAAAABm-subscription"  # A capability URL.
PUSH_ADDRESS = "198.51.100.20"  # TEST-NET-2: where the push service resolves.
OWN_ADDRESS = "192.0.2.5"  # TEST-NET-1: the Hive Stand's own LAN address.
VPN_CIDR = "100.64.0.0/10"  # Tailscale's range, as [entrance] vpn_cidrs' default has it.
AUTH_SECRET_BYTES = 16  # RFC 8291: the user agent's auth secret.

__all__ = [
    "HOOK_ADDRESS",
    "HOOK_HOST",
    "HOOK_URL",
    "OWN_ADDRESS",
    "PUSH_ADDRESS",
    "PUSH_HOST",
    "PUSH_URL",
    "START",
    "VPN_CIDR",
    "Recorder",
    "StaticResolver",
    "SteppingClock",
    "UserAgent",
    "approved_device",
    "make_guard",
    "make_policy",
    "web_push_subscription",
    "webhook_subscription",
]


class SteppingClock(FakeClock):
    """A FakeClock whose sleep moves time on by exactly what was asked, and records it."""

    def __init__(self) -> None:
        """Start at START with no sleeps recorded."""
        super().__init__(start=START)
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """Record ``seconds`` and advance by it at once: a backoff costs no real time."""
        self.sleeps.append(seconds)
        if seconds > 0:
            self.advance(seconds)


class StaticResolver:
    """A Resolver answering from a table; a name not in it fails like a DNS miss."""

    def __init__(self, answers: Mapping[str, Sequence[str]] | None = None) -> None:
        """Answer for the receiver and the push service by default, plus ``answers``."""
        self._answers: dict[str, tuple[str, ...]] = {
            HOOK_HOST: (HOOK_ADDRESS,),
            PUSH_HOST: (PUSH_ADDRESS,),
        }
        self._answers.update(
            {host: tuple(addresses) for host, addresses in (answers or {}).items()}
        )
        self.lookups: list[str] = []

    def answer(self, host: str, *addresses: str) -> None:
        """Change what ``host`` resolves to from now on (none: it stops resolving)."""
        self._answers[host] = addresses

    async def __call__(self, host: str, port: int) -> tuple[IPAddress, ...]:
        """Return the table's answer for ``host``; see Resolver."""
        self.lookups.append(host)
        addresses = self._answers.get(host)
        if not addresses:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return tuple(ipaddress.ip_address(address) for address in addresses)


class Recorder:
    """An ``httpx.MockTransport`` handler: records every request, answers from a script."""

    def __init__(self, *script: int | Exception, default: int = 201) -> None:
        """Answer each request with the next scripted status (or raise the next exception).

        Args:
            script: Statuses or exceptions, in order; once used up, ``default`` answers.
            default: The status every request past the script gets.
        """
        self._script = list(script)
        self._default = default
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it; see httpx.MockTransport."""
        self.requests.append(request)
        answer = self._script.pop(0) if self._script else self._default
        if isinstance(answer, Exception):
            raise answer
        # A redirect points somewhere the guard would refuse, to prove it is never followed.
        redirect = {"location": "https://127.0.0.1/elsewhere"} if 300 <= answer < 400 else {}
        return httpx.Response(answer, headers=redirect)

    def client(self) -> httpx.AsyncClient:
        """Return a client whose every request comes here; close it after the test."""
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


class UserAgent:
    """A Web Push user agent: its P-256 key pair and auth secret, as a browser holds them."""

    def __init__(self) -> None:
        """Mint fresh keys."""
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.auth_secret = secrets.token_bytes(AUTH_SECRET_BYTES)

    @property
    def keys(self) -> WebPushKeys:
        """The keys it registers: ``p256dh`` and ``auth``, base64url."""
        public = self.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return WebPushKeys(p256dh=b64url_encode(public), auth=b64url_encode(self.auth_secret))

    def open(self, body: bytes) -> bytes:
        """Decrypt a push body the way the browser would."""
        return decrypt(body, self.private_key, self.auth_secret)


def make_policy(
    allowed_hosts: frozenset[str] = frozenset(), allowed_networks: tuple[IPNetwork, ...] = ()
) -> DestinationPolicy:
    """Build a DestinationPolicy with the overlay range and the Hive Stand's own address."""
    return DestinationPolicy(
        vpn_networks=(ipaddress.ip_network(VPN_CIDR),),
        allowed_hosts=allowed_hosts,
        allowed_networks=allowed_networks,
        own_addresses=frozenset({ipaddress.ip_address(OWN_ADDRESS)}),
    )


def make_guard(
    resolver: StaticResolver | None = None, policy: DestinationPolicy | None = None
) -> DestinationGuard:
    """Build a guard over a static resolver and the default test policy."""
    return DestinationGuard(policy or make_policy(), resolver or StaticResolver())


def approved_device(clock: FakeClock, *capabilities: str, **overrides: object) -> EnrolledDevice:
    """Build an APPROVED device holding ``capabilities`` (entrance:push unless told otherwise)."""
    held = capabilities or ("entrance:push",)
    return make_device(clock, DeviceStatus.APPROVED, capabilities=held, **overrides)


def webhook_subscription(
    clock: FakeClock, device_id: DeviceId | None = None, endpoint: str = HOOK_URL
) -> Subscription:
    """Build a webhook subscription (a fresh device id unless one is given)."""
    return Subscription(
        id=new_subscription_id(clock),
        device_id=device_id or new_device_id(clock),
        channel=ChannelKind.WEBHOOK,
        endpoint=endpoint,
        created_at=clock.now(),
    )


def web_push_subscription(
    clock: FakeClock,
    agent: UserAgent,
    device_id: DeviceId | None = None,
    endpoint: str = PUSH_URL,
) -> Subscription:
    """Build a Web Push subscription holding ``agent``'s keys."""
    return Subscription(
        id=new_subscription_id(clock),
        device_id=device_id or new_device_id(clock),
        channel=ChannelKind.WEB_PUSH,
        endpoint=endpoint,
        keys=agent.keys,
        created_at=clock.now(),
    )
