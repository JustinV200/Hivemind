"""Decide what an approval may grant: the device ceiling rule and the steward rule, as pure checks.

An approved device's ``CapabilitySet`` is both its route ceiling (which Landing Board routes it may
call) and its goals' ceiling (what work those goals may do), so what an approval grants is the
most security-relevant choice in enrolment (ADR-0031, ADR-0033). Two rules bound it. The operator,
approving on loopback, may grant anything inside the Guard policy's ``device`` role ``allow``
list (the device ceiling) and nothing beyond it; naming nothing grants the role's ``proposed``
list, which leaves out stewardship and Night Veil. A steward device (``[entrance]
steward_devices``, holding ``entrance:steward``), approving remotely after full step-up, may grant
at most its own set intersected with that ceiling, and never ``entrance:steward`` itself, so
stewardship can only ever be granted at the Hive Stand; nor may it grant more spend per day, or a
longer life, than its own approval has (a grant never exceeds its grantor, ADR-0031). All are
pure functions (codingrules 8.3); the approval flow and the steward route call them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by
    ``hivemind.entrance.enrol.decisions.approve`` and, later, the steward route. Calls into
    ``hivemind.guard`` (the capability grammar, the policy's device role) and the enrolled-device
    model; no I/O.

Key invariants:
    - Nothing returned is wider than the device ceiling; nothing a steward returns is wider than
      the steward's own set, and never holds ``entrance:steward``.
    - A steward's approval never spends more per day, or lasts longer, than the steward's own.
    - A refusal names the first offending capability in sorted order, so the same request is
      always refused the same way.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the grammar and
      "a goal carries a ceiling".
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the steward rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import CapabilityCeilingError, StewardGrantError
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    GuardPolicy,
    proposed_set,
    role_set,
)
from hivemind.guard.policy import DEVICE_ROLE

# The capability that makes a device a steward; granted only by name, and only on loopback.
_STEWARD = Capability(family=CapabilityFamily.ENTRANCE_STEWARD)

__all__ = ["approval_grant", "device_ceiling", "steward_grant", "steward_terms"]


def device_ceiling(policy: GuardPolicy) -> CapabilitySet:
    """Return the device ceiling: the ``device`` role's ``allow`` list, which no approval exceeds.

    Args:
        policy: The Guard policy (the shipped one with the manifest's ``[guard]`` on top).

    Returns:
        The role's set; it names no scratch directory, so nothing is left to fill.

    Raises:
        GuardPolicyError: The policy's ``device`` role names ``{scratch}``, which no device has.
    """
    return role_set(policy, DEVICE_ROLE)


def approval_grant(policy: GuardPolicy, requested: Sequence[str] | None) -> CapabilitySet:
    """Return what an operator's approval grants: ``requested`` within the ceiling, or proposed.

    Args:
        policy: The Guard policy.
        requested: The capability strings the operator named; None when they named none.

    Returns:
        The ``device`` role's ``proposed`` set when ``requested`` is None, else ``requested``
        parsed, once every entry is inside the device ceiling.

    Raises:
        InvalidCapabilityError: A string is not a capability; the error names it.
        CapabilityCeilingError: A capability is beyond the device ceiling; the error names it.
    """
    if requested is None:
        return proposed_set(policy)
    granted = CapabilitySet.parse(*requested)
    ceiling = device_ceiling(policy)
    # Every entry must be something the ceiling itself allows; the first that is not is refused.
    for capability in _in_order(granted):
        if not ceiling.allows(capability):
            raise CapabilityCeilingError(str(capability))
    return granted


def steward_grant(
    steward: EnrolledDevice, requested: CapabilitySet, ceiling: CapabilitySet
) -> CapabilitySet:
    """Return ``requested`` if a steward device may grant it, else refuse (ADR-0033's rule).

    Args:
        steward: The device approving remotely; it must be APPROVED and hold
            ``entrance:steward``.
        requested: What it asks to grant the pending device.
        ceiling: The device ceiling (``device_ceiling``).

    Returns:
        ``requested``, unchanged, once every entry is held by the steward, inside the ceiling,
        and not ``entrance:steward``.

    Raises:
        StewardGrantError: The steward is not an approved steward, or an entry is stewardship
            itself, not held by the steward, or beyond the ceiling; the error names it.
        InvalidCapabilityError: The steward's own stored set no longer parses.
    """
    held = CapabilitySet.parse(*steward.capabilities)
    # Only an approved device flagged as a steward may approve at all.
    if steward.status is not DeviceStatus.APPROVED or not held.allows(_STEWARD):
        raise StewardGrantError(
            steward.id, None, "it is not an approved device holding entrance:steward"
        )
    # Each entry is checked against all three limits; the first that fails any is refused.
    for capability in _in_order(requested):
        reason = _steward_refusal(capability, held, ceiling)
        if reason is not None:
            raise StewardGrantError(steward.id, str(capability), reason)
    return requested


def steward_terms(
    steward: EnrolledDevice, spend_cap_usd_per_day: float, expires_at: datetime | None
) -> None:
    """Refuse the approval's other terms when they exceed the steward's own (ADR-0031, ADR-0033).

    ``steward_grant`` bounds what the pending device may do; this bounds how much it may spend a
    day and how long it lasts, so a steward can never mint a device that outspends or outlives
    its own approval. Anything more is granted at the Hive Stand.

    Args:
        steward: The approving steward, already admitted by ``steward_grant``.
        spend_cap_usd_per_day: The daily cap asked for the pending device.
        expires_at: Its asked expiry; None asks for no expiry.

    Raises:
        StewardGrantError: The cap is above the steward's own, or the steward's approval lapses
            and the asked one lapses later or never.
    """
    own_cap = steward.spend_cap_usd_per_day
    # A capped steward grants at most its own cap; only the console is uncapped (None).
    if own_cap is not None and spend_cap_usd_per_day > own_cap:
        reason = f"a daily cap above its own {own_cap:g} USD is granted only at the Hive Stand"
        raise StewardGrantError(steward.id, None, reason)
    own_expiry = steward.expires_at
    if own_expiry is not None and (expires_at is None or expires_at > own_expiry):
        reason = "an approval outlasting its own is granted only at the Hive Stand"
        raise StewardGrantError(steward.id, None, reason)


def _steward_refusal(
    capability: Capability, held: CapabilitySet, ceiling: CapabilitySet
) -> str | None:
    """Say why a steward may not grant ``capability``, or None when it may."""
    if capability.family is CapabilityFamily.ENTRANCE_STEWARD:
        return "stewardship is granted only at the Hive Stand"
    if not held.allows(capability):
        return "the steward does not hold it"
    if not ceiling.allows(capability):
        return "it is beyond the device ceiling"
    return None


def _in_order(capabilities: Iterable[Capability]) -> list[Capability]:
    """Return capabilities sorted by their string form, so refusals are deterministic."""
    return sorted(capabilities, key=str)
