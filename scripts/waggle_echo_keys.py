"""Write and read the per-node key files the waggle_echo demo's two processes identify with.

The demo (scripts/waggle_echo.py) runs a Waggle server and client in separate processes, and
every frame between them is signed (Waggle is the Hive's bee-to-bee wire protocol, and a frame
that crosses a process boundary is signed with the sending node's Ed25519 key). The two
processes therefore need each other's public keys and their own private keys before they can
speak, and a private key must never travel on a command line, where every process on the
machine can read it. This module fixes the layout of one keys directory: a public half
(``identities.json``, holding each role's node id, bee address and hex public key) and one raw
private key file per role, created with owner-only permissions where the OS honours them. The
orchestrator writes the directory once; each role process loads its own private key plus the
peer's public key from it and builds the signed Codec it speaks with.

Fits into the Hive:
    Layer: none (a demo helper, not shipped code). Called by scripts/waggle_echo.py to write
    the directory and by scripts/waggle_echo_server.py and scripts/waggle_echo_client.py to
    load it; calls into waggle.signing, waggle.signing, waggle.codec and waggle.ids.

Key invariants:
    - A private key is written to and read from its own file only; it never appears in the
      JSON, on a command line, in an event line or in an error message.
    - Each role's codec trusts exactly the other role's node id, so a frame from any other
      signer is refused as unknown and a tampered frame as invalid (waggle.codec).

See Also:
    - scripts/waggle_echo.py for the orchestrator that writes the directory.
    - waggle.signing for Ed25519Signer and Ed25519Verifier, whose key forms are stored here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from waggle import (
    Clock,
    Codec,
    Ed25519Signer,
    Ed25519Verifier,
    NodeId,
    new_hive_id,
    new_node_id,
    new_warden_id,
    public_key_from_hex,
    public_key_hex,
)

SERVER_ROLE = "server"  # The listener: plays the Queen on the Hive Stand, with a hive_ address.
CLIENT_ROLE = "client"  # The dialler: plays a Warden reaching the Hive Stand (warden_ address).
IDENTITIES_FILE = "identities.json"  # The public halves of both roles; safe to print or share.
PRIVATE_KEY_SUFFIX = ".key"  # <role>.key: the raw 32-byte Ed25519 private key, and nothing else.
_OWNER_ONLY = 0o600  # The mode a private key file is created with; ignored on Windows (NTFS ACLs).

__all__ = [
    "CLIENT_ROLE",
    "IDENTITIES_FILE",
    "PRIVATE_KEY_SUFFIX",
    "SERVER_ROLE",
    "Identity",
    "Keyring",
    "load_keyring",
    "write_keys",
]


@dataclass(frozen=True, slots=True)
class Identity:
    """The public half of one role: who it is on the wire and how its frames are verified."""

    role: str  # SERVER_ROLE or CLIENT_ROLE.
    node_id: NodeId  # The process identity the signature is attributed to.
    address: str  # The bee address envelopes from this role carry as sender.
    public_key: bytes  # The raw Ed25519 public key the peer verifies with.


@dataclass(frozen=True, slots=True)
class Keyring:
    """What one role process needs to speak: its own identity, the peer's, and the signed codec."""

    own: Identity
    peer: Identity
    codec: Codec  # Signs with own's private key; requires and checks peer's signature.


def write_keys(keys_dir: Path, clock: Clock) -> None:
    """Generate both roles' keys and identities and write them into ``keys_dir``.

    Args:
        keys_dir: An existing, empty directory the orchestrator owns for the demo's lifetime.
        clock: Mints the node ids and bee addresses (ids are timestamped).

    Raises:
        OSError: A file cannot be created, or a private key file already exists (O_EXCL: a
            key is never silently overwritten).
    """
    public: dict[str, dict[str, str]] = {}
    # One signer per role, each with a fresh keypair; the server is the Queen (hive_ address)
    # and the client a Warden (warden_ address), the two bee addresses a Queen-to-Warden link
    # carries. The demo's assign travels Warden-to-Queen, the reverse of the Hive's placement
    # flow, because it exercises the wire (a signed request across a process boundary) and not
    # the placement logic; the envelope validates address shape, not direction.
    for role, mint_address in ((SERVER_ROLE, new_hive_id), (CLIENT_ROLE, new_warden_id)):
        signer = Ed25519Signer.generate()
        public[role] = {
            "node_id": new_node_id(clock),
            "address": mint_address(clock),
            "public_key": public_key_hex(signer.public_key_bytes),
        }
        # The private key goes through a low-level open so the file is born owner-only and never
        # exists for an instant with default permissions; O_EXCL refuses to clobber a key.
        fd = os.open(
            keys_dir / f"{role}{PRIVATE_KEY_SUFFIX}",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            _OWNER_ONLY,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(signer.private_key_bytes)
    (keys_dir / IDENTITIES_FILE).write_text(json.dumps(public, indent=2), encoding="utf-8")


def load_keyring(keys_dir: Path, role: str) -> Keyring:
    """Load ``role``'s private key and both identities from ``keys_dir`` and build its codec.

    Args:
        keys_dir: The directory ``write_keys`` produced.
        role: SERVER_ROLE or CLIENT_ROLE: whose private key to load; the other is the peer.

    Returns:
        A Keyring whose codec signs as ``role`` and verifies exactly the peer's node.

    Raises:
        ValueError: ``role`` is not one of the two roles.
        OSError: A file is missing or unreadable.
        KeyError: The identities file lacks a role or a field (a hand-edited directory).
    """
    if role not in (SERVER_ROLE, CLIENT_ROLE):
        raise ValueError(f"Unknown waggle_echo role {role!r}; expected server or client.")
    peer_role = CLIENT_ROLE if role == SERVER_ROLE else SERVER_ROLE
    identities = json.loads((keys_dir / IDENTITIES_FILE).read_text(encoding="utf-8"))
    own = _identity_from(role, identities[role])
    peer = _identity_from(peer_role, identities[peer_role])
    # The private key is read straight into the signer and never kept as a separate value, so
    # nothing but the signer object holds it and nothing can accidentally log it.
    signer = Ed25519Signer((keys_dir / f"{role}{PRIVATE_KEY_SUFFIX}").read_bytes())
    # Trusting only the peer is what makes the tamper step meaningful: a frame the peer signed
    # verifies, a frame anyone altered afterwards does not, and no third key is ever accepted.
    verifier = Ed25519Verifier({peer.node_id: peer.public_key})
    return Keyring(own=own, peer=peer, codec=Codec(signer=signer, verifier=verifier))


def _identity_from(role: str, fields: dict[str, str]) -> Identity:
    """Build one Identity from its JSON object, decoding the hex public key."""
    return Identity(
        role=role,
        node_id=NodeId(fields["node_id"]),
        address=fields["address"],
        public_key=public_key_from_hex(fields["public_key"]),
    )
