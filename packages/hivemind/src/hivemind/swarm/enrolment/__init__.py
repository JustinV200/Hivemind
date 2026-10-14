"""Re-export the enrolment package: how a device becomes a Real Cell of the Swarm.

Enrolment is one invite, one signed request and one signed accept (docs/waggle/enrolment.md): the
operator mints a single-use invite on the Hive Stand (the machine the Queen, the central
orchestrator, runs on), the device's Pollen Packet (the thin gateway installed on it) presents it
with a key it generated and a description of itself, and the Hive answers with the identity and
limits the device will hold. `invite` mints the token and builds and parses the `pollen1.` string
that carries it, `capabilities` computes the capability strings a device is granted for an access
level, `accept` is the pure decision turning a request and an invite into a node and an accept,
and `door` is the effectful edge that mints, redeems atomically and records. This file is the
package's face: a caller writes `from hivemind.swarm.enrolment import EnrolmentDoor` without
knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Used by the
    gateway listener (the door) and the `hive swarm invite` command (the door and the invite
    format). Calls into hivemind.guard, hivemind.cell and the rest of hivemind.swarm.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - The invite's token is held only by the string `mint_invite` returns, shown once.

See Also:
    - docs/waggle/enrolment.md for the ceremony.
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the decision.

Public API:
    - EnrolmentDoor, MintedInvite: mint invites and admit the device that presents one (door).
    - EnrolmentRules, MintedIds, Admission, check_request, decide_enrolment, node_update_for:
      the pure decision and the terms it applies (accept).
    - InviteEndpoint, InviteContents, new_invite_token, token_hash, build_invite_string,
      parse_invite_string, INVITE_PREFIX, INVITE_CODE_PATTERN: the invite token and string
      (invite).
    - granted_capabilities, PACKET_FAMILIES: the capability strings for an access level
      (capabilities).
    - grants_read, readable_under: whether those strings let a Warden read under a directory,
      and which of a list of directories they let it read (capabilities).
"""

from hivemind.swarm.enrolment.accept import (
    Admission,
    EnrolmentRules,
    MintedIds,
    check_request,
    decide_enrolment,
    node_update_for,
)
from hivemind.swarm.enrolment.capabilities import (
    PACKET_FAMILIES,
    granted_capabilities,
    grants_read,
    readable_under,
)
from hivemind.swarm.enrolment.door import EnrolmentDoor, MintedInvite
from hivemind.swarm.enrolment.invite import (
    INVITE_CODE_PATTERN,
    INVITE_PREFIX,
    InviteContents,
    InviteEndpoint,
    build_invite_string,
    new_invite_token,
    parse_invite_string,
    token_hash,
)

__all__ = [
    "INVITE_CODE_PATTERN",
    "INVITE_PREFIX",
    "PACKET_FAMILIES",
    "Admission",
    "EnrolmentDoor",
    "EnrolmentRules",
    "InviteContents",
    "InviteEndpoint",
    "MintedIds",
    "MintedInvite",
    "build_invite_string",
    "check_request",
    "decide_enrolment",
    "granted_capabilities",
    "grants_read",
    "new_invite_token",
    "node_update_for",
    "parse_invite_string",
    "readable_under",
    "token_hash",
]
