"""Name the Hive Stand as this Cell reaches it: its host, and the addresses that host resolves to.

The Hive-state floor refuses a bee `net` to the Hive Stand itself (ADR-0033), and inside a Virtual
Cell the Hive Stand is whatever the Cell's Queen URL names: a host-gateway alias
(`host.docker.internal`), a gateway address (QEMU's `10.0.2.2`), or, for a Night Veil Cell, the
Hive Stand's onion service. `hive_stand_addresses` resolves that host once, when the Cell starts,
so the floor knows every address it answers on; a name that does not resolve leaves the Cell to
start anyway with a warning, since the name itself is still refused, and the floor's loopback and
link-local rules still stand. An onion host is never resolved here at all: it exists only inside
Tor, and a lookup would leak it to whoever answers (the Cell's own transport reaches it through
the SOCKS proxy instead). `hive_stand_names` is the host as a name, which the floor refuses before
any lookup.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Called by
    `hivemind.cli.in_cell.main.run_in_cell_warden` (the addresses, once, at start) and
    `hivemind.cli.in_cell.deps` (the names). Calls into `hivemind.cli.in_cell.config`,
    `hivemind.common.logging`, `hivemind.guard` (errors, net) and waggle (uris) only.

Key invariants:
    - An onion service host is never looked up.
    - A lookup failure is logged and answered with no addresses, never raised.

See Also:
    - hivemind.guard.policy.hive_state for HiveState, where both end up.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.common.logging import get_logger
from hivemind.guard.errors import UnresolvableHostError
from hivemind.guard.net import IPAddress, Resolver, ip_literal, resolve_host, system_resolver
from waggle.uris import is_onion_service_host

_DEFAULT_PORTS = {"ws": 80, "wss": 443}  # The port a Queen URL with none names, for the lookup.

__all__ = ["hive_stand_addresses", "hive_stand_names"]

_LOG = get_logger(__name__)


async def hive_stand_addresses(
    config: InCellRuntimeConfig, resolver: Resolver = system_resolver
) -> tuple[IPAddress, ...]:
    """Resolve the Queen URL's host once: every address the Hive Stand answers this Cell on.

    Args:
        config: This Cell's runtime config; `queen_waggle_url` is read.
        resolver: How a name is looked up; the system's resolver in production.

    Returns:
        The host itself when it is an address; every address a name resolves to; nothing for an
        onion service (never resolved here) or a name that did not resolve (logged).
    """
    parts = urlsplit(config.queen_waggle_url)
    host = parts.hostname or ""
    if is_onion_service_host(host):
        return ()  # Reached only through Tor, by name: a lookup here would leak it.
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(parts.scheme, 0)
    try:
        # External await: one bounded lookup (RESOLVE_TIMEOUT_S), made once, at start.
        return await resolve_host(host, port, resolver)
    except UnresolvableHostError as exc:
        # Not fatal: the name is refused by name regardless, and loopback stays refused.
        _LOG.warning("cell.hive_stand.unresolved", host=host, reason=exc.reason)
        return ()


def hive_stand_names(config: InCellRuntimeConfig) -> tuple[str, ...]:
    """Return the Queen URL's host when it is a name (an alias or an onion service), else none.

    Args:
        config: This Cell's runtime config; `queen_waggle_url` is read.

    Returns:
        The host as the one name the Hive Stand is reached by, or nothing for an address.
    """
    host = urlsplit(config.queen_waggle_url).hostname or ""
    return () if not host or ip_literal(host) is not None else (host,)
