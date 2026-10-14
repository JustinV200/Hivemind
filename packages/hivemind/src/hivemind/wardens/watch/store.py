"""Keep watch observations in Bee Bread for a retention window, and read them back for a Patrol.

An Observation describes the operator's own machine, so it is `C2` from the moment it is taken
(ADR-0054) and lives only as long as `[supervision.watch] retention_s`. This module is the one
place that turns an Observation into a Bee Bread entry (`BeeBreadEntryKind.WATCH_OBSERVATION`,
always `C2`, no task) and back. The text is the Observation's own JSON, whole, because the Patrol
reads what the machine said and a summary would already be a judgement; if a maximal Observation
would not fit one entry (200,000 characters), its lists are halved until it does and it is marked
truncated, the way a Pollen Packet shortens an answer that will not fit a frame. Saving a sample
also sweeps the expired ones, so retention needs no timer of its own. An entry that cannot be read
back (written by another version, or damaged) is skipped and counted, never raised: one bad row
must not stop a Patrol from reviewing the rest.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.watch.observer` (save) and `.patrol` (load and
    discard). Calls into `hivemind.memory` and `hivemind.memory.bee_bread` and waggle only.

Key invariants:
    - Everything saved here is `C2` and none of it reaches Honey (the House Bee passes the kind
      over); the trail records that an entry was deposited, never what was in it.
    - An entry names the Warden that kept it: a Warden reads and discards its own and no other's,
      though every Warden of a Hive Stand keeps them in the one store.
    - `load_observations` returns oldest first, within the window, and never an entry it could
      not parse.
    - Nothing here is a trail payload or a log line: Observation text stays in the store.

See Also:
    - hivemind.memory.bee_bread.deposit.deposit_watch_observation for the deposit itself.
    - hivemind.memory.store.protocol for `list_bee_bread_of_kind` and `purge_bee_bread_before`.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the retention rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.memory import MemoryContext
from hivemind.memory.bee_bread import (
    MAX_ENTRY_PAYLOAD_CHARS,
    BeeBreadEntryKind,
    deposit_watch_observation,
)
from waggle.messages.swarm import Observation

MAX_SHRINKS = 8  # Halvings tried before giving up: 500 items halved eight times is two.

__all__ = [
    "MAX_SHRINKS",
    "KeptObservation",
    "discard_before",
    "encode_observation",
    "load_observations",
    "save",
]


@dataclass(frozen=True, slots=True)
class KeptObservation:
    """One Observation as it was kept: which entry holds it, and when it was kept."""

    entry_id: str  # Names the entry, so a Patrol can tell it has reviewed it, to the instant.
    kept_at: datetime  # When it was deposited; the instant a Patrol's window is cut by.
    observation: Observation


def encode_observation(observation: Observation) -> str:
    """Return the Observation as JSON text that fits one Bee Bread entry.

    Args:
        observation: The sample as the probe returned it.

    Returns:
        Its JSON; if that is over the entry's bound, the JSON of a copy with each list halved
        (newest kept for log lines and changes, busiest kept for processes) and `is_truncated` set,
        halved again until it fits.
    """
    shrunk = observation
    text = shrunk.model_dump_json()
    for _ in range(MAX_SHRINKS):
        if len(text) <= MAX_ENTRY_PAYLOAD_CHARS:
            break
        shrunk = _halved(shrunk)
        text = shrunk.model_dump_json()
    return text


async def save(
    observation: Observation, ctx: MemoryContext, owner: str, retention_s: float
) -> None:
    """Keep one Observation as a C2 entry and sweep the ones past retention.

    Args:
        observation: The sample to keep.
        ctx: The store, identity and clock to write with.
        owner: The id of the Warden keeping it. The Hive Stand's Warden and every device's keep
            theirs in one store; each reads and discards only its own.
        retention_s: Seconds an entry is kept; older ones, whoever's, are purged by this same
            call, since the manifest gives every Warden the same retention.
    """
    await deposit_watch_observation(encode_observation(observation), owner, ctx)
    cutoff = ctx.clock.now() - timedelta(seconds=retention_s)
    await ctx.store.purge_bee_bread_before(BeeBreadEntryKind.WATCH_OBSERVATION, cutoff)


async def load_observations(
    ctx: MemoryContext, owner: str, since: datetime, until: datetime
) -> tuple[KeptObservation, ...]:
    """Read the Observations one Warden kept between two instants, oldest first.

    Args:
        ctx: The store, identity and clock to read with.
        owner: The id of the Warden whose observations are read.
        since: Inclusive lower bound on when an entry was kept.
        until: Inclusive upper bound.

    Returns:
        The kept Observations; an entry that does not parse is left out.
    """
    entries = await ctx.store.list_bee_bread_of_kind(
        BeeBreadEntryKind.WATCH_OBSERVATION, since, until, HoneyClearance.C2, owner
    )
    found: list[KeptObservation] = []
    for entry in entries:
        try:
            observation = Observation.model_validate_json(entry.payload or "")
            found.append(KeptObservation(entry.id, entry.created_at, observation))
        except ValidationError:
            continue  # Written by another version or damaged: skipped, never fatal to a Patrol.
    return tuple(found)


async def discard_before(ctx: MemoryContext, owner: str, cutoff: datetime) -> int:
    """Delete a Warden's kept Observations older than `cutoff`, as a Patrol that found nothing does.

    Args:
        ctx: The store, identity and clock to delete with.
        owner: The id of the Warden whose observations go; no other Warden's are touched.
        cutoff: Entries kept strictly before this instant are deleted.

    Returns:
        How many were deleted.
    """
    return await ctx.store.purge_bee_bread_before(
        BeeBreadEntryKind.WATCH_OBSERVATION, cutoff, owner
    )


def _halved(observation: Observation) -> Observation:
    """Return a copy with half of each list, marked truncated."""
    return observation.model_copy(
        update={
            "processes": observation.processes[: len(observation.processes) // 2],
            # The newest are last in both lists, so the cut keeps the newest.
            "log_lines": observation.log_lines[len(observation.log_lines) // 2 :],
            "file_changes": observation.file_changes[len(observation.file_changes) // 2 :],
            "is_truncated": True,
        }
    )
