"""Define what the Hive Entrance is built from: typed parts, never the Hive Manifest itself.

The composition root (``hive serve``) is the one place a Hive Manifest becomes collaborators
(codingrules 13): it reads the ``[entrance]`` section and the other slices the Entrance needs, opens
the Entrance's tables on the Hive's own database file, loads the keys from the secret store, plans
the exposure against this host, and hands the Entrance these parts. ``EntranceParts`` groups them by
what they are for, so ``build_entrance`` stays within codingrules 5.1's parameter bound and a test
can build the same Entrance over in-memory tables and fakes.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Built by
    ``hivemind.cli.compose`` (``hive serve``) and by tests; read by
    ``hivemind.entrance.runtime.build``. Calls into the types it names only.

Key invariants:
    - The keys are held, never logged: every key-bearing field is left out of ``repr``.

See Also:
    - hivemind.entrance.runtime.build for how the parts are wired.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from hivemind.entrance.enrol import EntranceIdentity
from hivemind.entrance.expose import ExposurePlan, HiveAuthority
from hivemind.entrance.gate import HiveReads, QueenDoor
from hivemind.entrance.push import Resolver, SubscriptionStore, VapidSigner, system_resolver
from hivemind.entrance.runtime.tls import NoCertificates, RevokedSerials
from hivemind.entrance.store import EntranceStore
from hivemind.guard import Enforcer, GuardPolicy
from hivemind.manifest import EntranceSection
from hivemind.pheromone import DEFAULT_POLL_INTERVAL_S, PheromoneTrail
from waggle.clock import Clock
from waggle.signing import Ed25519Signer

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address  # An address of either family.

__all__ = [
    "EntranceHive",
    "EntranceKeys",
    "EntranceParts",
    "EntranceSettings",
    "EntranceTables",
    "IPAddress",
]


@dataclass(frozen=True, slots=True)
class EntranceSettings:
    """What the manifest decides, as the Entrance reads it.

    Attributes:
        section: ``[entrance]``.
        identity: The Hive, node and default actor every ``guard.*`` event carries.
        goal_spend_cap_usd: ``[forage] spend_cap_per_goal_usd``.
        plan: The exposure plan ``plan_exposure`` made for this host.
        web_root: The Observation Hive's build directory, served when it exists.
        tunnel_env: The tunnel child's environment (``tunnel_environment``); None when no tunnel.
        poll_interval_s: How often the stream hub polls the trail once caught up.
    """

    section: EntranceSection
    identity: EntranceIdentity
    goal_spend_cap_usd: float
    plan: ExposurePlan
    web_root: Path | None = None
    tunnel_env: Mapping[str, str] | None = field(default=None, repr=False)
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S


@dataclass(frozen=True, slots=True)
class EntranceTables:
    """Where the Entrance keeps and audits its state.

    Attributes:
        store: The Entrance tables (devices, sessions, logins, held requests, the mode).
        push: The push subscriptions and their delivery log.
        trail: The Pheromone Trail the tables write their events to.
    """

    store: EntranceStore
    push: SubscriptionStore
    trail: PheromoneTrail


@dataclass(frozen=True, slots=True)
class EntranceHive:
    """The Hive the Entrance serves, as the composition root hands it over.

    Attributes:
        queen: The Queen's door: every write into the Hive.
        reads: The stores the Entrance reads directly.
        enforcer: The Guard's enforcer (the Entrance route point).
        policy: The Guard policy (the device ceiling and proposed set).
    """

    queen: QueenDoor
    reads: HiveReads
    enforcer: Enforcer
    policy: GuardPolicy


@dataclass(frozen=True, slots=True)
class EntranceKeys:
    """The keys the Entrance signs and serves TLS with.

    Attributes:
        hive_signer: The Hive's own Ed25519 key: webhooks, and the key programs pin.
        vapid: The Web Push VAPID signer; None when ``[entrance.push] web_push`` is off.
        topic_key: The key a Web Push ``Topic`` is derived under.
        authority: The Hive's certificate authority; None when no mutual TLS is asked for.
        serials: The revoked device certificates, for every revocation list.
    """

    hive_signer: Ed25519Signer = field(repr=False)
    vapid: VapidSigner | None = field(repr=False)
    topic_key: bytes = field(repr=False)
    authority: HiveAuthority | None = field(default=None, repr=False)
    serials: RevokedSerials = field(default_factory=NoCertificates)


@dataclass(frozen=True, slots=True)
class EntranceParts:
    """Everything ``build_entrance`` wires, grouped by what it is for.

    Attributes:
        settings: What the manifest decides.
        tables: The Entrance's tables and the trail.
        hive: The Queen, the stores, the enforcer and the policy.
        keys: The signing keys and the TLS authority.
        clock: The Entrance's clock.
        http: The HTTP client push deliveries use, built without environment proxies.
        own_addresses: Every address the Hive Stand answers on; no push destination may be one.
        resolver: Resolves push destinations; the system resolver unless a test injects one.
    """

    settings: EntranceSettings
    tables: EntranceTables
    hive: EntranceHive
    keys: EntranceKeys
    clock: Clock
    http: httpx.AsyncClient
    own_addresses: frozenset[IPAddress] = frozenset()
    resolver: Resolver = system_resolver
