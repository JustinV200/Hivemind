"""Tests for hivemind.cli.in_cell.fanner: build_in_cell_fanner and lane_for_grant.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/fanner.py (codingrules section 3). Builds a real
    `InCellRuntimeConfig` from a `HIVEMIND_*` environment, the Cell's own provider registry
    (`hivemind.cli.in_cell.providers`) and its Fanner, then makes a real call through a lane and
    reads the `llm.call` back off the Cell's own trail segment.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.fanner for the module under test.
    - test_deps.py for the same Fanner under a real Drone, through build_in_cell_warden_deps.
"""

from __future__ import annotations

import json

from builders.llm import make_request

from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.fanner import build_in_cell_fanner, lane_for_grant
from hivemind.cli.in_cell.providers import build_in_cell_provider_registry
from hivemind.forage import ModelSlot, Tempo
from hivemind.llm import DEFAULT_SEATS, FakeLLMProvider, RateLimit, text_response
from hivemind.manifest.env import read_in_cell_env
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

# Two providers as the Queen ships them: one naming its seats and a request-rate limit, one from
# a Queen that shipped neither (so the Fanner's own defaults apply to it).
_PROVIDERS_JSON = json.dumps(
    [
        {
            "name": "local",
            "kind": "openai_compat",
            "base_url": "http://host.docker.internal:1234/v1",
            "default_model": "local-test-model",
            "seats": 2,
            "requests_per_minute": 60,
            "tokens_per_minute": None,
        },
        {"name": "older", "kind": "fake", "base_url": "", "default_model": None},
    ]
)


def _config(clock: FakeClock, **overrides: str) -> InCellRuntimeConfig:
    """A valid InCellRuntimeConfig, with sensible defaults for every field a test ignores."""
    environ = {
        "HIVEMIND_QUEEN_WAGGLE_URL": "wss://queen.example.org:8443/waggle",
        "HIVEMIND_CELL_ID": new_cell_id(clock),
        "HIVEMIND_HIVE_ID": new_hive_id(clock),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(clock),
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": Ed25519Signer.generate().public_key_bytes.hex(),
    }
    environ.update(overrides)
    return build_runtime_config(read_in_cell_env(environ), clock)


async def test_each_provider_is_metered_by_the_seats_and_limits_the_queen_shipped() -> None:
    clock = FakeClock()
    config = _config(clock, HIVEMIND_PROVIDERS=_PROVIDERS_JSON)

    fanner = build_in_cell_fanner(config, MemoryPheromoneTrail(clock), clock)

    assert fanner.deps.seats == {"local": 2}
    assert fanner.deps.limits["local"] == RateLimit(requests_per_minute=60)
    assert fanner.deps.limits["older"] == RateLimit()
    # The seat meter itself is sized from the shipped row, and a row without seats gets one.
    assert (await fanner.seat_meter_for("local")).capacity == 2
    assert (await fanner.seat_meter_for("older")).capacity == DEFAULT_SEATS


async def test_the_fallback_fake_registry_gets_the_same_fanner_recording_on_the_cells_node() -> (
    None
):
    # No provider table shipped: the fallback fake registry, and still a real, recording Fanner.
    clock = FakeClock()
    config = _config(clock)
    trail = MemoryPheromoneTrail(clock)
    fanner = build_in_cell_fanner(config, trail, clock)
    bound = build_in_cell_provider_registry(clock, config).bound(ModelSlot.WORKER)
    assert isinstance(bound.provider, FakeLLMProvider)
    bound.provider.script(text_response("Hello."))
    lane = lane_for_grant(fanner)("grant_one", "goal_one", Tempo())

    await lane.complete(bound, make_request())

    [call] = await trail.query(TrailQuery(kind="llm.call"))
    # The Cell's own hive and node, its Warden as actor: the segment trail_sync ships as is.
    assert (call.hive_id, call.node_id, call.actor) == (
        config.hive_id,
        config.node_id,
        str(config.warden_id),
    )
    assert call.payload["grant_id"] == "grant_one"
    assert call.payload["goal_id"] == "goal_one"
    assert fanner.deps.seats == {}


async def test_the_wardens_own_lane_records_its_calls_unattributed() -> None:
    clock = FakeClock()
    config = _config(clock)
    trail = MemoryPheromoneTrail(clock)
    fanner = build_in_cell_fanner(config, trail, clock)
    bound = build_in_cell_provider_registry(clock, config).bound(ModelSlot.WARDEN)
    assert isinstance(bound.provider, FakeLLMProvider)
    bound.provider.script(text_response("{}"))

    await fanner.lane(Tempo()).complete(bound, make_request(slot=ModelSlot.WARDEN))

    [call] = await trail.query(TrailQuery(kind="llm.call"))
    assert "grant_id" not in call.payload
    assert call.node_id == config.node_id
