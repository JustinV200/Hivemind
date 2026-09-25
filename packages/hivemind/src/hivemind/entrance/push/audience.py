"""Decide which enrolled devices a notice of each kind may reach: the push audience rules.

A push says only that something is waiting, but even that says something (a question exists, the
chat moved), so ADR-0042 limits who hears it to devices that may act on it or read it:
``question_waiting`` goes to devices holding ``entrance:answer``; ``reply_waiting`` only to devices
that may read the chat (``entrance:submit`` and ``honey:clearance:c2``); ``goal_completed`` only to
the device that submitted the goal; ``security_event`` and ``alarm_waiting`` to every approved
device except the one the event concerns (a device asking to join never hears about itself). Every
kind reaches only devices that are APPROVED and hold ``entrance:push``. ``withdrawn`` has no
audience of its own: it goes to exactly the recipients of the original
(``hivemind.entrance.push.dispatch.PushDispatcher.withdraw``). ``audience_for`` is pure, a table of
one rule per kind over the devices it is given, so the rules are tested without a store or a
socket, and the dispatcher only ever sends to an ``Audience`` this function built.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Called by
    whatever raises a notice (the inbox, the Alarm path, the chat, the enrolment flows) with the
    approved devices from ``hivemind.entrance.store``; its result is handed to
    ``PushDispatcher.push``. Calls into ``hivemind.guard`` (the capability grammar) and
    ``hivemind.entrance.enrol`` (the device record) only.

Key invariants:
    - A device that is not APPROVED, or does not hold ``entrance:push``, is never in an audience.
    - A device whose stored capabilities do not parse holds nothing here (it fails closed).
    - ``withdrawn`` never has an audience; ``goal_completed`` needs its submitting device.

See Also:
    - docs/adr/0042-landing-board-versioning-and-push.md, "Subscriptions are per device ...
      filtered by what the device may read".
    - hivemind.guard.capabilities for the capability grammar parsed here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice
from hivemind.entrance.push.models import NoticeKind
from hivemind.guard import Capability, CapabilityFamily, CapabilitySet, InvalidCapabilityError
from waggle.ids import DeviceId

PUSH_CAPABILITY = Capability(family=CapabilityFamily.ENTRANCE_PUSH)  # Needed to hear any push.
ANSWER_CAPABILITY = Capability(family=CapabilityFamily.ENTRANCE_ANSWER)  # May answer questions.
SUBMIT_CAPABILITY = Capability(family=CapabilityFamily.ENTRANCE_SUBMIT)  # May use the chat.
# The chat is personal (C2) by construction: reading a reply needs the top clearance rung.
CHAT_CLEARANCE_CAPABILITY = Capability(family=CapabilityFamily.HONEY_CLEARANCE, scope="c2")

__all__ = [
    "ANSWER_CAPABILITY",
    "CHAT_CLEARANCE_CAPABILITY",
    "PUSH_CAPABILITY",
    "SUBMIT_CAPABILITY",
    "Audience",
    "audience_for",
    "held_capabilities",
]


@dataclass(frozen=True, slots=True)
class Audience:
    """The devices one notice kind may reach, as ``audience_for`` decided them.

    Attributes:
        kind: The notice kind the rule was applied for; the dispatcher refuses to send a notice
            of another kind to it.
        device_ids: The devices that may hear it.
    """

    kind: NoticeKind
    device_ids: frozenset[DeviceId]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Who a notice is about: the device it concerns and the device that submitted the goal."""

    concerning: DeviceId | None
    submitter: DeviceId | None


def audience_for(
    kind: NoticeKind,
    devices: Iterable[EnrolledDevice],
    *,
    concerning: DeviceId | None = None,
    submitter: DeviceId | None = None,
) -> Audience:
    """Return the devices a notice of ``kind`` may reach, out of ``devices``.

    Args:
        kind: The notice kind.
        devices: The candidates, normally every device the Entrance tables hold; any that is not
            APPROVED or lacks ``entrance:push`` is skipped.
        concerning: For ``security_event`` and ``alarm_waiting``: the device the event is about,
            which is left out. None when it concerns no device.
        submitter: For ``goal_completed``: the device that submitted the goal.

    Returns:
        The audience for ``kind``.

    Raises:
        ValueError: ``kind`` is ``goal_completed`` and ``submitter`` is None.

    Example:
        >>> audience_for(NoticeKind.WITHDRAWN, []).device_ids
        frozenset()
    """
    if kind is NoticeKind.GOAL_COMPLETED and submitter is None:
        raise ValueError("A goal_completed notice goes to its submitting device; pass submitter.")
    rule = _RULES[kind]
    subjects = _Subjects(concerning=concerning, submitter=submitter)
    chosen: set[DeviceId] = set()
    # Each candidate must be approved, hold entrance:push, and pass the kind's own rule.
    for device in devices:
        if device.status is not DeviceStatus.APPROVED:
            continue
        held = held_capabilities(device)
        if held.allows(PUSH_CAPABILITY) and rule(device, held, subjects):
            chosen.add(device.id)
    return Audience(kind=kind, device_ids=frozenset(chosen))


def held_capabilities(device: EnrolledDevice) -> CapabilitySet:
    """Parse what a device holds, failing closed.

    Args:
        device: The device record; its capabilities are strings in ``hivemind.guard``'s grammar.

    Returns:
        The parsed set; an empty one when any stored string does not parse, so a corrupt record
        can never hear more than a clean one.
    """
    try:
        return CapabilitySet.parse(*device.capabilities)
    except InvalidCapabilityError:
        return CapabilitySet.empty()


def _may_answer(_device: EnrolledDevice, held: CapabilitySet, _subjects: _Subjects) -> bool:
    """Whoever may answer the human's inbox is told a question is waiting."""
    return held.allows(ANSWER_CAPABILITY)


def _may_read_chat(_device: EnrolledDevice, held: CapabilitySet, _subjects: _Subjects) -> bool:
    """A reply is chat content: only a device that may submit and read C2 hears of it."""
    return held.allows(SUBMIT_CAPABILITY) and held.allows(CHAT_CLEARANCE_CAPABILITY)


def _submitted(device: EnrolledDevice, _held: CapabilitySet, subjects: _Subjects) -> bool:
    """A finished goal concerns only the device that submitted it."""
    return device.id == subjects.submitter


def _not_concerned(device: EnrolledDevice, _held: CapabilitySet, subjects: _Subjects) -> bool:
    """Every device but the one the event is about (a joining device never hears of itself)."""
    return device.id != subjects.concerning


def _nobody(_device: EnrolledDevice, _held: CapabilitySet, _subjects: _Subjects) -> bool:
    """A withdrawal goes to the original's recipients only, never to a fresh audience."""
    return False


# One rule per kind: does this device, holding these capabilities, hear this kind of notice?
_Rule = Callable[[EnrolledDevice, CapabilitySet, _Subjects], bool]

_RULES: Mapping[NoticeKind, _Rule] = MappingProxyType(
    {
        NoticeKind.QUESTION_WAITING: _may_answer,
        NoticeKind.ALARM_WAITING: _not_concerned,
        NoticeKind.REPLY_WAITING: _may_read_chat,
        NoticeKind.GOAL_COMPLETED: _submitted,
        NoticeKind.SECURITY_EVENT: _not_concerned,
        NoticeKind.WITHDRAWN: _nobody,
    }
)
