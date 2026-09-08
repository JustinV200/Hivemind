"""Sign and verify canonical envelope bytes with Ed25519, one key per node.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) wraps every
message in an Envelope, and one that crosses a machine boundary must be attributable: the receiver
has to know which node (one running process, ``node_<ULID>``) sent it and that nothing changed in
transit, since it may be a command a Pollen Packet (the thin gateway on an enrolled device) will
execute (codingrules 15: device commands are signed). That attribution is an Ed25519 signature
over the envelope's canonical bytes. WHY Ed25519: small keys (32 bytes) and signatures (64 bytes),
verification fast enough for the constrained devices a Pollen Packet runs on, deterministic
signatures (same key and bytes, same signature, so a re-encoded frame is reproducible and a test
can pin one), and no curve, hash or padding parameters to choose, so none to get wrong.

Keys are per node, not per bee: a node is what holds a key on disk and what a receiver can be told
to trust, and every bee in that process signs with it. In phase 1 the Hive Stand's (the machine the
Queen, the central orchestrator, runs on) node key is also the Hive key that signs a ``QueenMoved``
relocation notice; a later phase may add a separate Hive keypair, and the ADR notes it. The
canonical bytes come from ``waggle.codec.canonical_bytes`` over the RAW wire dict (what was
actually sent, never a re-validated model, so defaults filled in on receipt cannot break a
signature). This module never imports codec: codec defines the structural ``Signer`` and
``Verifier`` protocols, which the two classes here satisfy by shape. A Verifier present in a Codec
means signatures are REQUIRED on every frame; none means they are ignored (the in-process case).
Wherever a human or a Hive Manifest (the TOML file that configures one Hive) sees a public key it
is 64 hex characters: ``hive keys list`` prints that form, an operator pastes it into a manifest
to tell a node which peers to trust, and the manifest loader turns it back into the 32 raw bytes
an ``Ed25519Verifier`` takes; ``public_key_hex`` and ``public_key_from_hex`` are those two
directions. WHY hex rather than base64: it reads back case-insensitively, has no padding or
punctuation to mistype, and two keys are easy to compare by eye.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Called by waggle.codec.Codec through the Signer/Verifier protocols on every encode and decode,
    and by composition roots (CLI, Entrance, Pollen's agent loop) that load a node's key from the
    secret store, and by the manifest loader and the CLI's key commands for the hex form; calls
    into the cryptography library only.

Key invariants:
    - verify() returns None only when ``signature`` was made by the key registered for ``node_id``
      over exactly ``canonical``; every other outcome raises a SignatureError subclass from
      waggle.errors, never a cryptography exception.
    - Ed25519Verifier is immutable: with_key() returns a new verifier; the original is unchanged.
    - Nothing but ``private_key_bytes`` ever returns or prints private key material; it exists only
      so a composition root can persist the key to a secret store (codingrules 13).
    - Every key and signature length is checked before cryptography sees it, so a wrong length
      fails with a full-sentence waggle error, not a library error.
    - public_key_from_hex(public_key_hex(key)) == key for every PUBLIC_KEY_BYTES-byte key, the
      parse accepts either case, and both directions reject any other length with a ValueError
      that names the length, never the bytes or the text.

See Also:
    - waggle.codec for canonical_bytes and the Signer/Verifier protocols this module satisfies.
    - waggle.errors for SignatureError, UnknownSignerError and InvalidSignatureError.
    - docs/waggle/spec.md section 6 (Signing) for the wire-level rules.
    - docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md for the per-node key decision,
      the phase 1 Hive-key note and the decision that public keys are hex wherever a human or a
      manifest sees them.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Mapping
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from waggle.errors import InvalidSignatureError, UnknownSignerError

PRIVATE_KEY_BYTES = 32  # RFC 8032 seed length, which is cryptography's Raw private format.
PUBLIC_KEY_BYTES = 32  # RFC 8032 compressed Edwards point; manifests carry it hex-encoded.
SIGNATURE_BYTES = 64  # RFC 8032 signature (R || S); 88 characters once base64-padded on the wire.
_REPR_PREFIX_BYTES = 8  # Enough public key to tell two signers apart in a log line, no more.

__all__ = [
    "PRIVATE_KEY_BYTES",
    "PUBLIC_KEY_BYTES",
    "SIGNATURE_BYTES",
    "Ed25519Signer",
    "Ed25519Verifier",
    "public_key_from_hex",
    "public_key_hex",
]


class Ed25519Signer:
    """Sign canonical envelope bytes with one node's Ed25519 private key.

    Satisfies waggle.codec's structural ``Signer`` protocol. One per node, built by a composition
    root from the key it loaded from the secret store (or ``generate()`` on a first run) and handed
    to the node's Codec; holds the key for the life of the process and never prints it.
    """

    def __init__(self, private_key_bytes: bytes) -> None:
        """Load a signer from a node's raw Ed25519 private key.

        Args:
            private_key_bytes: The raw private key (RFC 8032 seed), exactly PRIVATE_KEY_BYTES long,
                as read back from a secret store or another signer's ``private_key_bytes``.

        Raises:
            ValueError: ``private_key_bytes`` is not exactly PRIVATE_KEY_BYTES long.
        """
        _require_length("private key", private_key_bytes, PRIVATE_KEY_BYTES)
        self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)

    @classmethod
    def generate(cls) -> Ed25519Signer:
        """Create a signer with a fresh random keypair, for a node's first run or a test.

        Returns:
            A signer; persist its ``private_key_bytes`` to keep the identity across restarts.
        """
        # Per RFC 8032 section 5.1.5 a private key is exactly PRIVATE_KEY_BYTES of CSPRNG output,
        # which is all the library's own generate() draws; `secrets` keeps one construction path.
        return cls(secrets.token_bytes(PRIVATE_KEY_BYTES))

    @property
    def public_key_bytes(self) -> bytes:
        """The raw public key a verifier registers under this node's id.

        Returns:
            Exactly PUBLIC_KEY_BYTES bytes; safe to publish, log or put in a manifest.
        """
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )

    @property
    def private_key_bytes(self) -> bytes:
        """The raw private key, for a composition root to persist and for nothing else.

        Secret material (codingrules 13 and 15): it goes to a secret store only, never to a log
        line, a Pheromone Trail event (the Hive's append-only audit log), an exception message or
        the wire. Reading it anywhere but a composition root is a review rejection.

        Returns:
            Exactly PRIVATE_KEY_BYTES bytes; ``Ed25519Signer(value)`` rebuilds this signer.
        """
        # Raw (not PKCS8/PEM) so the secret store holds exactly PRIVATE_KEY_BYTES with no framing
        # to parse, and NoEncryption because encryption at rest is the secret store's job.
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def sign(self, canonical: bytes) -> str:
        """Sign canonical envelope bytes and return the signature as base64 text.

        Args:
            canonical: What ``waggle.codec.canonical_bytes`` produced for the envelope being sent;
                signing anything else means the receiver's verification will fail.

        Returns:
            Standard, padded base64 (RFC 4648 section 4) of the signature: 88 ASCII characters.
        """
        # Ed25519 hashes the message internally, so the bytes go in whole, never pre-hashed:
        # pre-hashing would change what is signed and break any other RFC 8032 verifier.
        raw_signature = self._private_key.sign(canonical)
        # Standard alphabet with padding, not urlsafe: the JSON frame quotes the value, so "+" and
        # "/" are harmless there, and every base64 decoder agrees on this form.
        return base64.b64encode(raw_signature).decode("ascii")

    def __repr__(self) -> str:
        """Identify the signer by a public-key prefix only; never any key material."""
        return f"Ed25519Signer(public_key={self.public_key_bytes[:_REPR_PREFIX_BYTES].hex()}...)"


class Ed25519Verifier:
    """Verify envelope signatures against the public keys of the nodes this receiver trusts.

    Satisfies waggle.codec's structural ``Verifier`` protocol. Immutable (codingrules 8.5): keys
    are copied into a read-only mapping, and adding a node (at enrolment, or when a peer rotates
    its key) makes a new verifier through ``with_key``, so a Codec holding the old one keeps
    exactly the trust it was given. Public keys are not secret and may be logged; the signatures
    and canonical bytes it checks never are.
    """

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        """Build a verifier trusting exactly the given node ids and their raw public keys.

        Args:
            keys: ``node_id -> raw PUBLIC_KEY_BYTES public key``, one entry per node whose frames
                this receiver accepts; copied. Empty means every signed frame is unattributable.

        Raises:
            ValueError: A value is not exactly PUBLIC_KEY_BYTES long (checked before any is kept).
        """
        # Validate every entry before keeping any, so the error names the offending node and the
        # caller never gets a verifier that trusts only some of what it asked for.
        for node_id, public_key in keys.items():
            _require_length(f"public key for node {node_id}", public_key, PUBLIC_KEY_BYTES)
        # A read-only proxy over a private copy is the immutability promise: neither the caller's
        # mapping nor anything holding this verifier can change which nodes it trusts.
        self._keys: Mapping[str, bytes] = MappingProxyType(dict(keys))

    @property
    def known_nodes(self) -> frozenset[str]:
        """The node ids this verifier holds a public key for.

        Returns:
            A frozen set of node ids; a frame from any other node raises UnknownSignerError.
        """
        return frozenset(self._keys)

    def with_key(self, node_id: str, public_key: bytes) -> Ed25519Verifier:
        """Return a new verifier that also trusts ``public_key`` for ``node_id``.

        Args:
            node_id: The node whose frames the new verifier should accept; a key already held for
                it is replaced, which is how a peer's key rotation reaches a receiver.
            public_key: That node's raw PUBLIC_KEY_BYTES public key.

        Returns:
            A new Ed25519Verifier holding every key this one holds plus the given one; the
            verifier this is called on is left unchanged.

        Raises:
            ValueError: ``public_key`` is not exactly PUBLIC_KEY_BYTES long.
        """
        # Rebuilt through __init__ rather than by copying internals, so the new key is validated
        # like every other and there is a single construction path to reason about.
        return Ed25519Verifier({**self._keys, node_id: public_key})

    def verify(self, node_id: str, canonical: bytes, signature: str) -> None:
        """Check that ``signature`` is ``node_id``'s Ed25519 signature over ``canonical``.

        Args:
            node_id: The envelope's ``node_id``: which registered key to check against.
            canonical: What ``waggle.codec.canonical_bytes`` produced from the RAW wire dict as
                received, signature field excluded.
            signature: The envelope's ``signature`` field: base64 text of the signature.

        Returns:
            None when the signature verifies; every failure is an exception, never a result a
            caller could forget to check.

        Raises:
            UnknownSignerError: No public key is registered for ``node_id``.
            InvalidSignatureError: ``signature`` is not valid base64, is not SIGNATURE_BYTES long
                once decoded, or does not verify (the frame was altered in transit or signed with
                a key other than the one registered for ``node_id``).
        """
        # A good signature from a node this receiver was never told about is unattributable, not
        # invalid; the distinct error lets a transport log an enrolment gap rather than tampering.
        raw_public_key = self._keys.get(node_id)
        if raw_public_key is None:
            raise UnknownSignerError(
                f"Node {node_id} has no public key registered with this verifier, so the "
                "signature on its frame cannot be checked."
            )
        raw_signature = _decode_signature(node_id, signature)

        # PERF: the key object is rebuilt per frame from raw bytes, one cheap library call; storing
        # only raw bytes keeps the state trivially immutable and with_key a one-liner.
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(raw_public_key)
        # cryptography's InvalidSignature carries no message of its own, so the waggle error
        # supplies the sentence and chains the cause for the Pheromone Trail (codingrules 10).
        try:
            public_key.verify(raw_signature, canonical)
        except InvalidSignature as exc:
            raise InvalidSignatureError(
                f"The signature on the frame from node {node_id} does not verify against its "
                "canonical bytes: the frame was altered in transit or signed with another key."
            ) from exc


def public_key_hex(public_key: bytes) -> str:
    """Render a raw public key as the lowercase hex a human or a Hive Manifest sees.

    Args:
        public_key: Exactly PUBLIC_KEY_BYTES raw bytes, e.g. ``Ed25519Signer.public_key_bytes``.

    Returns:
        64 lowercase hex characters; ``public_key_from_hex`` inverts it.

    Raises:
        ValueError: ``public_key`` is not exactly PUBLIC_KEY_BYTES long.
    """
    # Checked here rather than trusting the caller so a truncated key never reaches a manifest,
    # where it would only fail much later, on the node that tries to load it.
    _require_public_key_length(public_key)
    return public_key.hex()


def public_key_from_hex(text: str) -> bytes:
    """Parse the hex form from a manifest or a human back into a raw public key.

    Args:
        text: 64 hex characters in either case, as ``public_key_hex`` produces or an operator types.

    Returns:
        Exactly PUBLIC_KEY_BYTES raw bytes, ready for ``Ed25519Verifier``.

    Raises:
        ValueError: ``text`` is not hexadecimal, or decodes to other than PUBLIC_KEY_BYTES bytes.
    """
    # The offending text stays out of the message: an operator who pasted the wrong value may have
    # pasted a private key, and this error may end up in a log line.
    try:
        public_key = bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError("A hex-encoded Ed25519 public key must contain only hex digits.") from exc
    _require_public_key_length(public_key)
    return public_key


def _require_length(label: str, value: bytes, expected: int) -> None:
    """Raise ValueError with a full sentence when ``value`` is not exactly ``expected`` bytes."""
    # The length, never the bytes, goes into the message: this guards private keys too, and a
    # key's value belongs in a secret store, not in an exception that may be logged.
    if len(value) != expected:
        raise ValueError(
            f"An Ed25519 {label} must be exactly {expected} raw bytes, got {len(value)}."
        )


def _decode_signature(node_id: str, signature: str) -> bytes:
    """Decode the base64 signature text, or raise InvalidSignatureError naming ``node_id``."""
    # validate=True rejects characters outside the standard alphabet instead of silently dropping
    # them, so a corrupted field cannot decode to something that happens to verify. binascii.Error
    # (bad alphabet or padding) is a ValueError subclass, and non-ASCII text raises a plain
    # ValueError before decoding starts, so one clause covers every malformed input.
    try:
        raw_signature = base64.b64decode(signature, validate=True)
    except ValueError as exc:
        raise InvalidSignatureError(
            f"The signature on the frame from node {node_id} is not valid base64 text."
        ) from exc

    # A wrong-length signature can never verify; failing here gives a precise reason (and skips
    # the curve arithmetic) instead of a generic "does not verify" from the library.
    if len(raw_signature) != SIGNATURE_BYTES:
        raise InvalidSignatureError(
            f"The signature on the frame from node {node_id} decodes to {len(raw_signature)} "
            f"bytes, expected {SIGNATURE_BYTES}."
        )
    return raw_signature


def _require_public_key_length(public_key: bytes) -> None:
    """Raise ValueError naming the length (never the bytes) unless it is PUBLIC_KEY_BYTES."""
    if len(public_key) != PUBLIC_KEY_BYTES:
        raise ValueError(
            f"An Ed25519 public key must be exactly {PUBLIC_KEY_BYTES} raw bytes, "
            f"got {len(public_key)}."
        )
