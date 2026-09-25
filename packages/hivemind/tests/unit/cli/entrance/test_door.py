"""Test hivemind.cli.entrance.door: status, reduce and open over a real hive serve; expose, dry.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/door.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

from builders.entrance.stand import json_of, serving_stand, set_password, stand_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app

runner = CliRunner()


async def test_status_then_reduce_then_open_walk_the_door_through_its_modes(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, entrance):
        before = json_of(await stand.entrance("status", "--json"))
        shown = await stand.entrance("status")
        reduced = await stand.entrance("reduce")
        again = await stand.entrance("reduce")
        reopened = await stand.entrance("open")
        after = json_of(await stand.entrance("status", "--json"))
        port = entrance.listeners.loopback_port

    assert before["mode"] == "OPEN" and before["exposed"] is False
    assert before["loopback"] == f"http://127.0.0.1:{port}"
    assert before["devices"] == {"APPROVED": 1}
    assert before["plan"]["mode"] == "loopback" and before["plan_refused"] is None
    assert "Entrance: OPEN" in shown.output and "devices: 1 APPROVED" in shown.output
    assert reduced.exit_code == 0 and "Reduced" in reduced.output
    assert "already REDUCED" in again.output
    # Reopening is loopback-only and needs a step-up: the console gave it with its password.
    assert reopened.exit_code == 0, reopened.output
    assert "Reopened" in reopened.output
    assert after["mode"] == "OPEN"


def test_expose_prints_the_plan_this_host_yields_and_starts_nothing(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)

    shown = runner.invoke(app, ["entrance", "expose", "--manifest", str(path)])
    as_json = runner.invoke(app, ["entrance", "expose", "--manifest", str(path), "--json"])

    assert shown.exit_code == 0, shown.output
    assert "Exposure: loopback" in shown.output
    assert "loopback listener: 127.0.0.1:a port the system picks" in shown.output
    assert "remote listener: none" in shown.output and "Nothing was started." in shown.output
    assert '"remote": null' in as_json.output


def test_expose_names_the_rule_a_mode_breaks_and_exits_2(tmp_path: Path) -> None:
    lan = (
        'expose = "lan"\nremote_bind = "192.0.2.5:8711"\n'
        'public_url = "https://hive.example.net"\nmutual_tls = false\n'
    )
    path = stand_manifest(tmp_path, lan)

    refused = runner.invoke(app, ["entrance", "expose", "--manifest", str(path)])

    assert refused.exit_code == 2
    assert "refuses to start with expose = 'lan'" in refused.output
    assert "mutual" in refused.output
