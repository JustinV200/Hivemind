"""Run a real Hive's authentication lifecycle end to end, over one SQLite file.

The real thing wherever it can be (CLAUDE.md: prefer a real task over a synthetic one): the
operator is bootstrapped for real (the console's key minted, wrapped under the password with the
64 MiB Argon2id, the hash stored), the console unwraps its key and logs in on loopback, a program
is invited, redeems and is approved by the console, then logs in on the remote listener from a
network it never used, so the travel lock flags it; it cannot step up, so its request is held
until the console steps up (the real password again) and confirms it; the Guard reduces the
Entrance; the Hive restarts and comes back reduced, the console's volatile session gone; the
console logs in again, steps up and reopens; and no trail event carries a credential.

Fits into the Hive:
    Covers hivemind.entrance.auth as a whole (its sub-packages together), as
    tests/unit/entrance/enrol/test_flow.py covers enrolment.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for every step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from builders.entrance import (
    ADDRESS,
    LOOPBACK,
    PASSWORD,
    REMOTE,
    AuthRig,
    RecordingListener,
    RecordingStreams,
    approval,
    auth_rig,
    program_login,
    redeem_program,
    sign_b64url,
    signed_request,
    sqlite_enrolment,
)
from pydantic import JsonValue

from hivemind.common.secrets import FileSecretStore
from hivemind.common.sqlite import connect
from hivemind.entrance.auth import (
    ActionKind,
    AuthenticatedSession,
    DeviceProof,
    FakePeerEndpointSource,
    OpenedSession,
    StepUpReason,
    authenticate_request,
    confirm,
    hold,
    login_string,
    requires_step_up,
    step_up,
    step_up_challenge,
)
from hivemind.entrance.enrol import (
    ConsoleDeps,
    EnrolledDevice,
    approve,
    bootstrap_operator,
    mint_invite,
    unlock_console_key,
)
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.entrance.reducer import EntranceMode, EntranceReducer, ReduceReason, ReducerSeams
from hivemind.entrance.store import SqliteEntranceStore
from waggle.signing import Ed25519Signer

_HOME = "203.0.113.0/24"  # The program's server's network, as tailscaled reports it.


async def _admit(auth: AuthRig, opened: OpenedSession, key: Ed25519Signer) -> AuthenticatedSession:
    """Authenticate one signed request on ``opened``, from the listener it was opened on."""
    arrival = REMOTE if opened.session.listener is REMOTE.listener else LOOPBACK
    request = signed_request(opened.token, key, auth.clock.now(), arrival)
    return await authenticate_request(auth.book, request, auth.clock.now())


async def _stepped_up(
    auth: AuthRig, opened: OpenedSession, key: Ed25519Signer
) -> AuthenticatedSession:
    """Step the console's session up with a fresh proof and the real password."""
    session = await _admit(auth, opened, key)
    challenge = step_up_challenge(auth.deps, session)
    hive_id = auth.deps.records.identity.hive_id
    signature = sign_b64url(key, login_string(hive_id, session.device.id, challenge.nonce))
    proof = DeviceProof(session.device.id, challenge.nonce, signature=signature)
    await step_up(auth.deps, session, proof, PASSWORD, LOOPBACK)
    return await _admit(auth, opened, key)


async def _restart(auth: AuthRig, db: Path, source: FakePeerEndpointSource) -> AuthRig:
    """Stop and start the Entrance: new connections, store, book and volatile sessions."""
    reopened = await SqliteEntranceStore.create(connect(db), auth.clock)
    records = replace(auth.enrolment.deps.records, store=reopened)
    rig = replace(auth.enrolment, deps=replace(auth.enrolment.deps, records=records))
    return await auth_rig(rig, source, cheap_password=False)


def _reducer(auth: AuthRig) -> EntranceReducer:
    """A reducer over the rig, with the app's seams recorded."""
    seams = ReducerSeams(RecordingListener(), RecordingStreams())
    return EntranceReducer(auth.deps.records, auth.book, seams)


@dataclass(frozen=True, slots=True)
class _Cast:
    """The console and the program, with the keys they hold and their first sessions."""

    console: EnrolledDevice
    console_key: Ed25519Signer
    console_login: OpenedSession
    program: EnrolledDevice
    program_key: Ed25519Signer
    first: OpenedSession
    code: str


async def _bootstrap(auth: AuthRig, tmp_path: Path) -> tuple[EnrolledDevice, Ed25519Signer]:
    """Bootstrap the operator for real: the console's key wrapped under the 64 MiB Argon2id."""
    secrets = FileSecretStore(tmp_path / "secrets")
    records = auth.deps.records
    deps = ConsoleDeps(records.store, secrets, auth.hasher, records.clock, records.identity)
    console = await bootstrap_operator(deps, PASSWORD)
    return console, await unlock_console_key(secrets, auth.hasher, PASSWORD)


async def _cast(auth: AuthRig, tmp_path: Path) -> _Cast:
    """The console logs in on loopback and admits a program, which logs in remotely."""
    console, console_key = await _bootstrap(auth, tmp_path)
    console_login = await program_login(auth, console, console_key)
    minted = await mint_invite(auth.deps.enrolment, "backup job", console.id)
    program_key = Ed25519Signer.generate()
    redeemed = await redeem_program(auth.enrolment, minted, program_key)
    request = approval(name="backup job", actor=console.id)
    program = await approve(auth.deps.enrolment, redeemed.device_id, request)
    first = await program_login(auth, program, program_key, REMOTE)
    return _Cast(console, console_key, console_login, program, program_key, first, minted.code)


async def _clear_network(auth: AuthRig, cast: _Cast) -> OpenedSession:
    """The program cannot step up: its network waits for the console, stepped up, to clear it."""
    network: dict[str, JsonValue] = {"network": cast.first.session.network}
    records = auth.deps.records
    pending_id = await hold(auth.deps.enrolment, cast.program, ActionKind.NEW_NETWORK, network)
    operator = await _stepped_up(auth, cast.console_login, cast.console_key)
    held = await confirm(records, pending_id, operator)
    assert auth.deps.guards.travel is not None
    await auth.deps.guards.travel.trust(held.device_id, str(held.payload["network"]))
    return await program_login(auth, cast.program, cast.program_key, REMOTE)


async def test_a_real_hive_logs_in_holds_reduces_restarts_and_reopens(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    source = FakePeerEndpointSource({ADDRESS: _HOME})
    enrolment = await sqlite_enrolment(db)
    auth = await auth_rig(enrolment, source, cheap_password=False)
    cast = await _cast(auth, tmp_path)
    flagged = await _admit(auth, cast.first, cast.program_key)
    assert requires_step_up(flagged, step_up_spend=5.0) is StepUpReason.NEW_NETWORK
    cleared = await _clear_network(auth, cast)
    assert (
        requires_step_up(await _admit(auth, cleared, cast.program_key), step_up_spend=5.0) is None
    )

    # The Guard orders a reduction; the program's remote session dies with it.
    assert await _reducer(auth).reduce(ReduceReason.GUARD_ORDER, "system")
    with pytest.raises(AuthenticationFailedError):
        await _admit(auth, cleared, cast.program_key)

    # A restart comes back reduced; the console's session lived in memory and is gone.
    auth = await _restart(auth, db, source)
    assert await _reducer(auth).start_mode() is EntranceMode.REDUCED
    with pytest.raises(AuthenticationFailedError):
        await _admit(auth, cast.console_login, cast.console_key)
    with pytest.raises(AuthenticationFailedError):
        await program_login(auth, cast.program, cast.program_key, REMOTE)

    # The console logs in again, steps up on loopback and reopens; the program is back.
    again = await program_login(auth, cast.console, cast.console_key)
    assert await _reducer(auth).reopen(await _stepped_up(auth, again, cast.console_key))
    back = await program_login(auth, cast.program, cast.program_key, REMOTE)
    assert not back.session.needs_step_up

    sessions = (cast.console_login, cast.first, cleared, again, back)
    secrets_seen = [PASSWORD, cast.code, *(opened.token for opened in sessions)]
    for event in await enrolment.events():
        dumped = json.dumps(event.model_dump(mode="json"))
        assert [value for value in secrets_seen if value in dumped] == [], event.kind
