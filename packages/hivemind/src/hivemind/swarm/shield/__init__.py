"""Re-export the Propolis shield: verify a device's VPN before work is placed on it.

The Propolis tier (the Comb Shield tier that requires a VPN) on a Real Cell is the operator's word
that the device already runs a tunnel, and the Hive checks it before placing work (README: "HiveMind
verifies the tunnel before placing work and never installs, starts or reroutes one"). The checks
themselves (`routes`, the four pure `judges` and the fixed read-only commands) are shared with the
Virtual Cell attestation and live in `hivemind.cell.tunnel`, below both; this package re-exports
them so its face is what it always was. What is the shield's own is `propolis`, which runs those
checks through the device's session and records the outcome, and `shielded`, the `RealCellSource`
decorator that holds a lease back until the checks pass. A caller writes
`from hivemind.swarm.shield import ShieldedSource` without knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Built by
    the composition root around a `SwarmSource` for each Propolis device. Calls into hivemind.cell,
    hivemind.cell.tunnel, the swarm recorder and the standard library.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - README.md "Comb Shield level" for the tier this verifies.
    - docs/waggle/enrolment.md for how a device is enrolled at the Propolis tier.
    - hivemind.cell.tunnel for the checks, and hivemind.hive.propolis for their Virtual Cell use.

Public API:
    - ShieldedSource, ShieldVerifier: the decorator and the protocol it calls (shielded).
    - PropolisChecks: runs the checks on a leased device and records the outcome (propolis).
    - ProbeCommand, COMMANDS: the fixed commands (re-exported from hivemind.cell.tunnel).
    - ShieldReport, CheckResult, judge_all and the four judges: the pure checks (re-exported).
    - Route, effective_interface, is_tunnel and the parsers: the routing-table readers
      (re-exported).
"""

from hivemind.cell.tunnel import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    COMMANDS,
    CheckResult,
    ProbeCommand,
    Route,
    ShieldReport,
    effective_interface,
    is_tunnel,
    judge_all,
    judge_default_route,
    judge_dns,
    judge_egress,
    judge_ipv6,
    parse_linux_routes,
    parse_linux_routes6,
    parse_macos_routes,
    parse_nameservers,
)
from hivemind.swarm.shield.propolis import PropolisChecks
from hivemind.swarm.shield.shielded import ShieldedSource, ShieldVerifier

__all__ = [
    "CHECK_DEFAULT_ROUTE",
    "CHECK_DNS",
    "CHECK_EGRESS",
    "CHECK_IPV6",
    "COMMANDS",
    "CheckResult",
    "ProbeCommand",
    "PropolisChecks",
    "Route",
    "ShieldReport",
    "ShieldVerifier",
    "ShieldedSource",
    "effective_interface",
    "is_tunnel",
    "judge_all",
    "judge_default_route",
    "judge_dns",
    "judge_egress",
    "judge_ipv6",
    "parse_linux_routes",
    "parse_linux_routes6",
    "parse_macos_routes",
    "parse_nameservers",
]
