"""Refuse what a Night Veil task may never do: the tier's floors (roadmap steps 10.3a and 10.3d).

A task whose bound or requested tier is `NIGHT_VEIL`, or any action on a Night Veil Cell, gets
these floors whatever its set says (ADR-0031): it is placed only on a fresh Virtual Cell, so a
`cell:hive_stand` or `cell:real:*` need is refused (`night_veil_virtual_only`; holding
`cell:virtual` is left to the held set); every model it binds must be local, in the binding
process or served on the Cell itself, never hosted and never the Hive Stand's
(`night_veil_local_slots`); it touches Honey at `c0` and `c1` and never `c2`
(`night_veil_clearance`); it is location-blind, so `geo`, `wifi:scan` and `host:metadata` are
never granted and a cloud metadata endpoint is never reachable by `net`
(`night_veil_location`); and a Cell provisioned for it dials the Queen only over a v3 `.onion`
hidden service (`waggle.uris.is_onion_service_host`, the rule the Cell's own transport dials by)
through the Tor SOCKS proxy on its own loopback, never the VPN interface or a clearnet address
(`night_veil_control_link`, checked when the enforcement point states the link).
Each floor reads the request's context (`PolicyContext`), never the held set.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Run by `hivemind.guard.policy.floors.chain`. Calls into `hivemind.cell` (the tier enums),
    `hivemind.guard.capabilities`, `hivemind.guard.net`, this package's `refusal`, the policy
    package's `facts`, `models` and `table`, and `waggle.uris` (the onion service rule).

Key invariants:
    - Applies only when `is_night_veil(context)`: the Cell's tier or the task's bound or
      requested tier is NIGHT_VEIL. Every other action passes through untouched.
    - An `llm` need is a binding, so an unknown `binding_local` is refused, never assumed local.
    - Refuses and never allows.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the tier's boundary.
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the floors.
    - hivemind.guard.policy.floors.initiation for who may start Night Veil work at all.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.guard.capabilities import Capability, CapabilityFamily
from hivemind.guard.net import (
    ip_literal,
    is_loopback_name,
    is_metadata_address,
    is_metadata_host,
    normalise_host,
)
from hivemind.guard.policy.facts import ControlLink
from hivemind.guard.policy.floors.refusal import FloorRefusal, tier
from hivemind.guard.policy.models import PolicyContext, PolicyRequest
from hivemind.guard.policy.table import GuardPolicy
from waggle.uris import ONION_SUFFIX as WAGGLE_ONION_SUFFIX
from waggle.uris import is_onion_service_host

VIRTUAL_ONLY_FLOOR = "night_veil_virtual_only"  # Never the Hive Stand, never a Real Cell.
LOCAL_SLOTS_FLOOR = "night_veil_local_slots"  # Every binding local: never hosted or Hive Stand.
CLEARANCE_FLOOR = "night_veil_clearance"  # Honey at c0 and c1 only.
LOCATION_FLOOR = "night_veil_location"  # geo, wifi:scan, host:metadata, metadata endpoints.
CONTROL_LINK_FLOOR = "night_veil_control_link"  # Waggle over Tor to a .onion hidden service.
# The capability families that reveal where a Cell is (ADR-0031's location families).
LOCATION_FAMILIES = frozenset(
    {CapabilityFamily.GEO, CapabilityFamily.WIFI_SCAN, CapabilityFamily.HOST_METADATA}
)
# A Tor hidden service's top-level name: reachable only through Tor (waggle.uris owns the rule).
ONION_SUFFIX = WAGGLE_ONION_SUFFIX
# SOCKS schemes that resolve the name through the proxy: a .onion name never meets local DNS.
REMOTE_DNS_SOCKS_SCHEMES = frozenset({"socks5h", "socks4a"})
_HIGHEST_NIGHT_VEIL_CLEARANCE = HoneyClearance.C1  # ADR-0030: never c2 on a Night Veil Cell.

__all__ = [
    "CLEARANCE_FLOOR",
    "CONTROL_LINK_FLOOR",
    "LOCAL_SLOTS_FLOOR",
    "LOCATION_FAMILIES",
    "LOCATION_FLOOR",
    "ONION_SUFFIX",
    "REMOTE_DNS_SOCKS_SCHEMES",
    "VIRTUAL_ONLY_FLOOR",
    "is_night_veil",
    "night_veil_floor",
]


def is_night_veil(context: PolicyContext) -> bool:
    """Return whether an action happens under Night Veil: on such a Cell, or for such a task.

    Args:
        context: Where the action happens.

    Returns:
        True when the Cell's tier, or the task's bound or requested tier, is NIGHT_VEIL.
    """
    return CombShieldLevel.NIGHT_VEIL in (context.comb_shield, context.bound_tier)


def night_veil_floor(request: PolicyRequest, policy: GuardPolicy) -> FloorRefusal | None:
    """Refuse a Night Veil action the tier forbids, whatever the principal holds.

    Args:
        request: The action: its needed capability and context are read.
        policy: Unused: every Night Veil floor is fixed by ADR-0030, never configured.

    Returns:
        A `guard.tier_floor.night_veil_<floor>` refusal, or None when none applies.
    """
    del policy  # ADR-0030 fixes these floors; nothing in [guard] may loosen them.
    if not is_night_veil(request.context):
        return None
    # Each check is one floor; the first that refuses decides, in ADR-0031's own order.
    for check in _CHECKS:
        refusal = check(request.needed, request.context)
        if refusal is not None:
            return refusal
    return None


def _virtual_only(needed: Capability, context: PolicyContext) -> FloorRefusal | None:
    """Refuse placing Night Veil work on the Hive Stand or any other Real Cell."""
    del context  # The need alone says whether the Cell would be Real.
    if needed.family is CapabilityFamily.CELL_HIVE_STAND:
        where = "the Hive Stand"
    elif needed.family is CapabilityFamily.CELL_REAL:
        where = f"Real Cell {needed.scope}"
    else:
        return None
    return tier(
        VIRTUAL_ONLY_FLOOR, f"a Night Veil task runs only on a fresh Virtual Cell, never {where}"
    )


def _local_slots(needed: Capability, context: PolicyContext) -> FloorRefusal | None:
    """Refuse a Night Veil model binding that is not shown to be local."""
    if needed.family is not CapabilityFamily.LLM or context.binding_local is True:
        return None
    # An `llm` need is always a binding, so an unstated locality fails closed.
    shown = (
        "is hosted or served off the Cell"
        if context.binding_local is False
        else "was not shown local"
    )
    return tier(
        LOCAL_SLOTS_FLOOR,
        f"a Night Veil task binds only local models (in process, or served on its own Cell), and "
        f"this binding {shown}",
    )


def _clearance(needed: Capability, context: PolicyContext) -> FloorRefusal | None:
    """Refuse Night Veil Honey access above c1 (ADR-0030's clearance boundary)."""
    del context  # The clearance a need names is the whole question.
    if needed.family is not CapabilityFamily.HONEY_CLEARANCE:
        return None
    if HoneyClearance[needed.scope.upper()].rank <= _HIGHEST_NIGHT_VEIL_CLEARANCE.rank:
        return None
    return tier(CLEARANCE_FLOOR, "a Night Veil task touches Honey at c0 and c1 only, never c2")


def _location(needed: Capability, context: PolicyContext) -> FloorRefusal | None:
    """Refuse a location family, and `net` to a cloud metadata endpoint, under Night Veil."""
    if needed.family in LOCATION_FAMILIES:
        what = needed.family.value
    elif needed.family is CapabilityFamily.NET and _reaches_metadata(needed.scope, context):
        what = "a cloud metadata endpoint"
    else:
        return None
    return tier(LOCATION_FLOOR, f"a Night Veil Cell is location-blind, so {what} is never granted")


def _reaches_metadata(scope: str, context: PolicyContext) -> bool:
    """Return whether a `net` scope, or any address it resolved to, is a metadata endpoint."""
    resolved = context.resolved_addresses or ()
    return is_metadata_host(scope) or any(is_metadata_address(address) for address in resolved)


def _control_link(needed: Capability, context: PolicyContext) -> FloorRefusal | None:
    """Refuse a Night Veil Cell any control link but a .onion service through a loopback SOCKS."""
    del needed  # The link is stated on the context by the point provisioning the Cell.
    link = context.control_link
    if link is None:
        return None  # This action provisions no Cell, so no link is being chosen.
    problem = _link_problem(link)
    if problem is None:
        return None
    return tier(
        CONTROL_LINK_FLOOR,
        "a Night Veil Cell dials the Queen only at a .onion hidden service through the Tor SOCKS "
        f"proxy on its own loopback (socks5h://), but {problem}",
    )


def _link_problem(link: ControlLink) -> str | None:
    """Say what is wrong with a control link for a Night Veil Cell, or None when nothing is."""
    # The very address rule the Cell's own transport dials by: a well-formed v3 onion service.
    if not is_onion_service_host(normalise_host(link.host)):
        return "its link dials a host that is not a v3 onion service"
    if link.socks_proxy_url is None:
        return "its link dials directly, with no SOCKS proxy"
    parts = urlsplit(link.socks_proxy_url)
    if parts.scheme.lower() not in REMOTE_DNS_SOCKS_SCHEMES:
        return "its proxy is not a SOCKS proxy that resolves names itself"
    host = parts.hostname or ""
    literal = ip_literal(host)
    on_loopback = is_loopback_name(host) or (literal is not None and literal.is_loopback)
    return None if on_loopback else "its SOCKS proxy is off the Cell's own loopback"


# ADR-0031's order for the Night Veil floors: where it runs, what it binds, what it reads, what it
# may learn about its place, and how it talks home.
_CHECKS: tuple[Callable[[Capability, PolicyContext], FloorRefusal | None], ...] = (
    _virtual_only,
    _local_slots,
    _clearance,
    _location,
    _control_link,
)
