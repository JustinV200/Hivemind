"""Map VirtualCellSpec.network_policy onto QEMU user-mode (SLIRP) networking args.

Pure functions only (codingrules 8.3: "pure core, effectful edges"); `hivemind.hive.backends.
qemu.backend.QemuCellBackend` calls `plan_network` to decide the VM's `-netdev` argument and which
URL the Cell should actually dial, then hands the real QEMU invocation to
`hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner`. `NetworkPolicy` is defined once, in
`hivemind.hive.models`, for every backend; this module is QEMU's own reading of it, mirroring
`hivemind.hive.backends.docker.network`'s shape and its module docstring's own "what each policy
really enforces" convention.

QEMU's user-mode networking (`-netdev user,...`, ADR-0027: never `-netdev user,hostfwd=...` --
no inbound port, ever) gives every VM a private, per-process virtual router on `10.0.2.0/24`: the
VM is `10.0.2.15`, the virtual router (and QEMU's own alias for "the host") is `10.0.2.2`, DNS is
`10.0.2.3`. Because this subnet is private to *each* QEMU process, reusing the same addresses for
every Cell this backend makes never collides -- the same reasoning Docker's `host.docker.internal`
alias relies on for every container.

Cutting a RUNNING VM's egress (isolation, roadmap step 10.6a) is not offered either, so
`QemuCellBackend` does not declare `can_cut_egress`: `restrict=on` is fixed when the `-netdev` is
created, and the runtime levers QMP has (`set_link` off, or `netdev_del` and a fresh `netdev_add`)
take the guest's one NIC down with every connection on it, the Waggle link included. A real
implementation needs the same thing Docker's does (`hivemind.hive.backends.docker.network`): an
egress control beside the hypervisor that can drop the VM's traffic except the control link's.

What each policy really enforces at the QEMU level, and what it does not:

    NONE: `-netdev user,...,restrict=on`. QEMU's own docs: with `restrict=on` "the guest will be
    isolated, i.e. it will not be able to contact the host and no guest IP packets will be routed
    over the host to the outside" -- this includes the `10.0.2.2` host alias itself, which
    `restrict=on` also blocks. The one documented exception is an explicit `guestfwd` rule: QEMU
    still services a `guestfwd` forward even under `restrict=on`, because it is a fixed, one-shot
    port forward terminated inside QEMU's own SLIRP stack, not a route "to the outside" in the
    sense `restrict` disables. This module adds exactly one such rule, forwarding
    `GUEST_CONTROL_ADDR:GUEST_CONTROL_PORT` to the real Queen endpoint (ADR-0027: the control link
    must survive every network policy, NONE included, mirroring Docker's own
    `host_gateway_extra_hosts()` applied "regardless of policy"). Everything else the guest tries
    to reach -- any other host or address -- is refused. Caveat, stated plainly rather than
    assumed: the `guestfwd=...-cmd:...` relay this module builds
    (`hivemind.hive.backends.qemu.process_runner`) shells out to the *host's* own Python
    interpreter to bridge the forwarded connection to the real Queen socket; this has been
    reviewed by reading, not exercised against a real `qemu-system-x86_64` (this dev host has
    none, ADR-0026), so a Docker/QEMU-enabled CI runner (a later roadmap step) is what actually
    proves it end to end.

    EGRESS_ONLY: `-netdev user,id=net0` with no `restrict` -- full outbound NAT, so the Cell can
    reach anything, including the Queen at its ordinary reachable address (rewritten to the
    `10.0.2.2` host alias only when that address is itself loopback, the same "reach the host from
    inside" problem Docker's own network module documents). "No inbound" is not this network's
    job: no Virtual Cell backend ever publishes a port (codingrules section 15), whatever the
    policy.

    ALLOWLIST: the same unrestricted user network as EGRESS_ONLY -- QEMU's own SLIRP stack has no
    built-in way to restrict outbound reach to a named set of hosts (that needs a firewall inside
    the guest, or a proxy the Cell is configured to use). This module only stamps
    `spec.network_allowlist` into a label for audit and for a later enforcement layer to read; see
    the TODO(5.7a) marker below, mirroring Docker's own `ALLOWLIST_LABEL`.

    VPN_TOR (roadmap step 5.7a): the same unrestricted user network as EGRESS_ONLY -- QEMU's own
    SLIRP stack cannot itself restrict outbound reach to "only the VPN endpoint and Tor's own
    bootstrap addresses" any more than Docker's network API can (`hivemind.hive.backends.docker.
    network`'s own module docstring). The real enforcement is the `night-veil-ubuntu` image's own
    in-guest nftables kill-switch (roadmap step 5.3a); unlike a Docker container, a QEMU guest's
    own init runs with full kernel privilege inside the VM, so there is no host-side capability
    (Docker's `cap_add=NET_ADMIN` gap, see that module's own backend.py) this backend needs to
    grant for the kill-switch to load its own ruleset. `QemuCellBackend.provision` still refuses a
    VPN_TOR spec whose image is not `"night-veil-ubuntu"` before ever reaching here, mirroring
    Docker's own defensive check.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Called by
    `hivemind.hive.backends.qemu.backend.QemuCellBackend`. Calls into hivemind.hive.models
    (NetworkPolicy) and hivemind.hive.backends.bootstrap (QueenEndpoint) only.

Key invariants:
    - The returned `queen_waggle_url` is always reachable from inside the guest under the returned
      `netdev_arg`: the control link (ADR-0027) must work under every network policy.
    - VPN_TOR's own network plan is built exactly like EGRESS_ONLY's: QEMU gives unrestricted
      outbound reach either way, and the difference between the two tiers is entirely inside the
      guest, not in what this module asks QEMU to create (mirrors `hivemind.hive.backends.docker.
      network`'s own VPN_TOR invariant).

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why the control
      link must survive every network policy.
    - hivemind.hive.models for NetworkPolicy and VirtualCellSpec.
    - hivemind.hive.backends.docker.network for the Docker backend's own reading of the same enum,
      whose shape and "what each policy enforces" convention this module mirrors.
    - hivemind.hive.backends.qemu.backend for QemuCellBackend, this module's one caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec

# QEMU's own alias for "the host machine", inside user-mode networking's private 10.0.2.0/24; the
# QEMU counterpart of Docker's host.docker.internal, usable only while restrict is off (see the
# module docstring's NONE-policy explanation for why restrict=on blocks it too).
USER_NET_HOST_ALIAS = "10.0.2.2"

# A fixed, otherwise-unused address in the same private /24, reserved for the one guestfwd
# exception restrict=on still honours (see the module docstring's NONE-policy explanation). Safe
# to reuse for every Cell: each QEMU process's user-mode network is private to that one process.
GUEST_CONTROL_ADDR = "10.0.2.100"
GUEST_CONTROL_PORT = 8710  # Arbitrary but fixed; never a real listener, only a guestfwd target.

# Loopback hostnames a Cell cannot dial directly from inside a QEMU guest: the same "the Queen
# listens on the host's own loopback" case Docker's network module rewrites for the same reason.
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

# TODO(5.7a): QEMU's own SLIRP stack cannot restrict outbound reach to a named allowlist of hosts;
# the in-Cell firewall Night Veil's kill-switch work adds (roadmap step 5.7a) is what will actually
# enforce this label's contents. Until then this is audit metadata only, mirroring Docker's own
# ALLOWLIST_LABEL.
ALLOWLIST_LABEL = "hivemind.network_allowlist"

__all__ = [
    "ALLOWLIST_LABEL",
    "GUEST_CONTROL_ADDR",
    "GUEST_CONTROL_PORT",
    "USER_NET_HOST_ALIAS",
    "QemuNetworkPlan",
    "plan_network",
]


@dataclass(frozen=True, slots=True)
class QemuNetworkPlan:
    """What `QemuCellBackend.provision` needs to build the VM's `-netdev` arg and its own env.

    Attributes:
        netdev_arg: The full `-netdev` option value, e.g. `"user,id=net0"` or
            `"user,id=net0,restrict=on,guestfwd=tcp:10.0.2.100:8710-cmd:..."`.
        queen_waggle_url: The URL the Cell's own `HIVEMIND_QUEEN_WAGGLE_URL` must carry so the
            control link actually reaches the Queen under `netdev_arg` -- not always
            `endpoint.waggle_url` unchanged; see the module docstring.
        relay_target: `(host, port)` the real Queen listens on, or `None` unless a guestfwd relay
            needs it (NONE policy only); `hivemind.hive.backends.qemu.process_runner` uses this to
            build the relay's own `cmd:` value.
    """

    netdev_arg: str
    queen_waggle_url: str
    relay_target: tuple[str, int] | None = None


def plan_network(spec: VirtualCellSpec, endpoint: QueenEndpoint) -> QemuNetworkPlan:
    """Decide the QEMU-level network for one Cell, per its `network_policy`.

    Args:
        spec: The Cell's own request; only `network_policy` is read.
        endpoint: Where the Queen is, as the composition root configured it; rewritten onto a
            QEMU-reachable address when needed (see the module docstring).

    Returns:
        A QemuNetworkPlan ready for the VM's `-netdev` argument and its own environment.
    """
    parsed = urlsplit(endpoint.waggle_url)
    if spec.network_policy is NetworkPolicy.NONE:
        return _plan_none(parsed)
    # EGRESS_ONLY, ALLOWLIST and VPN_TOR: full outbound NAT, so the ordinary host-loopback rewrite
    # (if any) is all the control link needs; ALLOWLIST's own entries are audit-only (TODO(5.7a)
    # above) and VPN_TOR's own restriction is enforced inside the guest, not here (module
    # docstring's VPN_TOR entry).
    return QemuNetworkPlan(netdev_arg="user,id=net0", queen_waggle_url=_reachable_url(parsed))


def _plan_none(parsed: SplitResult) -> QemuNetworkPlan:
    """Build the restrict=on plus guestfwd plan for NetworkPolicy.NONE (see module docstring)."""
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise ValueError(
            f"queen endpoint {parsed.geturl()!r} must carry an explicit host and port for the "
            "NONE network policy's guestfwd relay to target."
        )
    guest_addr = f"{GUEST_CONTROL_ADDR}:{GUEST_CONTROL_PORT}"
    # The guestfwd target ("cmd:...") is filled in by process_runner.py, which owns the actual
    # relay command; this module only reserves the guest-visible address and port.
    netdev = f"user,id=net0,restrict=on,guestfwd=tcp:{guest_addr}-cmd:"
    reachable = urlunsplit(parsed._replace(netloc=guest_addr))
    return QemuNetworkPlan(netdev_arg=netdev, queen_waggle_url=reachable, relay_target=(host, port))


def _reachable_url(parsed: SplitResult) -> str:
    """Rewrite a loopback host onto QEMU's own host alias; leave every other URL unchanged."""
    if parsed.hostname not in _LOOPBACK_HOSTNAMES:
        return urlunsplit(parsed)  # Already reachable through plain outbound NAT.
    port = parsed.port
    netloc = USER_NET_HOST_ALIAS if port is None else f"{USER_NET_HOST_ALIAS}:{port}"
    return urlunsplit(parsed._replace(netloc=netloc))
