"""Provide EnrolmentDoor: mint device invites and admit the device that presents one.

The door is the Hive Stand's side of the enrolment ceremony (docs/waggle/enrolment.md sections 3
and 6). `mint_invite` is the operator's act (`hive swarm invite`): it makes a single-use token,
stores only its SHA-256 with the access cap and Comb Shield tier the operator chose, records
`swarm.invited` in the same transaction, and returns the invite string once. `enrol` is the
device's: given the verified `swarm.enrol_request` frame it checks the request's addressing and
identity, hashes the presented token, decides on the terms (`accept.decide_enrolment`), and
redeems the invite atomically in the registry (the node, the spent invite and `swarm.enrolled`
commit together, so two devices presenting one token can never both win). A refusal is two things
at once: a `swarm.enrol_refused` event with ids and a reason code, never the token, and an
`EnrolmentDeniedError` that the listener turns into the `control.error` `hive.swarm.enrol_denied`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.enrolment`.
    Called by the gateway listener (`enrol`) and by the `hive swarm invite` command
    (`mint_invite`). Calls into the swarm registry, the recorder, the pure decision and the
    invite format of this package, and waggle for ids.

Key invariants:
    - The token is hashed on arrival and never stored, logged, put on the trail or placed in an
      error: `swarm.enrol_refused` carries ids and a reason code only.
    - Night Veil cannot be minted: it is a Virtual-only tier.
    - A successful `enrol` has created the node, spent the invite and recorded `swarm.enrolled`;
      a refused one has changed nothing but, for a lapsed invite, the invite's own move to EXPIRED.

See Also:
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the trust model.
    - hivemind.swarm.enrolment.accept for the pure decision this effectful edge wraps.
    - hivemind.swarm.registry for the atomic redemption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.pheromone import SwarmEvent
from hivemind.swarm.enrolment.accept import (
    EnrolmentRules,
    MintedIds,
    check_request,
    decide_enrolment,
)
from hivemind.swarm.enrolment.invite import build_invite_string, new_invite_token, token_hash
from hivemind.swarm.errors import EnrolmentDeniedError, EnrolRefusal, InviteRefusedError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import SwarmInvite, SwarmNode
from hivemind.swarm.registry import SwarmRegistry
from waggle.envelope import Envelope
from waggle.ids import new_cell_id, new_warden_id
from waggle.messages.swarm import EnrolAccept, EnrolRequest

__all__ = ["EnrolmentDoor", "MintedInvite"]


def _refuse_night_veil(shield: CombShieldLevel) -> None:
    """Raise `InviteRefusedError` for the tier that exists only for Virtual Cells."""
    if shield is CombShieldLevel.NIGHT_VEIL:
        raise InviteRefusedError(
            "A Swarm invite cannot be NIGHT_VEIL: that tier exists only for Virtual Cells, "
            "which are provisioned and destroyed; a borrowed device is never torn down."
        )


@dataclass(frozen=True, slots=True)
class MintedInvite:
    """A freshly minted invite, shown once at the Hive Stand and never stored.

    Attributes:
        invite_string: The `pollen1.` string for `pollen enrol`. A secret until used or expired;
            hidden from `repr`, and held nowhere else.
        code_hash: The SHA-256 of its token, the key the registry stores the invite under (and
            what the registry's `revoke_invite` names it by; no CLI command exposes that).
        access_level: The most the device may be granted.
        comb_shield: The tier the operator vouched for.
        expires_at: When the invite stops being accepted.
        label: The operator's label for the device, or None.
    """

    invite_string: str
    code_hash: str
    access_level: AccessLevel
    comb_shield: CombShieldLevel
    expires_at: datetime
    label: str | None

    def __repr__(self) -> str:
        """Show who the invite is for and when it lapses, never the string that is the secret."""
        return (
            f"MintedInvite(code_hash={self.code_hash[:12]}..., label={self.label!r}, "
            f"expires_at={self.expires_at.isoformat()})"
        )


class EnrolmentDoor:
    """The Hive Stand's enrolment door: mints invites, redeems them, records both."""

    def __init__(
        self, registry: SwarmRegistry, recorder: SwarmRecorder, rules: EnrolmentRules
    ) -> None:
        """Build the door over the registry, the trail writer and the Hive's terms.

        Args:
            registry: Where invites and nodes live; redemption is atomic there.
            recorder: The trail, clock and identity events are recorded with.
            rules: The Hive's address half and the timings every invite and accept carries.
        """
        self._registry = registry
        self._recorder = recorder
        self._rules = rules

    async def mint_invite(
        self, access: AccessLevel, shield: CombShieldLevel, label: str | None = None
    ) -> MintedInvite:
        """Mint one single-use invite and record `swarm.invited`.

        Args:
            access: The most the device may be granted; it receives the lower of this and what
                it asks for.
            shield: The Comb Shield tier the operator vouches for: MEADOW, or PROPOLIS for a
                device that already runs a VPN.
            label: The operator's name for the device, shown in `hive swarm list`.

        Returns:
            The minted invite; its string is shown once and nowhere kept.

        Raises:
            InviteRefusedError: `shield` is NIGHT_VEIL, which exists only for Virtual Cells.
            pydantic.ValidationError: `label` is longer than a label may be.
        """
        _refuse_night_veil(shield)
        now = self._recorder.clock.now()
        token = new_invite_token()
        invite = SwarmInvite(
            code_hash=token_hash(token),
            label=label,
            access_level=access,
            comb_shield=shield,
            created_at=now,
            expires_at=now + self._rules.invite_ttl,
        )
        # Written with its event in one transaction (codingrules Appendix C), after the string is
        # built so a malformed label fails before anything is stored.
        text = build_invite_string(token, self._rules.endpoint, invite.expires_at, label)
        await self._registry.put_invite(invite, self._invited_event(invite))
        return MintedInvite(
            invite_string=text,
            code_hash=invite.code_hash,
            access_level=access,
            comb_shield=shield,
            expires_at=invite.expires_at,
            label=label,
        )

    def _invited_event(self, invite: SwarmInvite) -> SwarmEvent:
        """Build `swarm.invited`: the label, the cap, the tier and the expiry, and nothing else."""
        # The token and the string are not on the trail (enrolment.md section 3.2).
        return self._recorder.swarm_event(
            "swarm.invited",
            self._rules.endpoint.hive_id,
            {
                "label": invite.label,
                "access_level": invite.access_level.value,
                "comb_shield": invite.comb_shield.value,
                "expires_at": invite.expires_at.isoformat(),
            },
        )

    async def enrol(self, envelope: Envelope) -> EnrolAccept:
        """Admit the device whose verified `swarm.enrol_request` this is, or refuse it.

        Args:
            envelope: The request's envelope, already verified against the key in its payload
                (proof of possession); its payload is a `swarm.enrol_request`.

        Returns:
            The `swarm.enrol_accept` payload the caller sends back, correlated to the request.
            The node exists, ONLINE on the GATEWAY rung, and its invite is spent.

        Raises:
            EnrolmentDeniedError: The request is misaddressed or inconsistent, or its token is
                unknown, lapsed, revoked or used, or its identity is already enrolled. Nothing
                was created, and `swarm.enrol_refused` is on the trail.
        """
        request = envelope.payload
        if not isinstance(request, EnrolRequest):
            await self._refuse(envelope.sender, envelope.node_id, EnrolRefusal.NOT_AN_ENROLMENT)
        refusal = check_request(envelope, request, self._rules)
        if refusal is not None:
            await self._refuse(request.device_id, request.node_id, refusal)
        code_hash = token_hash(request.invite_token)
        now = self._recorder.clock.now()
        invite = await self._registry.get_invite(code_hash)
        if invite is None:
            await self._refuse(request.device_id, request.node_id, EnrolRefusal.UNKNOWN_INVITE)
        decision = decide_enrolment(request, invite, self._rules, self._mint_ids(), now)
        if isinstance(decision, EnrolRefusal):
            # The pure decision found the invite lapsed by the clock without ever reaching the
            # registry's redemption, so the invite's own move to EXPIRED is made here.
            if decision is EnrolRefusal.EXPIRED:
                await self._registry.expire_invites(now)
            await self._refuse(request.device_id, request.node_id, decision)
        await self._redeem(code_hash, decision.node, now)
        return decision.accept

    async def _redeem(self, code_hash: str, node: SwarmNode, at: datetime) -> None:
        """Spend the invite and create `node` with its `swarm.enrolled` event, in one unit."""
        event = self._recorder.swarm_event(
            "swarm.enrolled",
            node.device_id,
            {
                "device_id": node.device_id,
                "node_id": node.node_id,
                "cell_id": node.cell_id,
                "warden_id": node.warden_id,
                "access_level": node.access_level.value,
                "comb_shield": node.comb_shield.value,
                "label": node.label,
            },
        )
        # The registry spends the invite, creates the node and records the event as one unit; a
        # race with another device holding the same token is lost there, not here.
        try:
            await self._registry.redeem(code_hash, node, at, event)
        except EnrolmentDeniedError as exc:
            await self._refuse(node.device_id, node.node_id, exc.reason)

    def _mint_ids(self) -> MintedIds:
        """Mint the Real Cell and Warden ids a new device will keep for life."""
        clock = self._recorder.clock
        return MintedIds(cell_id=new_cell_id(clock), warden_id=new_warden_id(clock))

    async def _refuse(self, device_id: str, node_id: str, reason: EnrolRefusal) -> NoReturn:
        """Record `swarm.enrol_refused` (ids and a reason code only) and raise the denial."""
        event = self._recorder.swarm_event(
            "swarm.enrol_refused",
            device_id,
            {"device_id": device_id, "node_id": node_id, "reason": reason.value},
        )
        await self._recorder.record(event)
        raise EnrolmentDeniedError(reason, device_id, node_id)
