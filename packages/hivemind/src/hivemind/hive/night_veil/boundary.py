"""Define NightVeilBoundary: open a Night Veil Cell's segment at provision, purge it at every end.

Codingrules section 12 keeps a Night Veil Cell's execution records in an ephemeral segment keyed
to the Cell and purges them at teardown (`hivemind.pheromone.retention`). This module is where the
Virtual Cell lifecycle meets that boundary, so every path a Night Veil Cell ends runs the purge:
`provisioned_facts` opens the Cell's segment the moment `hivemind.hive.lifecycle.CellLifecycle`
provisions one, before the Queen records a word about it (and `failure_facts` withholds the
`cell.provision_failed` of one whose backend failed: the skeleton has no such kind, and with no
Cell ever made the lifecycle takes its segment, which holds only its `cell.provisioning`, unread
and unpurged); `end_night_veil` runs
`NightVeilTeardownPurge` the moment the lifecycle destroys one (a finished task, a failed
provision after the Cell existed, a Hive shutdown), and `hivemind.cli.readback.virtual_abscond`
calls it for each Night Veil Cell an Absconding destroys (`adopt_night_veil` says which);
`sweep_night_veil` runs at every Queen start, holding again the segment of a Night Veil Cell that
outlived a restart and purging every one the trail's skeleton names that is gone unpurged.

What a restarted Queen relearns of a Night Veil Cell, and from where: that it is one (its backend
label, or the skeleton's `cell.provisioned` and `cell.attested`); its Capping counts so far and
the ids filed under it (its tasks, Wardens, nodes and grants: the segment's checkpoint,
`hivemind.pheromone.retention.checkpoint`, numbers and ids only); its Warden again, when that
Warden's link re-attaches; and every record it makes from then on, in the segment held again.
Never the records an earlier Queen held of it: those were in that Queen's memory alone, as the
boundary intends, and the purge's summary is built from the checkpointed counts instead.

A Cell's tier outlives the Queen that provisioned it in two places this module reads back: the
backend label every provisioned Cell now carries (`with_tier_label`; Docker also keeps its own
`hivemind.comb_shield`), and the skeleton itself, whose `cell.provisioned` names the tier and
whose `cell.attested` exists only for a Night Veil Cell (so a Cell provisioned before the tier was
recorded is still found). `is_night_veil_cell` asks both, with the boundary behind a trail, for a
writer about to record something that would outlive the Cell (a Cell Wax note: the Queen refuses
it for a Night Veil Cell).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.night_veil`. Built by the composition root
    (`hivemind.cli.compose.night_veil.build_night_veil`); called by `hivemind.hive.lifecycle` and
    `hivemind.cli.readback.virtual_abscond`. Calls into `hivemind.cell` (CombShieldLevel),
    `hivemind.common.logging`, `hivemind.hive.models` (VirtualCellSpec), `hivemind.pheromone` (the
    retention boundary, CellEvent, TrailQuery) and waggle only.

Key invariants:
    - A Night Veil Cell's segment is open before the lifecycle records its first event.
    - A Cell is purged at most once by one Queen: a segment already taken is never purged again,
      and the restart sweep skips every Cell the trail already shows a `cell.purged` for.
    - Every function takes the boundary as optional: a Hive with no Virtual side has none, and
      each is then a no-op.

See Also:
    - .claude/codingrules.md section 12 for the boundary, and section 8.7 for the teardown-only
      Night Veil lifecycle.
    - hivemind.pheromone.retention for the segments, the skeleton and the purge.
    - hivemind.hive.lifecycle for CellLifecycle, this module's main caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.cell import CombShieldLevel
from hivemind.common.logging import get_logger
from hivemind.hive.models import VirtualCellSpec
from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    CellEvent,
    EphemeralSegments,
    NightVeilTeardownPurge,
    PheromoneTrail,
    PurgeReport,
    TrailQuery,
    TrailRecorder,
    VeiledTrail,
    segments_of,
)
from waggle.ids import CellId, new_event_id

TIER_LABEL = "comb_shield"  # The label every provisioned Cell carries its tier under.
# The Docker backend's own copy of the same fact (hive/backends/docker/backend.py), read back too.
_DOCKER_TIER_LABEL = "hivemind.comb_shield"
_NIGHT_VEIL = CombShieldLevel.NIGHT_VEIL
_ACTOR = "system"  # Every end this module drives is the Hive's own housekeeping, not a person's.

log = get_logger(__name__)

__all__ = [
    "TIER_LABEL",
    "NightVeilBoundary",
    "adopt_night_veil",
    "end_night_veil",
    "failure_facts",
    "is_night_veil_cell",
    "night_veil_cells",
    "provisioned_facts",
    "sweep_night_veil",
    "tier_from_labels",
    "with_tier_label",
]


@dataclass(frozen=True, slots=True)
class NightVeilBoundary:
    """Everything the hive layer needs to open, end and sweep a Night Veil Cell's segment.

    Attributes:
        segments: The Queen's ephemeral segments, one per living Night Veil Cell.
        veiled: The durable trail behind the boundary; every Queen-side writer records through it.
        purge: The teardown purge every end runs.
        recorder: The durable trail and the Queen's own identity: the restart sweep reads the
            skeleton through it and records a vanished Cell's `cell.destroyed` with it.
    """

    segments: EphemeralSegments
    veiled: VeiledTrail
    purge: NightVeilTeardownPurge
    recorder: TrailRecorder


def with_tier_label(spec: VirtualCellSpec) -> VirtualCellSpec:
    """Return `spec` with its tier stamped into its labels, so a reconcile can read it back."""
    return spec.model_copy(update={"labels": {**spec.labels, TIER_LABEL: spec.comb_shield.value}})


def tier_from_labels(labels: Mapping[str, str]) -> CombShieldLevel:
    """Read a Cell's tier back from its backend labels; MEADOW when none names one.

    MEADOW is the default because the purge does not depend on it: a Night Veil Cell whose label
    is missing is still found by its skeleton (`night_veil_cells`) and by its held segment.
    """
    for key in (TIER_LABEL, _DOCKER_TIER_LABEL):
        value = labels.get(key)
        if value is not None and value in CombShieldLevel.__members__:
            return CombShieldLevel[value]
    return CombShieldLevel.MEADOW


def provisioned_facts(
    boundary: NightVeilBoundary | None, cell_id: CellId, spec: VirtualCellSpec, backend: str
) -> dict[str, JsonValue]:
    """Open `cell_id`'s segment when `spec` is Night Veil; return the provisioning payload.

    Returns:
        The payload `cell.provisioning` and `cell.provisioned` carry: the backend, the image and
        the tier, which the restart sweep reads back from the skeleton's `cell.provisioned`.
    """
    # Opened before the lifecycle's first record about the Cell, so none of them escapes it.
    if boundary is not None and spec.comb_shield is _NIGHT_VEIL:
        boundary.segments.open(cell_id)
    return {"backend": backend, "image": spec.image, "comb_shield": spec.comb_shield.value}


def failure_facts(
    boundary: NightVeilBoundary | None, spec: VirtualCellSpec, backend: str, reason: str
) -> dict[str, JsonValue] | None:
    """Return the payload `cell.provision_failed` carries, or None to withhold it for Night Veil.

    The Queen knows a failed Night Veil provision's Cell id (the lifecycle mints it before the
    backend is called, and opened its segment under it), so the record could be kept there; it is
    withheld whole instead, its reason (free text from the Cell's own boot) with it, since the
    skeleton names no such kind and the lifecycle takes that segment unread, no Cell ever having
    been made. Only the backend's name reaches the Queen's log.

    Returns:
        The backend, image and reason for any other tier; None for a Night Veil spec.
    """
    if boundary is not None and spec.comb_shield is _NIGHT_VEIL:
        log.info("night_veil.provision_failed", backend=backend)
        return None
    return {"backend": backend, "image": spec.image, "reason": reason}


async def end_night_veil(
    boundary: NightVeilBoundary | None, cell_id: CellId, tier: CombShieldLevel
) -> PurgeReport | None:
    """Purge `cell_id` once it is destroyed, when it is a Night Veil Cell; else do nothing.

    A Cell is purged when its segment is held here, or when its tier says Night Veil and this
    store never saw it; a segment already taken was purged once already and is not purged again.

    Returns:
        The purge's report, or None when nothing was purged.
    """
    if boundary is None:
        return None
    held = boundary.segments.holds(cell_id)
    if not held and (tier is not _NIGHT_VEIL or boundary.segments.owns(cell_id)):
        return None
    return await boundary.purge.purge(cell_id, actor=_ACTOR)


async def adopt_night_veil(
    boundary: NightVeilBoundary, cell_id: CellId, labels: Mapping[str, str]
) -> bool:
    """Return whether `cell_id` is a Night Veil Cell, holding its segment from now on if so.

    For an Absconding, which destroys Cells from backend labels alone: a Cell is Night Veil when
    its segment is held here, its labels say so, or the trail's skeleton does.
    """
    if boundary.segments.holds(cell_id):
        return True
    known = tier_from_labels(labels) is _NIGHT_VEIL
    if not known:
        known = cell_id in await night_veil_cells(boundary.recorder)
    if known:
        boundary.segments.open(cell_id)
    return known


async def is_night_veil_cell(trail: PheromoneTrail, cell_id: CellId) -> bool:
    """Return whether `cell_id` is or was a Night Veil Cell, by its boundary or the skeleton.

    The boundary behind `trail` (`segments_of`) owns every Night Veil Cell this Queen held or
    took; the skeleton names one she never held (a Cell provisioned before a restart).

    Args:
        trail: The trail the caller records through: a `VeiledTrail`, or a plain one (an offline
            command), whose reads reach the durable trail either way.
        cell_id: The Cell about to be written about.

    Returns:
        True for a Night Veil Cell; False for any other, or one this Hive never knew.
    """
    segments = segments_of(trail)
    if segments is not None and segments.owns(cell_id):
        return True
    provisioned = await trail.query(TrailQuery(kind="cell.provisioned", subject_id=cell_id))
    if any(e.payload.get(TIER_LABEL) == _NIGHT_VEIL.value for e in provisioned):
        return True
    # Attestation runs for a Night Veil Cell alone (`night_veil_cells`).
    return bool(await trail.query(TrailQuery(kind="cell.attested", subject_id=cell_id, limit=1)))


async def night_veil_cells(recorder: TrailRecorder) -> frozenset[CellId]:
    """Return every Night Veil Cell the durable trail's skeleton names, newest first.

    A `cell.provisioned` that names the tier, or any `cell.attested`: attestation runs for a
    Night Veil Cell alone, so it finds one provisioned before the tier was recorded as well.
    """
    provisioned = await recorder.trail.query(_newest("cell.provisioned"))
    attested = await recorder.trail.query(_newest("cell.attested"))
    named = {e.subject_id for e in provisioned if e.payload.get(TIER_LABEL) == _NIGHT_VEIL.value}
    return frozenset(CellId(cell) for cell in named | {e.subject_id for e in attested})


async def sweep_night_veil(
    boundary: NightVeilBoundary | None, live: Mapping[CellId, CombShieldLevel]
) -> tuple[CellId, ...]:
    """At a Queen start: hold every living Night Veil Cell, purge every one gone unpurged.

    Args:
        boundary: The Queen's boundary; None for a Hive with no Virtual side.
        live: Every Cell a backend still holds after the lifecycle's reconcile, with the tier its
            labels name.

    Returns:
        The Cells found gone and purged by this sweep, in id order.
    """
    if boundary is None:
        return ()
    known = await night_veil_cells(boundary.recorder)
    # A Night Veil Cell that outlived the restart: its segment is held again, so what it ships
    # from now on is veiled, and its own teardown purges it; what the earlier Queen checkpointed
    # of it (its ids, its Capping counts) is recalled, so nothing it did is lost to the purge.
    for cell_id, tier in live.items():
        if tier is _NIGHT_VEIL or cell_id in known:
            boundary.segments.open(cell_id)
            await boundary.segments.recall(cell_id)
    swept: list[CellId] = []
    for cell_id in sorted(known - set(live)):
        if await _has_record(boundary, cell_id, "cell.purged"):
            continue  # Purged by an earlier Queen already.
        await _record_gone(boundary, cell_id)
        await boundary.purge.purge(cell_id, actor=_ACTOR)
        swept.append(cell_id)
    return tuple(swept)


def _newest(kind: str) -> TrailQuery:
    """Query the newest page of `kind`: a long-lived Hive's oldest Cells were swept long ago."""
    return TrailQuery(kind=kind, newest_first=True, limit=MAX_QUERY_LIMIT)


async def _has_record(boundary: NightVeilBoundary, cell_id: CellId, kind: str) -> bool:
    """Return whether the durable trail holds a `kind` event about `cell_id`."""
    query = TrailQuery(kind=kind, subject_id=cell_id, limit=1)
    return bool(await boundary.recorder.trail.query(query))


async def _record_gone(boundary: NightVeilBoundary, cell_id: CellId) -> None:
    """Record `cell.destroyed` for a vanished Cell whose destruction the trail never recorded.

    The skeleton's own proof that the Cell is gone (ADR-0030): a Queen that stopped between the
    backend's destroy and its record, or a Cell lost with its host, still ends with one.
    """
    if await _has_record(boundary, cell_id, "cell.destroyed"):
        return
    recorder = boundary.recorder
    event = CellEvent(
        id=new_event_id(recorder.clock),
        hive_id=recorder.hive_id,
        node_id=recorder.node_id,
        at=recorder.clock.now(),
        actor=_ACTOR,
        kind="cell.destroyed",
        subject_id=cell_id,
        payload={},
    )
    await recorder.trail.record(event)
