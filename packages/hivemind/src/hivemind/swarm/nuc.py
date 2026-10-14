"""Define decide_nuc: should a colonized device start a model server of its own, and which?

A device on the second rung of the runtime ladder (ADR-0052) is a Nuc: a colonized Real Cell (an
existing machine the Hive borrows and leaves as found) whose Warden (the always-on supervisor of
one Cell) also runs a model server on it, so its sub-bees keep thinking when the link to the Queen
(the Hive's central orchestrator) is lost. Whether to climb to it is the Queen's decision, taken
from data (roadmap 11.8), and this is that decision as a pure function. The device must be
colonized, because the Warden always moves first; its Warden must have declared a model server in
its capacity report, because the Queen can only start what the operator put on the device; the
Hive must allow that server some models (its Forage map entries, the sources the Hive's manifest
lists for that server id), and at least one must fit the VRAM the device has free (a model that
declares no VRAM runs on the CPU and needs none); and something must ask for it: the operator
(`desired_level`), the Hive Stand's seat pressure past `[swarm.nuc] seat_pressure`, or a task that
must survive a lost link. The answer names the server, every allowlisted model that fits (what the
Queen will let the Warden load on its own, the `Ceilings.loadable_sources`) and the ones that fit
together (what is loaded first). It only decides; the Queen's swarm gate acts on the answer.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by the Queen's swarm gate
    (Layer 6) before it promotes a device. Calls into hivemind.forage (the capacity and map
    types), hivemind.swarm.level and waggle.messages.swarm only.

Key invariants:
    - Pure: no I/O, no clock; the same facts give the same decision.
    - A promotion is decided only for a COLONIZED device; GATEWAY (no Warden on it yet) and NUC
      (already one) are refused, whatever else holds.
    - A refusal is one stable word, never an address, a path or a figure.
    - Every model named in the answer is one the Hive allowed the chosen server, in the order the
      Hive listed it, and `initial` is the part of `loadable` that fits the free VRAM together.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder and this decision.
    - hivemind.swarm.level for `decide_colonize`, the sibling rule for the first rung.
    - hivemind.wardens.local_pool.hosting.sources.plan_loads for the Warden's own check of the
      same rule against the ceilings this answer becomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.forage import ForageCapacity, ModelSource
from waggle.messages.swarm import RuntimeLevel

__all__ = [
    "NucDecision",
    "NucFacts",
    "NucReason",
    "OfferedServer",
    "decide_nuc",
    "free_vram_bytes",
    "vram_of",
]


class NucReason(Enum):
    """Why a colonized device should get a model server: the first reason that holds, in order."""

    OPERATOR = "operator"  # The operator asked for it (`desired_level`, `hive swarm promote`).
    PRESSURE = "pressure"  # The Hive Stand's seats are past `[swarm.nuc] seat_pressure`.
    AUTONOMY = "autonomy"  # A task on the device must survive the link to the Queen being lost.


@dataclass(frozen=True, slots=True)
class OfferedServer:
    """One model server the Hive allows, with the Forage map entries it may load.

    Attributes:
        server_id: The `[llm.servers]` key, the same one the device's Warden reports.
        sources: The Forage map entries the Hive lists for that server, in manifest order; each
            has no host yet (`host_cell_id` is None) until a Warden loads it.
    """

    server_id: str
    sources: tuple[ModelSource, ...]


@dataclass(frozen=True, slots=True)
class NucFacts:
    """Everything `decide_nuc` weighs about one device and the Hive Stand.

    Attributes:
        level: The rung the device is on.
        capacity: The device's latest Forage capacity report (its GPUs' free VRAM is read).
        declared: The server ids the device's Warden declared in its capacity report, in the
            order it listed them.
        offered: The servers the Hive allows and the models it lists for each.
        desired_level: The rung the operator asked for, or None.
        hive_pressure: The Hive Stand's seat pressure, 0 to 1.
        seat_pressure: `[swarm.nuc] seat_pressure`: the pressure past which a Nuc is wanted.
        needs_outage_survival: Whether a task on this device must survive a lost link.
    """

    level: RuntimeLevel
    capacity: ForageCapacity
    declared: tuple[str, ...]
    offered: tuple[OfferedServer, ...]
    desired_level: RuntimeLevel | None
    hive_pressure: float
    seat_pressure: float
    needs_outage_survival: bool = False


@dataclass(frozen=True, slots=True)
class NucDecision:
    """The answer of `decide_nuc`: a server and its models, or one word for why not.

    Attributes:
        reason: Why the device should get a server; None when it should not.
        refusal: A stable word saying why not; None exactly when `reason` is set.
        server_id: The server to start; None when refused.
        loadable: Every model of that server that fits the free VRAM on its own: the allowlist
            the Queen sets in the Warden's ceilings.
        initial: The models to load first: those of `loadable` that fit together.
    """

    reason: NucReason | None
    refusal: str | None = None
    server_id: str | None = None
    loadable: tuple[str, ...] = ()
    initial: tuple[str, ...] = ()

    @property
    def should_promote(self) -> bool:
        """Whether the device should be promoted to a Nuc."""
        return self.reason is not None


def decide_nuc(facts: NucFacts) -> NucDecision:
    """Decide whether a COLONIZED device should start a model server, and which one.

    The reason is weighed first, so a device nobody wants promoted is refused as `not_asked` and
    not for whatever else it lacks. Then the device must be colonized, have declared a server,
    have models the Hive allows that server, and have VRAM free for at least one of them.

    Args:
        facts: The device, the Hive's allowances and the thresholds.

    Returns:
        The server, its models and the reason, or the first refusal: `not_asked`,
        `not_colonized`, `no_server`, `no_allowlist` or `vram_too_small`.
    """
    reason = _why(facts)
    if reason is None:
        return NucDecision(None, "not_asked")
    if facts.level is not RuntimeLevel.COLONIZED:
        return NucDecision(None, "not_colonized")  # The Warden moves first; a Nuc is climbed to.
    if not facts.declared:
        return NucDecision(None, "no_server")
    chosen = _first_that_fits(facts)
    if chosen is None:
        return NucDecision(None, _why_nothing_fits(facts))
    return NucDecision(reason, None, *chosen)


def free_vram_bytes(capacity: ForageCapacity) -> int:
    """Return the VRAM free across the device's GPUs, in bytes; 0 for a device with none.

    Args:
        capacity: The device's Forage capacity report.
    """
    return sum(gpu.vram_free_bytes for gpu in capacity.host.gpus)


def vram_of(source: ModelSource) -> int:
    """Return the VRAM `source` needs loaded; 0 for a source that declares none (it runs on CPU)."""
    return source.spec.vram_bytes_required or 0


def _why(facts: NucFacts) -> NucReason | None:
    """Return the first reason to promote that holds, or None when nothing asks for it."""
    if facts.desired_level is RuntimeLevel.NUC:
        return NucReason.OPERATOR
    if facts.hive_pressure >= facts.seat_pressure:
        return NucReason.PRESSURE
    return NucReason.AUTONOMY if facts.needs_outage_survival else None


def _first_that_fits(facts: NucFacts) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Return (server, loadable, initial) of the first declared server with a model that fits."""
    free = free_vram_bytes(facts.capacity)
    by_id = {server.server_id: server for server in facts.offered}
    # In the order the device declared them: the operator's own preference between its servers.
    for server_id in facts.declared:
        server = by_id.get(server_id)
        if server is None:
            continue  # Declared on the device, but the Hive allows it no models.
        loadable = tuple(s.source_id for s in server.sources if vram_of(s) <= free)
        initial = _fit_together(server.sources, free)
        if initial:
            return server_id, loadable, initial
    return None


def _fit_together(sources: tuple[ModelSource, ...], free: int) -> tuple[str, ...]:
    """Return the ids of the sources that fit `free` VRAM together, taken in the given order."""
    left, taken = free, []
    # One pass in the Hive's order, as the Warden's own `plan_loads` takes them: an earlier model
    # can crowd out a later one, never the reverse, so the two always agree on what loads.
    for source in sources:
        if vram_of(source) <= left:
            taken.append(source.source_id)
            left -= vram_of(source)
    return tuple(taken)


def _why_nothing_fits(facts: NucFacts) -> str:
    """Return the word for why no declared server could start: no allowlist, or too little VRAM."""
    offered = {server.server_id for server in facts.offered}
    # A declared server the Hive lists nothing for is a matter of the Hive's manifest; a listed
    # one that does not fit is a matter of the device.
    return "vram_too_small" if offered.intersection(facts.declared) else "no_allowlist"
