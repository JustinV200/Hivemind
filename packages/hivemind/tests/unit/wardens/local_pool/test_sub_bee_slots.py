"""Tests for hivemind.wardens.local_pool.sub_bee_slots: the bare sub-bee-slot counter.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/sub_bee_slots.py (codingrules section 3). Renamed
    from test_pool.py in roadmap step 4.7, alongside the LocalPool -> SubBeeSlots rename.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.sub_bee_slots for the module under test.
"""

from __future__ import annotations

from hivemind.wardens.local_pool.sub_bee_slots import SubBeeSlots


def test_sub_bee_slots_acquire_succeeds_until_capacity_is_reached() -> None:
    pool = SubBeeSlots(max_sub_bees=2)

    assert pool.acquire() is True
    assert pool.acquire() is True
    assert pool.in_use == 2
    assert pool.acquire() is False
    assert pool.in_use == 2  # A refused acquire never changes in_use.


def test_sub_bee_slots_release_frees_a_slot_for_a_later_acquire() -> None:
    pool = SubBeeSlots(max_sub_bees=1)
    assert pool.acquire() is True
    assert pool.acquire() is False

    pool.release()

    assert pool.in_use == 0
    assert pool.acquire() is True


def test_sub_bee_slots_release_never_drives_in_use_negative() -> None:
    pool = SubBeeSlots(max_sub_bees=1)

    pool.release()
    pool.release()

    assert pool.in_use == 0


def test_sub_bee_slots_capacity_zero_refuses_every_acquire() -> None:
    pool = SubBeeSlots(max_sub_bees=0)

    assert pool.acquire() is False
    assert pool.capacity == 0


def test_sub_bee_slots_resize_changes_capacity_without_touching_in_use() -> None:
    pool = SubBeeSlots(max_sub_bees=1)
    assert pool.acquire() is True

    pool.resize(4)

    assert pool.capacity == 4
    assert pool.in_use == 1
    assert pool.acquire() is True
