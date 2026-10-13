"""Enrol a device in the Swarm: read the invite, present the token, keep the accept.

A new device becomes a Real Cell of the Swarm through one ceremony (enrolment.md): the operator
mints an invite on the Hive Stand, the Pollen Packet (the thin gateway program on a device the Hive
borrows) reads it, generates its identity and key, dials the Queen (the Hive's central
orchestrator) over a connection whose certificate it checks against the invite's fingerprint, sends
one signed ``swarm.enrol_request`` and, if the accept matches what the invite pinned, persists its
state. ``invite`` reads the ``pollen1.`` string, ``request`` builds the request from what the
device finds out about itself, and ``enrol`` runs the whole exchange.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli
    (``pollen enrol``); calls into pollen.agent.link for the pinned connection, pollen.state for
    the key and the state, pollen.executors.info for the probes, and waggle.

Key invariants:
    - The key is on disk before the first dial, and the token is sent once, on a pinned connection.
    - Nothing is persisted unless the accept matches the invite; a device never holds half an
      enrolment.
    - The invite string and the token are never logged, stored or put in an error message.

See Also:
    - docs/waggle/enrolment.md for the ceremony end to end.
    - .claude/codingrules.md section 3 for where this sub-package sits under pollen.

Public API:
    - Invite, parse_invite: the invite string, validated.
    - DeviceIdentity, build_request: the enrolment request.
    - EnrolPlan, enrol_device: run the ceremony and persist its result.
"""

from pollen.enrol.enrol import EnrolPlan, enrol_device
from pollen.enrol.invite import Invite, parse_invite
from pollen.enrol.request import DeviceIdentity, build_request

__all__ = [
    "DeviceIdentity",
    "EnrolPlan",
    "Invite",
    "build_request",
    "enrol_device",
    "parse_invite",
]
