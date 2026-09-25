"""Tests for hivemind.entrance.auth.login.factors: the device proof and the password check.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/login/factors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.login.factors for the module under test.
"""

from __future__ import annotations

from builders.entrance import (
    LOOPBACK,
    PASSWORD,
    BrowserKey,
    admitted_browser,
    admitted_program,
    auth_rig,
    sign_b64url,
)

from hivemind.entrance.auth import DeviceProof, begin_login, login_string
from hivemind.entrance.auth.login import binding_fits, password_holds, verify_proof
from hivemind.entrance.store import MemoryEntranceStore
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock


async def test_binding_fits_a_p256_point_for_a_browser_and_nothing_for_a_program() -> None:
    auth = await auth_rig()
    browser, _ = await admitted_browser(auth.enrolment)
    program, _ = await admitted_program(auth.enrolment)
    point = BrowserKey().public_key

    assert binding_fits(browser, point)
    assert not binding_fits(browser, None)
    assert not binding_fits(browser, "A" * 87)  # 65 bytes, but not a point on the curve.
    assert not binding_fits(browser, "not base64url!")
    assert binding_fits(program, None)
    assert not binding_fits(program, point)


async def test_a_proof_of_the_wrong_kind_never_holds() -> None:
    auth = await auth_rig()
    program, signer = await admitted_program(auth.enrolment)
    issued = await begin_login(auth.deps, program.id, LOOPBACK)
    taken = auth.deps.ceremony.challenges.take(issued.nonce, program.id)
    hive_id = auth.deps.records.identity.hive_id
    signature = sign_b64url(signer, login_string(hive_id, program.id, issued.nonce))

    as_passkey = DeviceProof(program.id, issued.nonce, assertion="{}")
    both = DeviceProof(program.id, issued.nonce, signature=signature, assertion="{}")
    garbled = DeviceProof(program.id, issued.nonce, signature="!")
    good = DeviceProof(program.id, issued.nonce, signature=signature)

    for proof in (as_passkey, both, garbled):
        assert verify_proof(auth.deps.ceremony, hive_id, program, proof, taken) is None
    verified = verify_proof(auth.deps.ceremony, hive_id, program, good, taken)
    assert verified is not None
    assert verified.sign_count is None


async def test_password_holds_only_for_the_operators_password() -> None:
    auth = await auth_rig()
    empty = MemoryEntranceStore(MemoryPheromoneTrail(FakeClock()))

    assert await password_holds(auth.hasher, auth.store, PASSWORD)
    assert not await password_holds(auth.hasher, auth.store, PASSWORD.upper())
    assert not await password_holds(auth.hasher, empty, PASSWORD)
