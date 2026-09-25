"""Test hivemind.entrance.routes.enrol: the Hive's id, readable before a device has any session.

A program's enrolment and login signatures name the Hive (``hive-enrol-v1``, ``hive-login-v1``),
and an invite code does not carry its id, so a program written from the OpenAPI document alone
reads it from ``GET /v1/enrol/hive`` first (roadmap step 10.5c's conformance client found the gap).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import RigOptions, serving


async def test_the_hive_id_is_public_and_the_same_on_both_listeners() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        local = await rig.client().http.get("/v1/enrol/hive")
        remote = await rig.client(remote=True).http.get("/v1/enrol/hive")

    assert (local.status_code, remote.status_code) == (200, 200)
    assert local.json() == remote.json() == {"hive_id": rig.hive_id}
