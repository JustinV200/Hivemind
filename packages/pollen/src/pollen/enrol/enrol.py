"""Run the enrolment ceremony: dial, present the token, check the accept, persist, close.

Enrolment is one invite, one signed request and one signed accept (enrolment.md). The Pollen Packet
(the thin gateway program on a device the Hive borrows) reads the invite, mints its ids and its
Ed25519 key and writes the key to disk *before* the first dial, so a crash can never leave the Hive
holding a key the device lost. It then dials the Queen's address (the Hive's central orchestrator)
with the certificate fingerprint from the invite, which replaces chain and name validation and is
compared before a single Waggle frame is sent, so the token never reaches a server that cannot
prove it holds the certificate the operator pinned. The only frame it sends is the signed
``swarm.enrol_request``; the only frame it accepts back is signed by the Hive node the invite
pinned. A ``hive.swarm.enrol_denied`` ends the attempt with nothing persisted. An accept is used
only if it matches what the invite pinned (the Hive's id and key, this device's id), grants no
more access than was asked for and names a tier a Real Cell can hold; then the state is written
atomically, the connection is closed and the invite is forgotten. Nothing about a failed
enrolment is left on disk but the unused key.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli
    (``pollen enrol``); calls into pollen.enrol.invite and request, pollen.agent.link (the pinned
    connection), pollen.state and waggle.

Key invariants:
    - The key exists on disk before the first dial, and the invite token is sent only once, on a
      connection whose certificate matched the pin.
    - Nothing is persisted unless every check on the accept passed; a device never holds half an
      enrolment.
    - The invite string and the token are never logged, stored or put in an error message.
    - The enrolment request is never queued in an outbox.

See Also:
    - docs/waggle/enrolment.md sections 4, 5, 7 and 9.
    - pollen.agent.link for the connection this ceremony reuses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pollen.agent.link import Link, LiveVerifier
from pollen.enrol.invite import Invite
from pollen.enrol.request import DeviceIdentity, build_request
from pollen.errors import EnrolmentDeniedError, EnrolmentError
from pollen.platform.base import PlatformShim
from pollen.state import DataLayout, PacketState, create_key, save_state
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectFailedError, TlsPinMismatchError, TransportError, WaggleError
from waggle.ids import MessageId, new_device_id, new_node_id
from waggle.messages.control import ErrorMessage
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import EnrolAccept, EnrolRequest, RuntimeLevel
from waggle.signing import Ed25519Signer, public_key_hex

log = logging.getLogger(__name__)

ENROL_REPLY_TIMEOUT_S = (
    30.0  # How long the Queen has to answer the request before the attempt fails.
)

__all__ = ["ENROL_REPLY_TIMEOUT_S", "EnrolPlan", "enrol_device"]


@dataclass(frozen=True, slots=True)
class EnrolPlan:
    """What one enrolment needs, bundled so the ceremony takes few parameters."""

    invite: Invite  # The parsed invite string.
    layout: DataLayout  # Where the key and the state go.
    shim: PlatformShim  # The platform facts the request reports.
    clock: Clock  # Mints ids and stamps the request.
    requested_access: AccessLevel = (
        AccessLevel.FULL
    )  # What to ask for; the operator's cap may lower it.
    is_forced: bool = False  # Enrol again over an existing enrolment, replacing its key.


async def enrol_device(plan: EnrolPlan) -> PacketState:
    """Run the ceremony to the end and persist the result.

    Args:
        plan: The invite, the data directory, the platform and the clock.

    Returns:
        The state now on disk; the packet can run from it.

    Raises:
        EnrolmentDeniedError: The Queen refused the request.
        EnrolmentError: The device is already enrolled, the Queen could not be reached or did not
            answer, the certificate was not the pinned one, or the accept did not match the invite.
        StateError: The key or the state could not be written.
    """
    layout, invite = plan.layout, plan.invite
    layout.ensure()
    if layout.state.exists() and not plan.is_forced:
        raise EnrolmentError("This device is already enrolled; enrol again only with --force.")
    # Written before the first dial: the Hive can then never hold a key this device has lost. An
    # unused key from an earlier failed attempt is replaced; an existing enrolment needs --force.
    signer = create_key(layout.key, replace=True)
    identity = _identity(plan, signer)
    request = await build_request(invite, identity, plan.shim, layout.root, plan.requested_access)
    # Until the accept, the only node this device trusts is the one the invite pinned.
    verifier = LiveVerifier({invite.hive_node_id: bytes.fromhex(invite.hive_key)})
    link = Link(invite.url, invite.tls_sha256, signer, verifier, plan.clock)
    try:
        accept = await _exchange(link, plan, identity, request)
        state = _state_from(plan, identity, accept)
        save_state(layout.state, state)  # Atomic; the enrolment is real only from here.
    finally:
        await _close(link)
    log.info("pollen.enrolled device=%s cell=%s", state.device_id, state.cell_id)
    return state


def _identity(plan: EnrolPlan, signer: Ed25519Signer) -> DeviceIdentity:
    """Mint the device's bee address and node id; the key was minted by ``create_key``."""
    return DeviceIdentity(
        device_id=new_device_id(plan.clock),
        node_id=new_node_id(plan.clock),
        public_key_hex=public_key_hex(signer.public_key_bytes),
    )


async def _exchange(
    link: Link, plan: EnrolPlan, identity: DeviceIdentity, request: EnrolRequest
) -> EnrolAccept:
    """Dial, send the request and wait for the one answer that matters."""
    invite = plan.invite
    try:
        await link.connect()
    except TlsPinMismatchError as exc:
        raise EnrolmentError(
            "The listener's certificate is not the one the invite pinned; nothing was sent."
        ) from exc
    except (ConnectFailedError, TimeoutError) as exc:
        raise EnrolmentError("The Queen could not be reached at the invite's address.") from exc
    hop = Hop(sender=identity.device_id, recipient=invite.hive_id, node_id=identity.node_id)
    sent = wrap(request, hop, clock=plan.clock)
    try:
        await link.send(sent)
        async with asyncio.timeout(ENROL_REPLY_TIMEOUT_S):  # Network: the Queen's decision.
            return await _await_answer(link, sent.id, identity, invite)
    except TimeoutError as exc:
        raise EnrolmentError("The Queen did not answer the enrolment request in time.") from exc
    except (TransportError, WaggleError) as exc:
        raise EnrolmentError("The connection failed before the Queen answered.") from exc


async def _await_answer(
    link: Link, request_id: MessageId, identity: DeviceIdentity, invite: Invite
) -> EnrolAccept:
    """Read frames until the accept or the denial correlated to the request arrives."""
    async for envelope in link.receive():
        if envelope.correlation_id != request_id:
            continue  # Not an answer to this request: nothing else is trusted yet.
        payload = envelope.payload
        if isinstance(payload, ErrorMessage):
            _refuse(payload)
        if isinstance(payload, EnrolAccept):
            _check_accept(envelope, payload, identity, invite)
            return payload
    raise EnrolmentError("The connection closed before the Queen answered.")


def _refuse(error: ErrorMessage) -> None:
    """Turn the Queen's control.error into the exception that ends the attempt."""
    if error.code == EnrolmentDeniedError.code:
        raise EnrolmentDeniedError(error.message)
    raise EnrolmentError(f"The Queen refused the request with code {error.code}.")


def _check_accept(
    envelope: Envelope, accept: EnrolAccept, identity: DeviceIdentity, invite: Invite
) -> None:
    """Hold the accept to what the invite pinned and what was asked for; raise if it differs."""
    pinned = (envelope.sender, envelope.node_id, envelope.recipient) == (
        invite.hive_id,
        invite.hive_node_id,
        identity.device_id,
    )
    matches = (accept.device_id, accept.hive_id, accept.hive_key_hex) == (
        identity.device_id,
        invite.hive_id,
        invite.hive_key,
    )
    # A listed node that shares the Hive node's id must carry the pinned key, or the accept would
    # quietly replace the key the operator pinned.
    rekeyed = any(
        node.node_id == invite.hive_node_id and node.public_key_hex != invite.hive_key
        for node in accept.trusted_nodes
    )
    if not (pinned and matches) or rekeyed:
        raise EnrolmentError("The accept does not match what the invite pinned; it was discarded.")


def _state_from(plan: EnrolPlan, identity: DeviceIdentity, accept: EnrolAccept) -> PacketState:
    """Build the state to persist from the invite's pins and the accept's grants."""
    if accept.access_level.rank > plan.requested_access.rank:
        raise EnrolmentError("The accept grants more access than was asked for; it was discarded.")
    invite = plan.invite
    return PacketState(
        device_id=identity.device_id,
        node_id=identity.node_id,
        queen_url=invite.url,
        tls_pin_sha256=invite.tls_sha256,
        hive_id=invite.hive_id,
        hive_node_id=invite.hive_node_id,
        hive_key_hex=invite.hive_key,
        trusted_nodes=accept.trusted_nodes,
        cell_id=accept.cell_id,
        warden_id=accept.warden_id,
        level=RuntimeLevel.GATEWAY,
        access_level=accept.access_level,
        comb_shield=accept.comb_shield,
        granted_capabilities=accept.granted_capabilities,
        offline_limit_s=accept.offline_limit_s,
        heartbeat_interval_s=accept.heartbeat_interval_s,
        enrolled_at=plan.clock.now(),
    )


async def _close(link: Link) -> None:
    """Close the enrolment connection; a failure to close changes nothing about the outcome."""
    try:
        await link.close()
    except (TransportError, TimeoutError, OSError):
        log.debug("pollen.enrol_close_failed")
