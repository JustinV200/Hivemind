"""Test `hive supersede --settle`: the operator's word on a Supersedure a crash left open.

ADR-0065: an old Queen whose Supersedure has no outcome on her own trail never starts until the
human settles it, offline, after checking the candidate. The settle needs the operator password
(the console key must open), records the verdict on this node's trail as the human, and refuses
when nothing is unfinished. The move itself (the console, the step-up, the phrase) is tested at
its route (tests/unit/entrance/routes/test_supersede.py).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests hivemind.cli.supersede.command.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/runbooks/supersedure.md, "After a crash".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from builders.entrance.stand import PASSWORD, Terminal, set_password, stand_manifest

from hivemind.cli.stores import open_trail
from hivemind.manifest import load_manifest
from hivemind.queen.supersedure import STARTED_KIND, Recorder, latest
from waggle.clock import SystemClock


async def test_settling_needs_the_password_and_an_unfinished_supersedure(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    assert (await set_password(path)).exit_code == 0
    manifest = load_manifest(path, {})
    db = manifest.resolve_path(manifest.hive.db)
    trail = await asyncio.to_thread(open_trail, db)
    recorder = Recorder(trail, manifest.hive.id, manifest.hive.node_id, SystemClock())
    await recorder.record(
        STARTED_KIND,
        "device_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        candidate_node_id="node_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        sequence=1,
        grace_until=datetime(2026, 10, 27, tzinfo=UTC).isoformat(),
        old_address="ws://127.0.0.1:9000",
    )
    terminal = Terminal()

    async def settle_with(password: str) -> int:
        args = ("supersede", "--settle", "aborted", "--manifest", str(path), "--password-stdin")
        return (await terminal.hive(*args, stdin=f"{password}\n")).exit_code

    wrong = await settle_with("not the password")
    unsettled = await latest(trail)
    right = await settle_with(PASSWORD)
    settled = await latest(trail)
    again = await settle_with(PASSWORD)

    assert (wrong, right, again) == (1, 0, 1)
    assert unsettled is not None and unsettled.outcome is None
    assert settled is not None and settled.outcome == "queen.supersedure_aborted"
