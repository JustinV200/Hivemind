"""The Guard's view of the network: which hosts a bee may never reach, and how a host resolves.

A bee's network capability names a host (`net:api.example.com`), but a connection goes to an
address, and one host can be spelled many ways that all reach the Hive Stand itself (`localhost`,
`127.1`, `2130706433`, `[::ffff:127.0.0.1]`, or a DNS name answering 127.0.0.1). This package holds
what the Guard's floors and the HTTP tool both need to close that gap (roadmap step 10.3a,
ADR-0039, ADR-0041): pure predicates over hosts and addresses (`addresses`: loopback, unspecified,
link-local, a cloud metadata endpoint, one of the Hive Stand's own addresses, each judged on the
plain form of an IPv4-mapped address), and the one seam that turns a name into its addresses
(`resolve`), with an honest fake for tests (`fake`). A tool resolves first, asks the Guard about
every address it got back, then pins its connection to the address that was checked, so a DNS
answer that changes in between (rebinding) can never aim the request somewhere unchecked.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard`. Read by
    `hivemind.guard.policy.floors`, `hivemind.guard.policy.hive_state` and
    `hivemind.workers.tools.http`, and usable by any layer above that connects on a bee's or a
    device's behalf. Calls into the standard library and `hivemind.guard.errors` only.

Key invariants:
    - `addresses` is pure; `resolve.system_resolver` is the one function here that performs I/O.
    - An address is always judged in its plain form (`plain_address`), never as spelled.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Bees never touch
      the Hive's own state".
    - hivemind.entrance.push.destinations for the webhook guard that first used this approach.

Public API:
    - IPAddress, IPNetwork, LOCALHOST, METADATA_HOST_NAMES, METADATA_ADDRESSES: the address types
      and the fixed names and addresses the predicates know (addresses).
    - plain_address, normalise_host, ip_literal, is_loopback_name, address_refusal,
      network_refusal, is_metadata_address, is_metadata_host: the pure predicates (addresses).
    - Resolver, system_resolver, resolve_host, RESOLVE_TIMEOUT_S: the resolver seam (resolve).
    - FakeResolver, DEFAULT_ANSWER: the table-driven resolver tests use (fake).
    - UnresolvableHostError: a host with no usable address (re-exported from
      hivemind.guard.errors).
"""

from hivemind.guard.errors import UnresolvableHostError
from hivemind.guard.net.addresses import (
    LOCALHOST,
    METADATA_ADDRESSES,
    METADATA_HOST_NAMES,
    IPAddress,
    IPNetwork,
    address_refusal,
    ip_literal,
    is_loopback_name,
    is_metadata_address,
    is_metadata_host,
    network_refusal,
    normalise_host,
    plain_address,
)
from hivemind.guard.net.fake import DEFAULT_ANSWER, FakeResolver
from hivemind.guard.net.resolve import RESOLVE_TIMEOUT_S, Resolver, resolve_host, system_resolver

__all__ = [
    "DEFAULT_ANSWER",
    "LOCALHOST",
    "METADATA_ADDRESSES",
    "METADATA_HOST_NAMES",
    "RESOLVE_TIMEOUT_S",
    "FakeResolver",
    "IPAddress",
    "IPNetwork",
    "Resolver",
    "UnresolvableHostError",
    "address_refusal",
    "ip_literal",
    "is_loopback_name",
    "is_metadata_address",
    "is_metadata_host",
    "network_refusal",
    "normalise_host",
    "plain_address",
    "resolve_host",
    "system_resolver",
]
