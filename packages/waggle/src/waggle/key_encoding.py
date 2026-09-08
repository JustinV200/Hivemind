"""Encode and decode a raw Ed25519 public key as the hex text humans and Hive Manifests carry.

Inside a process a public key is the 32 raw bytes ``waggle.signing`` works with, but wherever a
human or a Hive Manifest (the TOML file that configures one Hive) sees a key it is 64 hex
characters: ``hive keys list`` prints that form, an operator pastes it into a manifest to tell a
node which peers to trust, and the manifest loader turns it back into bytes for an
``Ed25519Verifier``. WHY hex rather than base64: it reads back case-insensitively, has no padding
or punctuation to mistype, and two keys are easy to compare by eye. This is its own module rather
than part of ``waggle.signing`` because it is a presentation concern, not a signing one
(codingrules 5.2: one concept per file), and because signing.py sits at the 5.1 size limit.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen), inside the waggle package.
    Called by the manifest loader (reading keys) and the CLI's key commands (printing them) in
    later phases; calls into waggle.signing only for PUBLIC_KEY_BYTES.

Key invariants:
    - public_key_from_hex(public_key_hex(key)) == key for every PUBLIC_KEY_BYTES-byte key, and
      the parse accepts either case.
    - Both directions reject any length other than PUBLIC_KEY_BYTES with a ValueError whose
      message names the length, never the bytes or the text.

See Also:
    - waggle.signing for Ed25519Signer.public_key_bytes (the input) and Ed25519Verifier (the
      consumer of the parsed bytes).
    - docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md for the decision that public
      keys are hex-encoded wherever a human or a manifest sees them.
"""

from __future__ import annotations

from waggle.signing import PUBLIC_KEY_BYTES

__all__ = ["public_key_from_hex", "public_key_hex"]


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


def _require_public_key_length(public_key: bytes) -> None:
    """Raise ValueError naming the length (never the bytes) unless it is PUBLIC_KEY_BYTES."""
    if len(public_key) != PUBLIC_KEY_BYTES:
        raise ValueError(
            f"An Ed25519 public key must be exactly {PUBLIC_KEY_BYTES} raw bytes, "
            f"got {len(public_key)}."
        )
