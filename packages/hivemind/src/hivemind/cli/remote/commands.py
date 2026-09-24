"""Provide ``hive remote enrol|profiles|forget``, and the remote halves of ``run`` and ``inbox``.

A laptop's ``hive`` is an enrolled device like any other client of the Hive Entrance (ADR-0033).
``hive remote enrol URL --name NAME [--code CODE] [--hive HIVE]`` mints its key and redeems the
invite the operator read out, then says what the operator must do: approve it on the Hive Stand,
comparing the key fingerprint printed here. ``profiles`` lists the Hives this laptop is enrolled
with; ``forget`` drops one (and its key). ``remote_run`` is ``hive run --remote "goal"``: the goal
submitted through the Entrance and followed to its end, the Queen's lines printed as they come,
exit 0 when it finished, 1 when it was refused, 2 when the timeout ended the follow first.
``remote_inbox``, ``remote_answer`` and ``remote_acknowledge`` are ``hive inbox --remote``: what
waits on the human, an answer to a question, an Alarm acknowledged. Each is a thin layer over
``hivemind.cli.remote``'s enrolment, session and goal follower.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. ``app`` is registered on the root
    ``hive`` application by ``hivemind.cli.app``; the ``remote_*`` functions are called by
    ``hivemind.cli.run`` and ``hivemind.cli.readback.inbox`` when ``--remote`` is given. Calls into
    this package's modules and the Landing Board's models.

Key invariants:
    - Nothing here prints a key, a token, a password or an invite code.
    - Every refusal is one stderr line and exit 1 (2 for bad usage).

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from hivemind.cli.landing import (
    CarriedOption,
    SignedIn,
    carried_command,
    carried_path,
    carried_text,
    describe,
    shown,
)
from hivemind.cli.remote.enrol import EnrolmentOrder, enrol_device
from hivemind.cli.remote.goals import FollowOutcome, GoalAsk, submit_and_follow
from hivemind.cli.remote.profiles import DEFAULT_PROFILE, ProfileStore
from hivemind.cli.remote.render import chat_lines, inbox_lines, outcome_line
from hivemind.cli.remote.session import PROFILE, run_remote
from hivemind.common.errors import HiveMindError
from hivemind.entrance.models import (
    AcknowledgedView,
    AnswerBody,
    AnsweredView,
    ChatLine,
    GoalAccepted,
    InboxView,
)
from waggle.clock import SystemClock

CA_FILE = CarriedOption(
    key="hivemind.cli.remote.ca_file",
    decls=("--ca-file",),
    help="A PEM file of the only CA the Entrance's TLS may chain to (a Hive's own authority); "
    "omitted, the system's trust store.",
)
EnrolCommand = carried_command(PROFILE, CA_FILE)

app = typer.Typer(
    name="remote",
    help="Enrol this device with a remote Hive, and keep its profiles.",
    no_args_is_help=True,
)

__all__ = [
    "CA_FILE",
    "RemoteRun",
    "app",
    "remote_acknowledge",
    "remote_answer",
    "remote_inbox",
    "remote_run",
]


@dataclass(frozen=True, slots=True)
class RemoteRun:
    """Everything ``hive run --remote`` was told.

    Attributes:
        goal: The goal, its clearance and tier.
        timeout_s: The longest to follow it.
        as_json: Print only the goal request's final state, as JSON.
        profile: Which remote Hive.
        from_stdin: ``--password-stdin`` was given.
    """

    goal: GoalAsk
    timeout_s: float
    as_json: bool
    profile: str
    from_stdin: bool


@app.command("enrol", cls=EnrolCommand)
def enrol_command(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="The Entrance's URL, or the whole invite link.")],
    name: Annotated[str, typer.Option("--name", help="What this device calls itself.")],
    code: Annotated[
        str | None, typer.Option("--code", help="The invite code (or give the invite link).")
    ] = None,
    hive: Annotated[
        str | None, typer.Option("--hive", help="The Hive's id, printed beside the invite code.")
    ] = None,
) -> None:
    """Enrol this device: mint its key, redeem the invite, and wait for approval at the Stand."""
    order = EnrolmentOrder(
        link=url,
        code=code,
        hive_id=hive,
        name=name,
        profile=carried_text(ctx, PROFILE) or DEFAULT_PROFILE,
        ca_file=carried_path(ctx, CA_FILE),
    )
    try:
        profile = asyncio.run(enrol_device(ProfileStore.for_user(), order, SystemClock()))
    except (HiveMindError, ValidationError, OSError, sqlite3.Error) as exc:
        _refuse("remote enrol", exc)
    typer.echo(f"Enrolled with Hive {profile.hive_id} as device {profile.device_id} ")
    typer.echo(f"  (profile {profile.name!r}; key fingerprint {profile.fingerprint}).")
    typer.echo("It waits for approval. On the Hive Stand the operator compares this fingerprint:")
    typer.echo(f"  hive entrance approve {profile.device_id} --spend-cap 5 --interactive")
    typer.echo('Once approved: hive run --remote "your goal"')


@app.command("profiles")
def profiles_command() -> None:
    """List the remote Hives this device is enrolled with."""
    store = ProfileStore.for_user()
    names = store.names()
    if not names:
        typer.echo("No remote profile yet: hive remote enrol ...")
    for name in names:
        try:
            profile = store.load(name)
        except HiveMindError as exc:
            typer.echo(f"{name}: {describe(exc)}")
            continue
        typer.echo(
            f"{profile.name}: {profile.entrance_url} Hive {profile.hive_id} device "
            f"{profile.device_id} ({profile.fingerprint})"
        )


@app.command("forget")
def forget_command(
    name: Annotated[str, typer.Argument(help="The profile to forget.")],
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """Forget a remote profile and delete its device key (the Hive still lists the device)."""
    if not yes and not typer.confirm(f"Delete profile {name!r} and its device key?"):
        raise typer.Exit(code=1)
    try:
        existed = asyncio.run(ProfileStore.for_user().forget(name))
    except (HiveMindError, OSError) as exc:
        _refuse("remote forget", exc)
    typer.echo(f"Forgot {name!r}." if existed else f"No profile {name!r}.")
    if existed:
        typer.echo(
            "Ask the operator to revoke the device on the Hive Stand (hive entrance revoke)."
        )


def remote_run(order: RemoteRun) -> NoReturn:
    """Submit a goal through the remote Entrance and follow it; exit 0, 1 or 2 by how it ended.

    Args:
        order: What ``hive run --remote`` was told.

    Raises:
        typer.Exit: Always: 0 finished, 1 refused (or anything refused on the way), 2 the
            timeout ended the follow first.
    """
    printer = _Printer(order.as_json)

    async def work(board: SignedIn) -> FollowOutcome:
        """Submit and follow."""
        return await submit_and_follow(board, order.goal, order.timeout_s, printer)

    outcome = run_remote("run --remote", order.profile, order.from_stdin, work)
    if order.as_json:
        typer.echo(outcome.view.model_dump_json(indent=2))
    else:
        typer.echo(outcome_line(outcome))
    if outcome.timed_out:
        raise typer.Exit(code=2)
    raise typer.Exit(code=1 if outcome.view.refused else 0)


def remote_inbox(profile: str, from_stdin: bool, as_json: bool) -> None:
    """Print the questions and Alarms waiting on the human at the remote Hive.

    Args:
        profile: Which remote Hive.
        from_stdin: ``--password-stdin`` was given.
        as_json: Print the inbox as JSON.
    """

    async def read(board: SignedIn) -> InboxView:
        """Read the inbox (C2)."""
        return await board.call("GET", "/v1/inbox", None, InboxView)

    inbox = run_remote("inbox --remote", profile, from_stdin, read)
    if as_json:
        typer.echo(inbox.model_dump_json(indent=2))
        return
    for line in inbox_lines(inbox):
        typer.echo(line)


def remote_answer(profile: str, from_stdin: bool, question_id: str, answer: AnswerBody) -> None:
    """Answer a question waiting on the human; the Queen resumes its task.

    Args:
        profile: Which remote Hive.
        from_stdin: ``--password-stdin`` was given.
        question_id: The question.
        answer: The text, and the option picked for a closed question.
    """

    async def send(board: SignedIn) -> AnsweredView:
        """Answer on the Landing Board."""
        path = f"/v1/inbox/questions/{question_id}/answer"
        return await board.call("POST", path, answer, AnsweredView)

    answered = run_remote("inbox --remote answer", profile, from_stdin, send)
    typer.echo(f"answered: task {answered.task_id} is now {answered.task_status.value}")


def remote_acknowledge(profile: str, from_stdin: bool, alarm_id: str) -> None:
    """Acknowledge an Alarm that reached the human; it is withdrawn from every device.

    Args:
        profile: Which remote Hive.
        from_stdin: ``--password-stdin`` was given.
        alarm_id: The Alarm.
    """

    async def send(board: SignedIn) -> AcknowledgedView:
        """Acknowledge on the Landing Board."""
        path = f"/v1/inbox/alarms/{alarm_id}/acknowledge"
        return await board.call("POST", path, None, AcknowledgedView)

    acknowledged = run_remote("inbox --remote acknowledge", profile, from_stdin, send)
    typer.echo(
        f"acknowledged {alarm_id}" if acknowledged.acknowledged else f"{alarm_id} was not waiting"
    )


class _Printer:
    """Print a follow as it happens (nothing but the end in JSON mode)."""

    def __init__(self, quiet: bool) -> None:
        """Print lines unless ``quiet`` (JSON mode prints only the final state)."""
        self._quiet = quiet

    def submitted(self, accepted: GoalAccepted) -> None:
        """Say the goal request was committed."""
        if not self._quiet:
            typer.echo(f"goal request {accepted.id} received ({accepted.state.value})")

    def line(self, line: ChatLine) -> None:
        """Print one of the Queen's lines."""
        if not self._quiet:
            for text in chat_lines(line):
                typer.echo(text)

    def note(self, text: str) -> None:
        """Print a note about the follow itself."""
        if not self._quiet:
            typer.echo(shown(text, 300))


def _refuse(verb: str, exc: Exception) -> NoReturn:
    """Print why ``hive <verb>`` could not act, and exit 1."""
    typer.echo(f"hive {verb} refused: {describe(exc)}", err=True)
    raise typer.Exit(code=1) from exc
