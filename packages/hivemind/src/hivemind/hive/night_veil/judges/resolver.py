"""Judge where a Night Veil Cell's DNS resolver is: Tor's own DNS port, or only through the tunnel.

Night Veil's `dns_leak_free` check (roadmap step 5.7b) used to pass on any `/etc/resolv.conf` that
could be read, which says nothing about where a name lookup goes. A lookup leaks when it leaves the
Cell for a resolver that is not the operator's tunnel's or Tor's own, because that resolver (an
ISP's, a cloud's, a LAN router's) then sees every name the Cell asks for. So the judge asks, of each
`nameserver` the Cell lists, where it is. A loopback address is the Cell's own stub, and where a
stub forwards cannot be seen from outside it; it is accepted only when Tor itself serves DNS there,
which the image's `torrc` says with `DNSPort` on that loopback address at port 53 (a `resolv.conf`
cannot name another port). Any other address must be one a packet to which leaves by a tunnel
interface, and that is exactly the question the Propolis tier asks of its resolvers, so it is
answered by the same judge (`hivemind.cell.tunnel.judge_dns`) over the same routing-table readers,
not a copy that could drift. A Cell with no resolver at all fails, as in the Propolis tier: "no
leak" is granted only on a positive look at where lookups go.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil.judges`. Called by `hive.night_veil.
    session_probe.SessionNightVeilProbe` with the text of four fixed reads. Calls into
    `hivemind.cell.tunnel` (the parsers and the DNS judge) and `hive.night_veil.results` only.

Key invariants:
    - Pure and total: the same text always gives the same result, and nothing raises; a line that
      cannot be read is skipped, so an unreadable file fails the check closed rather than passing.
    - Green needs every resolver to be Tor's own DNS port on loopback or reached by a tunnel.
    - A result's `detail` is one of a few fixed words; never an address, an interface or any text
      the Cell produced, because the result is recorded on the trail.

See Also:
    - hivemind.cell.tunnel.judges for the tunnel half of the question, shared with Propolis.
    - images/night-veil-ubuntu/torrc for the Tor configuration whose `DNSPort` is read here.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping

from hivemind.cell.tunnel import (
    IpAddress,
    judge_dns,
    parse_linux_routes,
    parse_linux_routes6,
    parse_nameservers,
)
from hivemind.hive.night_veil.results import CheckResult, verdict

DNS_PORT = 53  # The port a `resolv.conf` nameserver is always asked on: it cannot name another.
LOOPBACK_V4 = "127.0.0.1"  # Where Tor binds a port given without an address.
# One `DNSPort` line of a torrc: Tor's option names are case-insensitive, and the value may be
# followed by flags (`IsolateClientAddr`), which are not read.
_DNS_PORT_LINE = re.compile(r"^\s*DNSPort\s+(\S+)", re.IGNORECASE | re.MULTILINE)

__all__ = ["DNS_PORT", "judge_resolver", "tor_dns_addresses"]


def judge_resolver(texts: Mapping[str, str]) -> CheckResult:
    """Judge that every resolver the Cell lists is Tor's DNS port on loopback or behind a tunnel.

    Args:
        texts: The text of the fixed reads, by key: "dns" (`/etc/resolv.conf`), "routes" and
            "routes6" (the kernel's IPv4 and IPv6 routing tables) and "torrc" (Tor's
            configuration); a key left out, or "", is a read that could not be had.

    Returns:
        PASS when there is at least one resolver and each is Tor's own or reached through a
        tunnel; FAIL when there is none, one is a stub that cannot be seen past, one is not reached
        through a tunnel, or the routing table could not be read.
    """
    nameservers = parse_nameservers(texts.get("dns", ""))
    # With nothing listed there is no resolver to place, and an unreadable file looks the same: the
    # check cannot say where lookups go, so it does not say they are safe.
    if not nameservers:
        return verdict(passed=False, detail="no_nameservers")
    tor = tor_dns_addresses(texts.get("torrc", ""))
    elsewhere = tuple(address for address in nameservers if address not in tor)
    if not elsewhere:
        return verdict(passed=True, detail="tor_dns_port")
    # Everything that is not Tor's own must be reached through a tunnel; the tunnel judge also
    # fails closed on a loopback stub (it cannot see where the stub forwards) and on a table it
    # could not read, which is why it is asked and not re-implemented.
    routes = parse_linux_routes(texts.get("routes", "")) + parse_linux_routes6(
        texts.get("routes6", "")
    )
    answer = judge_dns(routes, elsewhere)
    if not answer.has_passed:
        return verdict(passed=False, detail=answer.reason)
    # Say which way each resolver was placed, so a trail reader sees whether Tor served any.
    only_tunnel = len(elsewhere) == len(nameservers)
    return verdict(passed=True, detail="tunnel" if only_tunnel else "tor_dns_port_and_tunnel")


def tor_dns_addresses(torrc: str) -> frozenset[IpAddress]:
    """Return the loopback addresses a torrc has Tor answer DNS on, at the port a resolver is asked.

    Args:
        torrc: The text of Tor's configuration; "" if it could not be read.

    Returns:
        Each loopback address a `DNSPort` line binds at port 53. A port given alone binds the IPv4
        loopback; `auto`, another port and an address off loopback (a resolver the Cell's own
        processes cannot be pointed at by name alone, or one open to others) are not counted.
    """
    found: set[IpAddress] = set()
    for line in _DNS_PORT_LINE.finditer(torrc):
        endpoint = _endpoint(line.group(1))
        if endpoint is not None and endpoint[1] == DNS_PORT and endpoint[0].is_loopback:
            found.add(endpoint[0])
    return frozenset(found)


def _endpoint(value: str) -> tuple[IpAddress, int] | None:
    """Read a torrc `DNSPort` value (`53`, `127.0.0.1:53`, `[::1]:53`) into (address, port)."""
    host, separator, port_text = value.rpartition(":")
    if not separator:
        host, port_text = LOOPBACK_V4, value  # A bare port: Tor binds loopback.
    try:
        return ipaddress.ip_address(host.strip("[]")), int(port_text)
    except ValueError:
        return None  # `auto`, `0`-style words or garbage: not an endpoint we can place.
