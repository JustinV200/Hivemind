"""Build the in-Cell Fanner: the seat meter every model call inside a Virtual Cell passes through.

Codingrules section 8.10 and roadmap step 3.12a: every model call from any bee passes through the
Fanner (named for the bees that fan the hive), which enforces seat counts and rate limits and
records each call as an `llm.call` event on the trail (ADR-0020: the LLM layer records its own
events). The Hive Stand's own composition root builds its Fanner from a loaded Hive Manifest
(`hivemind.cli.compose.deps.build_fanner`, with `_lane_for_grant` beside it); a Virtual Cell has
no manifest, so this module is the same construction over what the Queen shipped the Cell at
provision time: each provider's seats and rate limits from the `HIVEMIND_PROVIDERS` rows
(`hivemind.cli.in_cell.config.InCellRuntimeConfig.provider_seats`/`.provider_limits`), and a
`TrailLlmEventRecorder` on the Cell's own trail segment, stamped with the Cell's own hive and node
ids, so `hivemind.wardens.trail_sync` ships every call to the Queen's trail with the rest of that
segment. `lane_for_grant` gives each sub-bee its own lane, attributed to its grant and goal and
ordered by its task's tempo, exactly as the Hive Stand's Wardens get theirs.

There is no ledger inside a Cell (the Queen's `ForageLedger` lives on the Hive Stand), so spend
reaches the Queen another way, twice over: every `TaskResult.spend` the Warden reports is booked
on the task's own outcome, and the shipped `llm.call` events carry each call's `usage.cost_usd`,
which the Queen's spend view reads off her trail. The in-Cell provider registry carries no Forage
map either (`hivemind.cli.in_cell.providers` builds it with `map=None`: the Queen ships no
`[forage.map]` rows), so this Fanner's own map starts empty: it has no grade or load to spill on
and no source to observe, and meters seats and rate limits and records every call. A Night Veil
Cell's `llm.call` events land on that Cell's own segment exactly as its other execution records
do (codingrules section 12, "the Night Veil boundary"); nothing here treats them differently.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Called by
    `hivemind.cli.in_cell.deps.build_in_cell_warden_deps`. Calls into `hivemind.cli.in_cell.
    config` (InCellRuntimeConfig), `hivemind.forage` (ForageMap, Tempo), `hivemind.llm`
    (CallGate, Fanner, FannerDeps, TrailLlmEventRecorder), `hivemind.pheromone` (PheromoneTrail)
    and waggle only.

Key invariants:
    - Built the same way whichever registry `hivemind.cli.in_cell.providers` built: with no
      provider table shipped (the fallback fake registry), the Fanner is the same, metering at its
      own one-seat default, so behaviour never depends on which registry was built.
    - Every event it records carries this Cell's own hive id and node id, with this Cell's Warden
      as actor, never the Queen's node: the shipped segment merges under the Cell's own node id.

See Also:
    - .claude/codingrules.md section 8.10 for the Fanner's role.
    - docs/adr/0020-llm-records-its-own-trail-events.md for why the Fanner records `llm.call`.
    - hivemind.cli.compose.deps for build_fanner/_lane_for_grant, the Hive Stand's counterpart.
    - hivemind.cli.in_cell.deps for build_in_cell_warden_deps, this module's one caller.
"""

from __future__ import annotations

from collections.abc import Callable

from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.forage import ForageMap, Tempo
from hivemind.llm import CallGate, Fanner, FannerDeps, TrailLlmEventRecorder
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock

__all__ = ["build_in_cell_fanner", "lane_for_grant"]


def build_in_cell_fanner(
    config: InCellRuntimeConfig, trail: PheromoneTrail, clock: Clock
) -> Fanner:
    """Build this Cell's one Fanner: shipped seats and rate limits, recording to this Cell's trail.

    Args:
        config: This process's own validated runtime config; `provider_seats`,
            `provider_limits`, `hive_id`, `node_id` and `warden_id` are read.
        trail: This Cell's own local Pheromone Trail segment, the one `WaggleTrailSync` ships.
        clock: Injected time source for every wait and every recorded event.

    Returns:
        A Fanner every `CallGate` this Cell's Warden and sub-bees use is a lane of.
    """
    # Recorded as this Cell's own Warden on this Cell's own node: the segment trail_sync ships.
    recorder = TrailLlmEventRecorder(
        trail, config.hive_id, config.node_id, str(config.warden_id), clock
    )
    deps = FannerDeps(
        map=ForageMap((), clock=clock),  # No [forage.map] rows reach a Cell (module docstring).
        seats=config.provider_seats,
        limits=config.provider_limits,
        clock=clock,
        recorder=recorder,
    )
    return Fanner(deps)


def lane_for_grant(fanner: Fanner) -> Callable[[str, str, Tempo], CallGate]:
    """Return `WardenDeps.lane_for_grant`: one `fanner` lane per (grant, goal) on a task's tempo.

    Args:
        fanner: This Cell's own Fanner (`build_in_cell_fanner`).

    Returns:
        The callable `hivemind.wardens.spawn.spawn_sub_bee` calls once per spawn, so every
        `llm.call` a sub-bee makes names its grant and goal.
    """
    return lambda grant_id, goal_id, tempo: fanner.lane(tempo, grant_id=grant_id, goal_id=goal_id)
