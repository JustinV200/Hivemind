"""Tests for hivemind.entrance.enrol.deps.notifier: the security notice and its no-op notifier.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/deps/notifier.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.deps.notifier for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.entrance.enrol import NullSecurityNotifier, SecurityNotice, SecurityNotifier
from waggle.ids import DeviceId, EventId

_NOTICE = SecurityNotice(
    DeviceId("device_01M221E4C10R4XDPNQNRX85AAA"),
    EventId("event_01M221E4C10R4XDPNQNRX85AAA"),
    "guard.entrance_pending",
    datetime(2026, 9, 24, tzinfo=UTC),
)


async def test_the_no_op_notifier_accepts_a_notice_and_keeps_nothing() -> None:
    notifier: SecurityNotifier = NullSecurityNotifier()

    await notifier.notify(_NOTICE)

    assert vars(notifier) == {}


def test_a_notice_carries_identifiers_only() -> None:
    assert (_NOTICE.device_id, _NOTICE.event_id, _NOTICE.kind) == (
        "device_01M221E4C10R4XDPNQNRX85AAA",
        "event_01M221E4C10R4XDPNQNRX85AAA",
        "guard.entrance_pending",
    )
