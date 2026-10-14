"""Re-export the tunnel checks: how a Cell's VPN is verified, shared by Real and Virtual Cells.

The Propolis tier (the Comb Shield tier that requires a VPN and disables Tor) is the claim that a
Cell's traffic leaves through a tunnel its own image or operator runs, and the Hive trusts the
claim only after looking, with read-only commands and never by installing, starting or rerouting a
VPN. A Real Cell (a borrowed device) is looked at before each lease by `hivemind.swarm.shield`; a
Virtual Cell (one the Hive provisions) before it is ready, by `hivemind.hive.propolis`. The two
live in sibling Layer 3 subsystems that may not import each other, so what they share sits here,
below both: `routes` reads a routing table and DNS settings, `judges` holds the four pure checks
over them and the report they make, and `check` runs the fixed commands through a Cell's session.
This file is the package's face: a caller writes `from hivemind.cell.tunnel import check_tunnel`
without knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell`. Called by
    `hivemind.swarm.shield` and `hivemind.hive.propolis`. Calls into hivemind.cell.session, the
    common errors and the standard library.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.
    - Nothing here installs, starts, stops or reroutes anything on a Cell.

See Also:
    - README.md "Comb Shield level" for the tier this verifies.
    - docs/adr/0057-propolis-is-verified-by-looking.md for why one set of judges serves both.

Public API:
    - check_tunnel, judge_outputs, read_command, ProbeCommand, COMMANDS, CHECK_PLATFORM,
      PROBE_TIMEOUT_S, MAX_PROBE_BYTES: the fixed commands, the run, and the judging of text read
      some other way (check).
    - ShieldReport, CheckResult, judge_all, the four judges and their CHECK_* names: the pure
      checks (judges).
    - Route, effective_interface, is_tunnel, TUNNEL_PREFIXES and the parsers: the routing-table
      readers (routes).
"""

from hivemind.cell.tunnel.check import (
    CHECK_PLATFORM,
    COMMANDS,
    MAX_PROBE_BYTES,
    PROBE_TIMEOUT_S,
    ProbeCommand,
    check_tunnel,
    judge_outputs,
    read_command,
)
from hivemind.cell.tunnel.judges import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    CheckResult,
    ShieldReport,
    judge_all,
    judge_default_route,
    judge_dns,
    judge_egress,
    judge_ipv6,
)
from hivemind.cell.tunnel.routes import (
    TUNNEL_PREFIXES,
    IpAddress,
    IpNetwork,
    Route,
    effective_interface,
    is_tunnel,
    parse_linux_routes,
    parse_linux_routes6,
    parse_macos_routes,
    parse_nameservers,
)

__all__ = [
    "CHECK_DEFAULT_ROUTE",
    "CHECK_DNS",
    "CHECK_EGRESS",
    "CHECK_IPV6",
    "CHECK_PLATFORM",
    "COMMANDS",
    "MAX_PROBE_BYTES",
    "PROBE_TIMEOUT_S",
    "TUNNEL_PREFIXES",
    "CheckResult",
    "IpAddress",
    "IpNetwork",
    "ProbeCommand",
    "Route",
    "ShieldReport",
    "check_tunnel",
    "effective_interface",
    "is_tunnel",
    "judge_all",
    "judge_default_route",
    "judge_dns",
    "judge_egress",
    "judge_ipv6",
    "judge_outputs",
    "parse_linux_routes",
    "parse_linux_routes6",
    "parse_macos_routes",
    "parse_nameservers",
    "read_command",
]
