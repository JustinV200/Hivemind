"""Tests for hivemind.entrance.enrol.deps.bundle: the enrolment bundles refuse what cannot work.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/deps/bundle.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.deps.bundle for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance import INVITE_TTL, PENDING_TTL, RELYING_PARTY

from hivemind.entrance.auth import ChallengeBook
from hivemind.entrance.enrol import (
    EnrolmentCeremony,
    EnrolmentRules,
    EnrolmentSeams,
    NullDeviceOffboarder,
    NullGoalLedger,
    NullSecurityNotifier,
)
from hivemind.guard import load_guard_policy
from waggle.clock import FakeClock

_POLICY = load_guard_policy()


@pytest.mark.parametrize(
    "base_url",
    ["http://localhost:8710", "https://hive.example.ts.net", "https://hive.example.ts.net/x/"],
)
def test_rules_accept_an_http_origin_and_path(base_url: str) -> None:
    rules = EnrolmentRules(_POLICY, INVITE_TTL, PENDING_TTL, base_url)

    assert rules.invite_base_url == base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://hive.example",
        "hive.example.ts.net",
        "https://",
        "https://hive.example/?next=1",
        "https://hive.example/#code=X",
    ],
)
def test_rules_refuse_a_base_url_an_invite_link_cannot_start_with(base_url: str) -> None:
    with pytest.raises(ValueError, match="invite base URL"):
        EnrolmentRules(_POLICY, INVITE_TTL, PENDING_TTL, base_url)


@pytest.mark.parametrize(
    ("invite_ttl", "pending_ttl"), [(timedelta(0), PENDING_TTL), (INVITE_TTL, timedelta(-1))]
)
def test_rules_refuse_a_lifetime_that_is_not_positive(
    invite_ttl: timedelta, pending_ttl: timedelta
) -> None:
    with pytest.raises(ValueError, match="positive"):
        EnrolmentRules(_POLICY, invite_ttl, pending_ttl, "https://hive.example")


def test_the_ceremony_needs_a_raw_ed25519_hive_key() -> None:
    book = ChallengeBook(FakeClock(), timedelta(minutes=2))

    assert EnrolmentCeremony(bytes(32), RELYING_PARTY, book).hive_public_key == bytes(32)
    with pytest.raises(ValueError, match="32 raw bytes"):
        EnrolmentCeremony(bytes(33), RELYING_PARTY, book)


def test_every_seam_defaults_to_its_no_op() -> None:
    seams = EnrolmentSeams()

    assert isinstance(seams.notifier, NullSecurityNotifier)
    assert isinstance(seams.offboarder, NullDeviceOffboarder)
    assert isinstance(seams.goals, NullGoalLedger)
