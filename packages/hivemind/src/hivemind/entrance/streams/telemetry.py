"""Provide TelemetryBoard: every Heartbeat the Queen receives, kept per Warden and fanned out live.

A Heartbeat (a Warden's periodic self-report, with one row per sub-bee) never reaches the
Pheromone Trail, so the stream hub cannot follow it (ADR-0032: "Telemetry and episode records come
from the same hub through hooks the composition root wires"). The Queen hands every Heartbeat she
records to ``QueenDeps.on_heartbeat``; the composition root sets that hook to a board's ``record``.
The board keeps each Warden's newest sample (the Wardens read and the Cell pages read how many
sub-bees each runs) and offers every sample to its subscriptions, the telemetry view's, each a
bounded ``StreamSubscription`` closed with FELL_BEHIND when its reader lags, exactly like the
hub's. ``record`` is synchronous and never waits: it runs inside the Queen's tick.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Built by the
    composition root (``hive serve``, the test rig) before the Queen, so her hook is set from her
    first tick; read by the census and the telemetry view. Calls into the subscription beside it
    and waggle's Heartbeat only.

Key invariants:
    - ``record`` never awaits and never raises into the Queen's tick.
    - The newest sample per Warden replaces the one before it; nothing else is kept.

See Also:
    - hivemind.queen.ticks.liveness for where the Queen calls the hook.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from hivemind.entrance.streams.subscription import DEFAULT_BACKLOG, StreamSubscription
from waggle.ids import WardenId
from waggle.messages.supervision import Heartbeat

__all__ = ["TelemetryBoard", "TelemetrySample"]


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    """One Heartbeat as the Queen received it.

    Attributes:
        warden_id: The Warden that sent it.
        heartbeat: The Heartbeat: the Warden's own telemetry and one row per sub-bee.
        at: When the Queen received it.
    """

    warden_id: WardenId
    heartbeat: Heartbeat
    at: datetime


class TelemetryBoard:
    """Each Warden's newest Heartbeat, and a live feed of every one to the telemetry views."""

    def __init__(self) -> None:
        """Start with no sample and no subscriber."""
        # Mutated only on the event loop's thread: by the Queen's tick and the views' tasks.
        self._latest: dict[WardenId, TelemetrySample] = {}
        self._subscriptions: set[StreamSubscription[TelemetrySample]] = set()

    @property
    def subscribers(self) -> int:
        """How many subscriptions are open."""
        return len(self._subscriptions)

    def record(self, warden_id: WardenId, heartbeat: Heartbeat, at: datetime) -> None:
        """Keep a Heartbeat as the Warden's newest and offer it to every subscriber.

        The Queen's ``on_heartbeat`` hook: synchronous, so it never holds up her tick.

        Args:
            warden_id: The Warden that sent it.
            heartbeat: The Heartbeat.
            at: When she received it.
        """
        sample = TelemetrySample(warden_id, heartbeat, at)
        self._latest[warden_id] = sample
        # A subscriber past its backlog closes itself; the others still get the sample.
        for subscription in tuple(self._subscriptions):
            subscription.offer(sample)

    def latest(self) -> Mapping[WardenId, TelemetrySample]:
        """Return each Warden's newest sample: a read-only snapshot.

        Returns:
            The newest sample per Warden that has sent one.
        """
        return types.MappingProxyType(dict(self._latest))

    def subscribe(
        self, name: str, backlog: int = DEFAULT_BACKLOG
    ) -> StreamSubscription[TelemetrySample]:
        """Open a subscription to every sample from now on.

        Args:
            name: What subscribes, for the log.
            backlog: How far it may fall behind before it is closed.

        Returns:
            The subscription; close it when done.
        """
        subscription: StreamSubscription[TelemetrySample] = StreamSubscription(
            name, _every_sample, backlog, self._subscriptions.discard
        )
        self._subscriptions.add(subscription)
        return subscription


def _every_sample(sample: TelemetrySample) -> bool:
    """Accept every sample; a view filters by Warden itself."""
    return True
