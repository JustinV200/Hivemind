"""Provide PollenWatchProbe: look at an idle device through its Pollen Packet, read-only.

A Real Cell's Warden (the always-on supervisor of one Cell) with no bees working enters WATCH: it
observes the device without touching it and, on a schedule, reviews what it saw (ADR-0054). Where
the Warden runs on the Hive Stand and only the device's Pollen Packet (the thin gateway installed
on it) is on the device, it cannot run commands to look, because there is no lease to run them in.
`PollenWatchProbe` instead asks the packet with a `swarm.observe`, which names the kinds of
observation wanted (the process list, resource use, log lines and file changes in named roots),
the roots to read inside, the cursor for file changes and a bound on the items returned. The packet
answers from fixed collectors of its own, so nothing a request says can make it run or read
anything beyond the four kinds and the roots, and it checks the Warden holds `watch:<cell_id>` and
`fs:read` on each root before it answers. The probe returns the `Observation` the packet sent, once
it has checked that it describes this device. Everything in it describes the operator's machine and
is the most sensitive label on arrival (`C2`); this module neither logs nor records any of it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Built by the
    composition root for each Level 0 device Warden and used by its watch observer
    (`hivemind.wardens.watch`, a later step). Calls into the gateway hub and link and waggle's
    swarm messages.

Key invariants:
    - The probe only asks: it sends one `swarm.observe`, as the device's Warden, and returns the
      reply; it never writes, runs a command or needs a lease.
    - A reply about another device, or anything but an `Observation`, is an error and not data.
    - Nothing in an observation is logged or put on the trail here.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode.
    - docs/waggle/gateway-contract.md section 9.4 for what the packet answers.
    - hivemind.guard.watch for the observations watch mode may ask for.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime

from hivemind.swarm.errors import DeviceRequestError, GatewayUnavailableError
from hivemind.swarm.gateway.hub import GatewayHub
from hivemind.swarm.models import SwarmNode
from waggle.messages.swarm import Observation, ObservationKind, Observe
from waggle.messages.swarm.watch import DEFAULT_OBSERVE_ITEMS

# Seconds to wait for the packet's one answer: its collectors read kernel tables and small files.
OBSERVE_REPLY_TIMEOUT_S = 60.0

OBSERVE_REASON = "A scheduled watch sample of an idle device."
MISMATCH_CODE = "hive.swarm.observation_mismatch"

__all__ = ["MISMATCH_CODE", "OBSERVE_REASON", "OBSERVE_REPLY_TIMEOUT_S", "PollenWatchProbe"]


class PollenWatchProbe:
    """Observe one Level 0 device by asking its packet; read-only by construction."""

    def __init__(
        self, node: SwarmNode, hub: GatewayHub, max_items: int = DEFAULT_OBSERVE_ITEMS
    ) -> None:
        """Build the probe for one device.

        Args:
            node: The device; its Warden id is the sender and its device id is who is asked.
            hub: Where the device's live link is found.
            max_items: The most items to ask for in each of the process list, the file changes
                and the log lines.
        """
        self._node = node
        self._hub = hub
        self._max_items = max_items

    async def observe(
        self,
        kinds: Sequence[ObservationKind],
        roots: Sequence[str | os.PathLike[str]],
        since_at: datetime | None,
    ) -> Observation:
        """Ask the packet what the device looks like right now.

        Args:
            kinds: What to report; at least one, each at most once.
            roots: Directories to read logs from and watch for changes; non-empty exactly when
                `kinds` includes logs or file changes.
            since_at: The file-change cursor, only with file changes; None asks for the packet's
                own recent window.

        Returns:
            The packet's observation of this device.

        Raises:
            GatewayUnavailableError: The device is not connected.
            DeviceRequestError: The packet refused (for example `pollen.capability_denied`), or
                answered with anything but an observation of this device.
            DeviceTimeoutError: The packet did not answer in time.
            pydantic.ValidationError: The kinds, roots or cursor do not make a valid request.
        """
        node = self._node
        link = self._hub.link(node.device_id)
        if link is None:
            raise GatewayUnavailableError(f"Device {node.device_id} is not connected.")
        request = Observe(
            device_id=node.device_id,
            kinds=tuple(kinds),
            roots=tuple(os.fspath(root) for root in roots),
            since_at=since_at,
            max_items=self._max_items,
            reason=OBSERVE_REASON,
        )
        reply = await link.request(
            request, sender=str(node.warden_id), timeout_s=OBSERVE_REPLY_TIMEOUT_S
        )
        observation = reply.payload
        if not isinstance(observation, Observation) or observation.device_id != node.device_id:
            raise DeviceRequestError(
                MISMATCH_CODE, f"the answer is not an observation of device {node.device_id}."
            )
        return observation
