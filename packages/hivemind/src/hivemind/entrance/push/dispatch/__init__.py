"""Dispatch push notices: register subscriptions, push to an audience, withdraw everywhere.

The push channel's entry point (ADR-0042), split by responsibility: ``dispatcher`` holds
``PushDispatcher`` (register, push, withdraw, forget a device, re-validate at start); ``courier``
delivers one notice to many destinations concurrently and settles the outcomes (delete what is
gone, record what arrived); ``refs`` keeps the in-memory bookkeeping per ref (one lock so a push
and its withdrawal never overlap, and which devices a ref reached over a live socket).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Built by the
    Entrance's composition root; called by the subscribe route, by whatever raises a notice and
    by device state changes. Calls into the push channels, the registration gate and the
    subscription store.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No channel's failure stops another channel's delivery.

See Also:
    - docs/adr/0042-landing-board-versioning-and-push.md for the decisions.

Public API:
    - PushDispatcher, MAX_SUBSCRIPTIONS_PER_DEVICE, MAX_LIVE_REFS: the entry point.
    - PushChannels, PushReport, Courier: what it delivers through, what it reports, and the
      concurrent delivery underneath.
    - RefLocks, LiveRecipients: the per-ref bookkeeping.
"""

from hivemind.entrance.push.dispatch.courier import Courier, PushChannels, PushReport
from hivemind.entrance.push.dispatch.dispatcher import (
    MAX_LIVE_REFS,
    MAX_SUBSCRIPTIONS_PER_DEVICE,
    PushDispatcher,
)
from hivemind.entrance.push.dispatch.refs import LiveRecipients, RefLocks

__all__ = [
    "MAX_LIVE_REFS",
    "MAX_SUBSCRIPTIONS_PER_DEVICE",
    "Courier",
    "LiveRecipients",
    "PushChannels",
    "PushDispatcher",
    "PushReport",
    "RefLocks",
]
