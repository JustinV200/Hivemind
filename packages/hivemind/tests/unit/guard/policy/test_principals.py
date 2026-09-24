"""Tests for hivemind.guard.policy.principals: the refs the Queen, a Warden and a Worker act as.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/principals.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.principals for the module under test.
"""

from __future__ import annotations

from hivemind.guard.policy import PrincipalKind, load_guard_policy
from hivemind.guard.policy.principals import queen_principal, warden_principal, worker_principal
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_warden_id, new_worker_id
from waggle.messages.task import WorkerRole

_CLOCK = FakeClock()


def test_the_queen_acts_as_her_hive_under_the_queen_role() -> None:
    hive_id = new_hive_id(_CLOCK)

    ref = queen_principal(hive_id)

    assert (ref.kind, ref.id, ref.role) == (PrincipalKind.QUEEN, hive_id, "queen")


def test_a_warden_acts_as_itself_under_the_warden_role() -> None:
    warden_id = new_warden_id(_CLOCK)

    ref = warden_principal(warden_id)

    assert (ref.kind, ref.id, ref.role) == (PrincipalKind.WARDEN, warden_id, "warden")


def test_a_worker_acts_as_itself_under_its_wire_roles_policy_name() -> None:
    worker_id = new_worker_id(_CLOCK)

    ref = worker_principal(worker_id, WorkerRole.DRONE)

    assert (ref.kind, ref.id, ref.role) == (PrincipalKind.WORKER, worker_id, "drone")


def test_every_role_a_principal_names_is_one_the_policy_defines() -> None:
    roles = load_guard_policy().roles
    refs = (
        queen_principal(new_hive_id(_CLOCK)),
        warden_principal(new_warden_id(_CLOCK)),
        worker_principal(new_worker_id(_CLOCK), WorkerRole.DRONE),
    )

    assert all(ref.role in roles for ref in refs)
