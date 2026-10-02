"""Put what a lease's browser blocked on the trail: `cell.browser_blocked`, once per rule and host.

The browser's guards (ADR-0034, ADR-0058) refuse a file outside scratch, a request to a host the
bee holds no `net` scope for, and a DevTools window, at the browser itself, where no proposal and
so no Capping gate record exists. `BlockTrail` is the sink attach hands them, so each refusal is
still on the trail: one `cell.browser_blocked` event per distinct rule and host for the lease,
carrying the rule and, for a host refusal, the host (never a URL or a path; a URL's path and
query can carry what a host name does not). A page that retries the same blocked host a hundred
times is one event, and past `MAX_BLOCK_EVENTS` distinct ones the rest go to the log alone, so a
hostile page cannot flood the trail.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Built by `attach.core.attach` into the browser's launch request. Calls into
    `hivemind.exoskeleton.browser.hosts` (the block record), `attach.handle` (ExoskeletonTrail),
    `hivemind.pheromone` (its errors) and `hivemind.common.logging`.

Key invariants:
    - Never raises: a trail that cannot take the event is logged, since the block itself held.
    - At most MAX_BLOCK_EVENTS events per lease.

See Also:
    - docs/adr/0044-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md.
    - hivemind.pheromone.events.families.work for the event kind.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.attach.handle import ExoskeletonTrail
from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock
from hivemind.pheromone import PheromoneError

BLOCKED_KIND = "cell.browser_blocked"  # The trail event one block becomes.
MAX_BLOCK_EVENTS = 64  # Distinct blocks recorded per lease; a page cannot flood the trail.

log = get_logger(__name__)

__all__ = ["BLOCKED_KIND", "MAX_BLOCK_EVENTS", "BlockTrail"]


class BlockTrail:
    """Record each distinct thing one lease's browser blocked, up to MAX_BLOCK_EVENTS.

    Owns mutable state (codingrules 8.5): the blocks already recorded for this lease.
    """

    def __init__(self, trail: ExoskeletonTrail) -> None:
        """Build the sink for one attach.

        Args:
            trail: Records events about the lease's Cell.
        """
        self._trail = trail
        self._seen: set[tuple[BlockRule, str | None]] = set()

    async def __call__(self, block: BrowserBlock) -> None:
        """Record `block` unless an equal one was already recorded or the cap is reached.

        Latency: one trail append, milliseconds.

        Args:
            block: What a guard blocked.
        """
        key = (block.rule, block.host)
        if key in self._seen:
            return
        if len(self._seen) >= MAX_BLOCK_EVENTS:
            log.info("exoskeleton.browser_block_not_trailed", rule=block.rule.value)
            return
        self._seen.add(key)
        payload: dict[str, JsonValue] = {"rule": block.rule.value}
        if block.host is not None:
            payload["host"] = block.host
        try:
            # External await: one append to the trail.
            await self._trail.record(BLOCKED_KIND, payload)
        except PheromoneError as error:
            # The block held whatever the trail did; losing its record is worth a warning only.
            log.warning("exoskeleton.browser_block_trail_failed", reason=type(error).__name__)
