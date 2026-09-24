"""Test hivemind.entrance.routes.entrance.operators: operator add refuses a second operator.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/entrance/operators.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import RigOptions, serving

_OPERATORS = "/v1/entrance/operators"


async def test_operator_add_is_refused_at_the_hive_stand_and_absent_remotely() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        console, session = await rig.console_session()

        local = await console.call(session, "POST", _OPERATORS)
        remote = await rig.client(remote=True).http.post(_OPERATORS)

    assert local.status_code == 409, local.text
    assert local.json()["error"] == "hivemind.entrance.single_operator"
    # ADR-0033: a loopback-only row does not exist on the remote listener at all.
    assert remote.status_code == 404
