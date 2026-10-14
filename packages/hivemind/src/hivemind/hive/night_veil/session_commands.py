"""Define the fixed, documented commands SessionNightVeilProbe runs, and how to judge each one.

Roadmap step 5.7b: "every command a named constant with a comment saying what a green result looks
like." Every genuinely fixed command (never varying between Hives) is a module-level constant, an
`argv` tuple `hivemind.cell.session.run` executes through a `CellSession` -- never a shell string
(codingrules section 15: argument lists, never `shell=True`). Three checks need one Hive-specific
value (the Hive Stand's own onion address, a direct-egress probe host, a metadata endpoint): those
build their own `argv` from a `SessionProbeConfig` at call time rather than being a constant, since
the flags and structure are fixed but the one target host is configuration, not a literal this
module could name in advance; each still documents what a green result looks like exactly like the
constants do. `SessionProbeConfig` groups every Hive-specific value `hive.night_veil.session_probe.
SessionNightVeilProbe` needs (codingrules section 5.1's parameter limit: one value object instead
of five-plus keyword arguments).

Three checks conclude from more than one read, or from more than a word in the output, and their
judges are in `hive.night_veil.judges`: the DNS check reads the resolver file, both routing tables
and Tor's configuration (`DNS_COMMANDS`, the Propolis tier's own reads plus the torrc, so the two
tiers place a Cell's resolver by the same table), the hostname check reads the kernel's hostname
(`CMD_HOSTNAME`), and the WebRTC check reads the browser profile's two preference files
(`WEBRTC_COMMANDS`). They are all `cat`: fixed, read-only, and a file that cannot be read makes its
check red rather than green.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Called only by `hive.night_veil.
    session_probe.SessionNightVeilProbe`, which owns the `CellSession` these commands run on.
    Calls into `hivemind.cell` (CompletedCommand, OsFamily), `hivemind.cell.tunnel` (the Propolis
    checks' reads) and `hive.night_veil.results` (CheckResult, CheckStatus) only.

Key invariants:
    - Every `_judge_*` function is pure: given the same `CompletedCommand`, it always returns the
      same `CheckResult`.
    - `CheckResult.detail` never carries a full command transcript, only a short fact (an exit
      code, a matched or missing substring) -- codingrules section 12: "log identifiers and sizes."
    - Nothing here is ever built from a request, a model or anything the Cell printed: the only
      variable parts of a command are the Hive's own configuration (`SessionProbeConfig`).

See Also:
    - .claude/roadmap.md step 5.7b for "every command a named constant... a green result looks
      like."
    - .claude/codingrules.md section 15 for "subprocess calls use argument lists, never shell=True."
    - hivemind.hive.night_veil.session_probe for SessionNightVeilProbe, this module's one caller.
    - hivemind.hive.night_veil.results for CheckResult and CheckStatus.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CompletedCommand, OsFamily
from hivemind.cell.tunnel import COMMANDS as TUNNEL_COMMANDS
from hivemind.cell.tunnel import ProbeCommand
from hivemind.hive.night_veil.results import CheckResult, CheckStatus

__all__ = [
    "CMD_DEFAULT_ROUTE",
    "CMD_GEOLOCATION_SERVICE",
    "CMD_HOSTNAME",
    "CMD_KILL_SWITCH",
    "CMD_LOCALE",
    "CMD_TIMEZONE",
    "CMD_TORRC",
    "CMD_TOR_BROWSER",
    "CMD_TOR_HEALTHY",
    "CMD_WAGGLE_SOCKET",
    "DNS_COMMANDS",
    "TOR_BROWSER_PROFILE",
    "WEBRTC_COMMANDS",
    "SessionProbeConfig",
    "direct_egress_cmd",
    "hidden_service_cmd",
    "judge_contains",
    "judge_exit_zero",
    "judge_fails",
    "metadata_cmd",
]

# ── Fixed commands: never vary between Hives ──────────────────────────────────────────────────
CMD_KILL_SWITCH = ("nft", "list", "ruleset")  # Green: exit 0 and a "policy drop" default line.
CMD_DEFAULT_ROUTE = ("ip", "route", "show", "default")  # Green: the route names "tun0".
CMD_TOR_HEALTHY = ("systemctl", "is-active", "tor")  # Green: exit 0, stdout "active".
CMD_TOR_BROWSER = ("which", "tor-browser")  # Green: exit 0, an install path on stdout.
# Green: an ESTABlished peer whose local address is loopback on the SOCKS port, never the tun0
# interface -- the Waggle client's own control link must never touch the VPN tunnel (codingrules
# section 8.7: "Night Veil's control channel has no clearnet destination").
CMD_WAGGLE_SOCKET = ("ss", "-tnp")
# Tor's configuration, as the image baked it (world-readable, never edited at runtime): the DNS
# check reads its `DNSPort` to know whether a loopback resolver is Tor's own. Green is not this
# command's to say; `judges.resolver.judge_resolver` places every resolver from it and the reads
# below.
CMD_TORRC = ("cat", "/etc/tor/torrc")
# Where the Cell's resolver is: the Propolis tunnel checks' own reads (the IPv4 and IPv6 routing
# tables and /etc/resolv.conf, taken from their table so the two tiers cannot disagree on what a
# Cell's DNS is) and Tor's configuration. Green: every nameserver is Tor's DNS port on loopback or
# an address a packet to which leaves by a tunnel interface; a lookup that can go anywhere else is
# a leak, and so is a file that could not be read.
DNS_COMMANDS = (*TUNNEL_COMMANDS[OsFamily.LINUX], ProbeCommand("torrc", CMD_TORRC))
CMD_TIMEZONE = ("timedatectl", "show", "--property=Timezone", "--value")  # Green: stdout "UTC".
CMD_LOCALE = ("locale",)  # Green: a LANG= line matching config.locale_profile.
# The kernel's own hostname (the value `hostname` prints), read where it lives so no binary is
# needed. Green: `night-veil-` and eight hex digits, neither an image's default nor the Hive
# Stand's.
CMD_HOSTNAME = ("cat", "/proc/sys/kernel/hostname")
# Green: no location-service daemon is running (defense in depth -- a Virtual Cell has no GPS
# hardware to begin with, but a rogue location service is still worth refusing outright).
CMD_GEOLOCATION_SERVICE = ("pgrep", "-x", "gpsd")
# Where the image keeps Tor Browser's profile (`images/night-veil-ubuntu/Dockerfile` unpacks the
# browser under /opt/tor-browser and bakes `user.js` into this directory); the WebRTC check reads
# its two preference files.
TOR_BROWSER_PROFILE = "/opt/tor-browser/Browser/TorBrowser/Data/Browser/profile.default"
# The profile's two preference files, read as text. Green: `user.js` (read last by the browser, so
# it wins) or else `prefs.js` sets `media.peerconnection.enabled` to false, which `judges.webrtc.
# judge_webrtc` decides. A file the profile does not have reads empty rather than failing the read.
WEBRTC_COMMANDS = (
    ProbeCommand("user_js", ("cat", f"{TOR_BROWSER_PROFILE}/user.js")),
    ProbeCommand("prefs_js", ("cat", f"{TOR_BROWSER_PROFILE}/prefs.js")),
)


@dataclass(frozen=True, slots=True)
class SessionProbeConfig:
    """The Hive-specific facts SessionNightVeilProbe needs to build its own commands.

    Attributes:
        hidden_service_address: The Hive Stand's own Tor hidden-service (.onion) address.
        socks_proxy_port: The loopback Tor SOCKS proxy port every check routes through.
        locale_profile: The fixed locale Night Veil pins every Cell to.
        direct_egress_probe_host: A plain host `direct_egress_blocked` dials with no proxy at all;
            the kill-switch must refuse it. Defaults to a well-known public resolver.
        metadata_addresses: Cloud metadata hosts `metadata_unreachable` must find unreachable.
        stand_hostname: The Hive Stand's own hostname, which `randomized_hostname` finds a Cell
            must not carry; empty when not known, and then only the random shape is judged.
    """

    hidden_service_address: str
    socks_proxy_port: int
    locale_profile: str
    direct_egress_probe_host: str = "1.1.1.1"
    metadata_addresses: tuple[str, ...] = ("169.254.169.254",)
    stand_hostname: str = ""


def hidden_service_cmd(config: SessionProbeConfig) -> tuple[str, ...]:
    """Build the curl-through-Tor command `hidden_service_reachable` runs.

    Green: exit 0 and exactly `426` on stdout (`judges.hidden_service.WEBSOCKET_PROBE_STATUS`):
    a plain GET to the Hive Stand's WebSocket listener is answered `Upgrade Required`, so that
    status through the SOCKS proxy is the Hive Stand's own hidden service answering. Any other
    status, or none, is a different service or no service.
    """
    target = f"http://{config.hidden_service_address}"
    proxy = f"127.0.0.1:{config.socks_proxy_port}"
    return (
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--socks5-hostname",
        proxy,
        target,
    )


def direct_egress_cmd(config: SessionProbeConfig) -> tuple[str, ...]:
    """Build the direct (no proxy) curl command `direct_egress_blocked` runs.

    Green: the command FAILS (non-zero exit, connection refused or timed out) -- the kill-switch
    must refuse a request that never goes through the tunnel or Tor at all.
    """
    return ("curl", "-s", "--max-time", "5", f"http://{config.direct_egress_probe_host}")


def metadata_cmd(config: SessionProbeConfig) -> tuple[str, ...]:
    """Build the curl command `metadata_unreachable` runs against the first metadata address.

    Green: the command FAILS -- the image's own null route makes the endpoint unreachable.
    """
    return ("curl", "-s", "--max-time", "3", f"http://{config.metadata_addresses[0]}/")


def judge_exit_zero(completed: CompletedCommand, *, ok_detail: str) -> CheckResult:
    """Pass iff `completed.exit_code == 0`; the common case for a positive presence/state check."""
    passed = completed.exit_code == 0
    detail = ok_detail if passed else f"exit_code={completed.exit_code}"
    return CheckResult(status=CheckStatus.PASS if passed else CheckStatus.FAIL, detail=detail)


def judge_contains(completed: CompletedCommand, needle: str, *, ok_detail: str) -> CheckResult:
    """Pass iff `completed` exited 0 and `needle` appears in its stdout."""
    stdout = completed.stdout.decode("utf-8", errors="replace")
    passed = completed.exit_code == 0 and needle in stdout
    detail = ok_detail if passed else f"exit_code={completed.exit_code}, {needle!r} not found"
    return CheckResult(status=CheckStatus.PASS if passed else CheckStatus.FAIL, detail=detail)


def judge_fails(completed: CompletedCommand, *, ok_detail: str) -> CheckResult:
    """Pass iff `completed.exit_code != 0`: for a probe that must be refused to be green."""
    passed = completed.exit_code != 0
    detail = ok_detail if passed else f"unexpectedly succeeded, exit_code={completed.exit_code}"
    return CheckResult(status=CheckStatus.PASS if passed else CheckStatus.FAIL, detail=detail)
