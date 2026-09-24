"""Test hivemind.cli.remote.commands through the hive application, from a laptop's terminal.

``hive serve``'s own composition runs the Hive Stand; a second terminal whose config directory is
the test's own ("the laptop") enrols, is approved at the Stand, runs a goal and reads its inbox,
all through ``CliRunner`` as a person would type them. A laptop that can reach no enrolment
listener enrols ``--offline``: it makes its key and certificate request, sends nothing, and says
what the operator runs; its profile waits for its certificate, and ``set-url`` moves it.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/commands.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

from builders.entrance.auth import PASSWORD
from builders.entrance.goals import goal_responder
from builders.entrance.stand import (
    Stand,
    Terminal,
    json_of,
    laptop_terminal,
    serving_stand,
    set_password,
    stand_manifest,
)

from hivemind.entrance.models import InboxView

_STDIN = f"{PASSWORD}\n"


async def _enrol(stand: Stand, laptop: Terminal) -> str:
    """Invite at the Stand, enrol from the laptop's terminal; return the device id."""
    invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
    enrolled = await laptop.hive(
        *("remote", "enrol", invite["url"], "--hive", invite["hive_id"], "--name", "laptop")
    )
    assert enrolled.exit_code == 0, enrolled.output
    return str(invite["device_id"])


async def test_enrol_prints_the_fingerprint_and_the_approval_the_operator_owes(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
        enrolled = await laptop.hive(
            *("remote", "enrol", invite["url"], "--hive", invite["hive_id"], "--name", "laptop")
        )
        pending = await stand.entrance("pending")
        profiles = await laptop.hive("remote", "profiles")
        refused = await laptop.hive("run", "--remote", "a goal", "--password-stdin", stdin=_STDIN)
        forgot = await laptop.hive("remote", "forget", "default", "--yes")
        after = await laptop.hive("remote", "profiles")

    assert enrolled.exit_code == 0, enrolled.output
    fingerprint = enrolled.output.split("key fingerprint ")[1].split(")")[0]
    assert fingerprint in pending.output
    assert f"hive entrance approve {invite['device_id']}" in enrolled.output
    assert invite["code"] not in enrolled.output
    assert "default: http://localhost:" in profiles.output and fingerprint in profiles.output
    # Still pending: the login is refused, and the refusal lists what that may mean.
    assert refused.exit_code == 1
    assert "hive run --remote refused" in refused.output and "pending approval" in refused.output
    assert "Forgot 'default'" in forgot.output and "No remote profile yet" in after.output


async def test_run_remote_follows_a_goal_to_its_end_and_exits_0(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path, goal_responder()) as (stand, _entrance):
        device_id = await _enrol(stand, laptop)
        await stand.entrance("approve", device_id, "--spend-cap", "20", "--interactive", "--yes")
        ran = await laptop.hive(
            *("run", "--remote", "write a haiku", "--password-stdin", "--timeout", "30"),
            stdin=_STDIN,
        )
        as_json = await laptop.hive(
            *("run", "--remote", "and another", "--password-stdin", "--json"), stdin=_STDIN
        )

    assert ran.exit_code == 0, ran.output
    assert "goal request goalreq_" in ran.output and "received (RECEIVED)" in ran.output
    assert " finished at " in ran.output
    assert as_json.exit_code == 0, as_json.output
    assert json_of(as_json)["finished_at"] is not None


async def test_inbox_remote_reads_the_inbox_and_acknowledges_as_the_device(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        device_id = await _enrol(stand, laptop)
        await stand.entrance("approve", device_id, "--spend-cap", "5", "--yes")
        listed = await laptop.hive("inbox", "--remote", "--password-stdin", stdin=_STDIN)
        as_json = await laptop.hive("inbox", "--remote", "--password-stdin", "--json", stdin=_STDIN)
        acknowledged = await laptop.hive(
            *("inbox", "--remote", "--password-stdin", "acknowledge"),
            "alarm_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
            stdin=_STDIN,
        )

    assert listed.exit_code == 0, listed.output
    # No goal ran, so no question waits; an Alarm may (a Warden's heartbeat late on a busy
    # machine reaches the human like any other), and the listing then shows it instead.
    assert "Nothing waits on the human." in listed.output or "alarm alarm_" in listed.output
    assert InboxView.model_validate(json_of(as_json)).questions == []
    assert acknowledged.exit_code == 0, acknowledged.output
    assert "was not waiting" in acknowledged.output


async def test_the_remote_commands_refuse_cleanly_without_a_profile(tmp_path: Path) -> None:
    laptop = laptop_terminal(tmp_path / "laptop")

    inbox = await laptop.hive("inbox", "--remote", "--password-stdin", stdin=_STDIN)
    run = await laptop.hive("run", "--remote", "a goal", "--profile", "garden")
    local_ack = await laptop.hive("inbox", "acknowledge", "alarm_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3")

    assert inbox.exit_code == 1 and "No remote profile 'default'" in inbox.output
    assert run.exit_code == 1 and "No remote profile 'garden'" in run.output
    assert local_ack.exit_code == 1 and "hive inbox --remote acknowledge" in local_ack.output


async def test_offline_enrolment_sends_nothing_and_its_profile_waits_for_its_certificate(
    tmp_path: Path,
) -> None:
    laptop, request = laptop_terminal(tmp_path / "laptop"), tmp_path / "field.csr"
    hive = ("--hive", "hive_01DXF6DT00S8CWQEAHWB40R349")
    offline = ("remote", "enrol", "https://192.0.2.2:8711", "--offline", *hive)
    named = ("--name", "field laptop")

    with_code = await laptop.hive(*offline, *named, "--code", "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YY")
    made = await laptop.hive(*offline, *named, "--csr-out", str(request))
    waiting = await laptop.hive("remote", "profiles")
    refused = await laptop.hive("inbox", "--remote", "--password-stdin", stdin=_STDIN)
    moved = await laptop.hive("remote", "set-url", "https://192.0.2.3:8711")
    after = await laptop.hive("remote", "profiles")

    assert with_code.exit_code == 1 and "needs no invite code" in with_code.output
    assert made.exit_code == 0 and "nothing was sent" in made.output, made.output
    assert "hive entrance register --name 'field laptop' --public-key " in made.output
    assert f"--csr {request.name}" in made.output and "certificate import" in made.output
    assert request.read_text(encoding="ascii").startswith("-----BEGIN CERTIFICATE REQUEST-----")
    assert "(waits for its certificate)" in waiting.output
    assert "no client certificate" in waiting.output
    assert refused.exit_code == 1 and "waits for its certificate" in refused.output
    assert moved.exit_code == 0 and "https://192.0.2.3:8711" in after.output
