"""Test hivemind.cli.entrance.serving: one holder of the serve lock, and the published record.

The lock is an operating-system lock, so a second holder is refused even inside one process (each
``open`` is its own lock owner); ``hive serve``'s composition takes it before it binds anything and
publishes its loopback listener once it serves.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/serving.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from builders.entrance.stand import serving_stand, set_password, stand_manifest

from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.cli.entrance import (
    HiveBusyError,
    ServeRecord,
    hold_serve_lock,
    publish_serve_record,
    read_serve_record,
)
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_STARTED = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def test_a_second_holder_is_refused_naming_the_first(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"

    with (
        hold_serve_lock(db, "hive serve"),
        pytest.raises(HiveBusyError) as busy,
        hold_serve_lock(db, "an offline step"),
    ):
        pytest.fail("the lock must have one holder")

    assert "hive serve" in str(busy.value)


def test_the_lock_is_free_again_once_its_holder_leaves(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    with hold_serve_lock(db, "first"):
        pass

    with hold_serve_lock(db, "second"):
        held = (tmp_path / "hive.sqlite3.serve.lock").read_text(encoding="utf-8")

    assert held.endswith(": second")


def test_a_published_record_is_read_back_and_removed_on_exit(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    record = ServeRecord(pid=4242, host="127.0.0.1", port=8710, started_at=_STARTED)

    with publish_serve_record(db, record):
        during = read_serve_record(db)
    after = read_serve_record(db)

    assert during == record and during.origin == "http://127.0.0.1:8710"
    assert after is None


def test_a_torn_or_foreign_record_reads_as_none(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    (tmp_path / "hive.sqlite3.serve.json").write_text('{"pid": 1', encoding="utf-8")

    assert read_serve_record(db) is None


def test_an_ipv6_loopback_record_brackets_its_host() -> None:
    record = ServeRecord(pid=1, host="::1", port=8710, started_at=_STARTED)

    assert record.origin == "http://[::1]:8710"


async def test_hive_serve_publishes_the_listener_it_serves_on(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    assert (await set_password(path)).exit_code == 0

    async with serving_stand(path) as (stand, entrance):
        record = read_serve_record(stand.db)
        assert record is not None
        async with httpx.AsyncClient() as http:
            answered = await http.get(f"{record.origin}/v1/openapi.json")
        port = entrance.listeners.loopback_port

    assert record.port == port
    assert answered.status_code == 200
    assert read_serve_record(_db(path)) is None


def test_hive_serve_refuses_while_another_process_holds_the_hive(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    # build_served_hive runs its own event loops, so it is built before the test's one.
    served = build_served_hive(load_manifest(path, {}), environ={}, clock=SystemClock())

    with (
        hold_serve_lock(_db(path), "an offline hive entrance step"),
        pytest.raises(HiveBusyError, match="an offline hive entrance step"),
    ):
        asyncio.run(_serve_once(served))


async def _serve_once(served: ServedHive) -> None:
    """Enter serve_hive; reaching the body at all means the refusal failed."""
    async with serve_hive(served):
        pytest.fail("hive serve must not serve beside an offline step")


def _db(manifest_path: Path) -> Path:
    """The Hive's database file, as the manifest names it."""
    manifest = load_manifest(manifest_path, {})
    return manifest.resolve_path(manifest.hive.db)
