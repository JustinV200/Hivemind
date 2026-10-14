"""Tests for hivemind.hive.night_veil.judges.hostname: minting and judging a randomized hostname.

The shape of a Night Veil Cell's hostname, `night-veil-` and eight hex digits, is fixed in one
module that both the backends (which mint it) and the attestation (which judges it) use, so the
two cannot disagree. These tests pin the shape, show every mint passes the judge, and show what the
judge refuses: no name, a name an image ships with, the Hive Stand's own, and any other shape (a
name made from a Cell's id, a Docker container's default).

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/judges/hostname.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.judges.hostname for the module under test.
    - tests/unit/hive/backends/docker/test_backend.py for the backend that mints one.
"""

from __future__ import annotations

import re

import pytest

from hivemind.hive.night_veil.judges import (
    HOSTNAME_PREFIX,
    SHIPPED_HOSTNAMES,
    judge_hostname,
    new_night_veil_hostname,
)
from hivemind.hive.night_veil.results import CheckStatus
from waggle.clock import FakeClock
from waggle.ids import new_cell_id


def _detail(reported: str, stand: str = "") -> tuple[CheckStatus, str]:
    result = judge_hostname(reported, stand_hostname=stand)
    return result.status, result.detail


def test_a_minted_hostname_is_the_prefix_and_eight_lowercase_hex_digits() -> None:
    name = new_night_veil_hostname()

    assert re.fullmatch(r"night-veil-[0-9a-f]{8}", name)
    assert name.startswith(HOSTNAME_PREFIX)


def test_minted_hostnames_differ_from_one_to_the_next() -> None:
    # 2**32 names: a collision among a hundred draws is a one in a million event, not a flake.
    assert len({new_night_veil_hostname() for _ in range(100)}) == 100


def test_every_minted_hostname_passes_the_judge() -> None:
    assert all(
        judge_hostname(new_night_veil_hostname()).status is CheckStatus.PASS for _ in range(50)
    )


def test_the_kernels_newline_and_a_different_case_do_not_matter() -> None:
    assert _detail("night-veil-0123abcd\n") == (CheckStatus.PASS, "random_hostname")
    assert _detail("NIGHT-VEIL-0123ABCD") == (CheckStatus.PASS, "random_hostname")


@pytest.mark.parametrize("reported", ["", "   ", "\n"])
def test_no_hostname_is_red(reported: str) -> None:
    assert _detail(reported) == (CheckStatus.FAIL, "no_hostname")


@pytest.mark.parametrize("shipped", sorted(SHIPPED_HOSTNAMES))
def test_a_name_an_image_ships_with_is_red(shipped: str) -> None:
    assert _detail(shipped) == (CheckStatus.FAIL, "image_default_hostname")
    assert _detail(shipped.upper()) == (CheckStatus.FAIL, "image_default_hostname")


def test_the_hive_stands_own_hostname_is_red_even_in_a_different_case() -> None:
    assert _detail("Bee-Keeper", stand="bee-keeper") == (CheckStatus.FAIL, "hive_stand_hostname")


def test_a_random_shaped_name_that_is_the_stands_is_still_red() -> None:
    stand = "night-veil-0123abcd"

    assert _detail(stand, stand=stand) == (CheckStatus.FAIL, "hive_stand_hostname")


def test_without_a_known_stand_hostname_only_the_shape_is_judged() -> None:
    assert _detail("night-veil-0123abcd", stand="")[0] is CheckStatus.PASS
    assert _detail("bee-keeper", stand="")[0] is CheckStatus.FAIL


@pytest.mark.parametrize(
    "reported",
    [
        "3f2a9c1d7b0e",  # A Docker container's default: its container id, not this shape.
        "night-veil-0123abc",  # One digit short.
        "night-veil-0123abcde",  # One digit long.
        "night-veil-0123abcg",  # Not hex.
        "night-veil-",  # No digits.
        "night-veil-0123abcd-extra",
        "my-night-veil-0123abcd",
        "bee-keeper",
    ],
)
def test_any_other_shape_is_red(reported: str) -> None:
    assert _detail(reported) == (CheckStatus.FAIL, "not_random_shape")


def test_a_name_made_from_the_cells_own_id_is_red() -> None:
    # What a QEMU guest was given before: the Cell id with its underscore made DNS-safe.
    named = str(new_cell_id(FakeClock())).replace("_", "-")

    assert _detail(named) == (CheckStatus.FAIL, "not_random_shape")


def test_a_result_never_carries_the_name_the_cell_reported() -> None:
    reported = "secret-operator-laptop"

    assert reported not in judge_hostname(reported, stand_hostname=reported).detail
    assert reported not in judge_hostname(reported).detail
