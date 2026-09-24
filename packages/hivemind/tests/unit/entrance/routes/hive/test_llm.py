"""Test hivemind.entrance.routes.hive.llm: providers as the Queen judges them, and every binding.

Over real listeners and a real Queen: a healthy provider is listed with its kind and every slot
binding with the slot it serves, and nothing like a key or a base URL is ever in the answer; when
her health poller reads the provider down it is DEGRADED, and once Clustering pauses its bees it
is DOWN with the Queen CLUSTERED. Answering never probes a provider: only her bookkeeping moves it.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/llm.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, serving

from hivemind.llm import FakeLLMProvider

_OBSERVE = ProgramGrant(capabilities=("observe",))


async def test_providers_are_judged_from_the_queens_bookkeeping_and_bindings_listed() -> None:
    provider = FakeLLMProvider()
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        name = rig.deps.bindings[0].provider
        healthy = await client.call(session, "GET", "/v1/llm")
        provider.set_outage(True)
        # One probe on her own schedule, as her cluster tick runs it; the read never probes.
        await rig.deps.health_poller.probe(name, lambda _: provider, rig.clock.now())
        degraded = await client.call(session, "GET", "/v1/llm")
        rig.deps.cluster_state.mark_clustered(name)
        down = await client.call(session, "GET", "/v1/llm")

    assert healthy.status_code == 200, healthy.text
    body = healthy.json()
    assert body["queen_mode"] == "RUNNING"
    assert body["providers"] == [
        {"name": name, "kind": "fake", "health": "HEALTHY", "clustered": False, "failed_probes": 0}
    ]
    assert [slot["key"] for slot in body["slots"]] == [b.key for b in rig.deps.bindings]
    assert {slot["provider"] for slot in body["slots"]} == {name}
    for text in (healthy.text, degraded.text, down.text):
        assert "api_key" not in text and "base_url" not in text
    assert degraded.json()["providers"][0]["health"] == "DEGRADED"
    assert degraded.json()["providers"][0]["failed_probes"] == 1
    assert down.json()["providers"][0]["health"] == "DOWN"
    assert down.json()["queen_mode"] == "CLUSTERED"


async def test_a_submit_only_device_is_refused_the_llm_read() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        read = await client.call(session, "GET", "/v1/llm")

    assert read.status_code == 403
