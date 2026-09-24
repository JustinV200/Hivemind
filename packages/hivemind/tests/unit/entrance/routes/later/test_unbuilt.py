"""Test hivemind.entrance.routes.later: the resources a later phase fills answer 501, and say when.

Over real listeners: ``tools``, ``honey`` and ``swarm`` each answer an observing device 501 with
the stable not-built code, the resource and the roadmap phase that fills it (9, 7 and 11), on the
remote listener too; the placeholders are still behind the gate, so a submit-only device is
refused and a request with no session is not answered at all.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/later/ (codingrules section 3): unbuilt.py's row and the
    three resources built from it.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, serving

from hivemind.entrance.models.views import NOT_BUILT_CODE

_PHASES = {"tools": 9, "honey": 7, "swarm": 11}  # Each resource, and the phase that fills it.


async def test_each_later_resource_answers_501_with_the_phase_that_fills_it() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        local, local_session = await rig.program(ProgramGrant(capabilities=("observe",)))
        remote, remote_session = await rig.program(
            ProgramGrant(capabilities=("observe",), remote=True)
        )

        answers = {
            resource: (
                await local.call(local_session, "GET", f"/v1/{resource}"),
                await remote.call(remote_session, "GET", f"/v1/{resource}"),
            )
            for resource in _PHASES
        }

    for resource, (loopback, far) in answers.items():
        assert loopback.status_code == far.status_code == 501, loopback.text
        assert loopback.json() == far.json()
        body = loopback.json()
        assert (body["error"], body["resource"], body["phase"]) == (
            NOT_BUILT_CODE,
            resource,
            _PHASES[resource],
        )


async def test_the_placeholders_are_still_behind_the_gate() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        refused = [
            (await client.call(session, "GET", f"/v1/{resource}")).status_code
            for resource in _PHASES
        ]
        anonymous = [
            (await rig.client().http.get(f"/v1/{resource}")).status_code for resource in _PHASES
        ]

    assert refused == [403, 403, 403]
    assert anonymous == [401, 401, 401]
