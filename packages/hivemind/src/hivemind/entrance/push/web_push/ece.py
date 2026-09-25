"""Encrypt a Web Push message with RFC 8291 aes128gcm, and decrypt one as a user agent would.

A push service relays a Web Push message it must not read, so the payload is encrypted to the user
agent's own key (RFC 8291, over RFC 8188's ``aes128gcm`` content coding). The sender makes an
ephemeral P-256 key per message and agrees a secret with the user agent's key (ECDH); HKDF-SHA-256
keyed with the user agent's 16-byte auth secret turns it into the input keying material, and a
second HKDF under a random 16-byte salt gives the AES-128-GCM key and nonce. The body is one
RFC 8188 record behind a header (salt, record size 4096, the ephemeral public key as key id).
``padded_length`` fixes the record's plaintext length (content, the 0x02 last-record delimiter,
then zeros), which is how ``hivemind.entrance.push.web_push.channel`` makes every notice's
ciphertext the same length. ``SenderMaterial`` is the seam that makes the output reproducible: a
fresh ephemeral key and salt by default, fixed values in the RFC 8291 Appendix A test. ``decrypt``
is the user agent's half, for tests, demos and a client written against the Landing Board.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.web_push``. Called
    by ``WebPush`` for every delivery; ``decrypt`` by tests. Calls into ``cryptography`` only.

Key invariants:
    - A body is ``HEADER_BYTES`` of header plus exactly ``padded_length + TAG_BYTES`` bytes of
      ciphertext, whatever the content's length.
    - Every call without explicit material uses a fresh ephemeral key and a fresh salt, so no two
      messages share a key or a nonce.
    - ``decrypt(encrypt(m, ...)) == m``; a body altered anywhere fails to decrypt.

See Also:
    - RFC 8291 section 3.4 (key derivation) and Appendix A (the test vector), RFC 8188 section 2.
    - hivemind.entrance.push.web_push.channel for the one caller.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096  # RFC 8291 section 4: a push service need not accept more than this.
SALT_BYTES = 16  # RFC 8188 section 2.1: the salt is 16 octets.
AUTH_SECRET_BYTES = 16  # RFC 8291 section 3.2: the user agent's auth secret.
P256_POINT_BYTES = 65  # An uncompressed P-256 point: the key id and the user agent's key.
KEY_BYTES = 16  # AES-128-GCM's content-encryption key.
NONCE_BYTES = 12  # AES-GCM's nonce.
TAG_BYTES = 16  # AES-GCM's authentication tag, appended to the record.
IKM_BYTES = 32  # RFC 8291 section 3.4: the input keying material is one SHA-256 block.
LAST_RECORD_DELIMITER = 0x02  # RFC 8188 section 2: the padding delimiter of the final record.
# salt (16) || record size (uint32) || key id length (uint8): RFC 8188 section 2.1's header.
_HEADER_PREFIX = struct.Struct(f"!{SALT_BYTES}sIB")
HEADER_BYTES = _HEADER_PREFIX.size + P256_POINT_BYTES  # 86 octets with a P-256 key id.
_KEY_INFO_PREFIX = b"WebPush: info\x00"  # RFC 8291 section 3.4's key_info label.
_CEK_INFO = b"Content-Encoding: aes128gcm\x00"  # RFC 8188 section 2.2's key label.
_NONCE_INFO = b"Content-Encoding: nonce\x00"  # RFC 8188 section 2.3's nonce label.

__all__ = [
    "HEADER_BYTES",
    "RECORD_SIZE",
    "TAG_BYTES",
    "ReceiverKeys",
    "SenderMaterial",
    "decrypt",
    "encrypt",
]


@dataclass(frozen=True, slots=True)
class ReceiverKeys:
    """The user agent's half of a subscription: its public key and its auth secret.

    Attributes:
        public_key: The 65-byte uncompressed P-256 point (``p256dh``).
        auth_secret: The 16-byte auth secret (``auth``); a secret, never logged.
    """

    public_key: bytes
    auth_secret: bytes


@dataclass(frozen=True, slots=True)
class SenderMaterial:
    """The per-message randomness: the ephemeral key and the salt. The seam tests pin.

    Attributes:
        private_key: The ephemeral P-256 key agreed with the user agent's key; used once.
        salt: The 16-byte salt for the content key and nonce.
    """

    private_key: ec.EllipticCurvePrivateKey
    salt: bytes

    @classmethod
    def fresh(cls) -> SenderMaterial:
        """Draw a new ephemeral key and salt for one message.

        Returns:
            Material never used for any other message.
        """
        salt = secrets.token_bytes(SALT_BYTES)
        return cls(private_key=ec.generate_private_key(ec.SECP256R1()), salt=salt)


def encrypt(
    plaintext: bytes,
    receiver: ReceiverKeys,
    padded_length: int,
    material: SenderMaterial | None = None,
) -> bytes:
    """Encrypt ``plaintext`` for ``receiver`` as one aes128gcm record padded to ``padded_length``.

    Args:
        plaintext: The content.
        receiver: The user agent's public key and auth secret.
        padded_length: The record's plaintext length after padding: content, the delimiter and
            zeros. At least ``len(plaintext) + 1``; at most ``RECORD_SIZE - TAG_BYTES``.
        material: The ephemeral key and salt; fresh random ones when None (always, outside tests).

    Returns:
        The message body: the RFC 8188 header, then the one encrypted record.

    Raises:
        ValueError: ``padded_length`` cannot hold the content and its delimiter, or exceeds the
            record size; or the receiver's key or auth secret is malformed.
    """
    if not len(plaintext) < padded_length <= RECORD_SIZE - TAG_BYTES:
        raise ValueError(f"A padded record must hold the content plus one byte, in {RECORD_SIZE}.")
    sender = material if material is not None else SenderMaterial.fresh()
    sender_public = _point(sender.private_key.public_key())
    key, nonce = _derive_key_and_nonce(sender.private_key, receiver, sender_public, sender.salt)
    # RFC 8188 padding: the delimiter marks the end of the content, zeros fill the rest.
    record = plaintext + bytes([LAST_RECORD_DELIMITER]) + bytes(padded_length - len(plaintext) - 1)
    header = _HEADER_PREFIX.pack(sender.salt, RECORD_SIZE, P256_POINT_BYTES) + sender_public
    # A single record, so its sequence number is 0 and the nonce is used as derived.
    return header + AESGCM(key).encrypt(nonce, record, None)


def decrypt(body: bytes, private_key: ec.EllipticCurvePrivateKey, auth_secret: bytes) -> bytes:
    """Decrypt a one-record aes128gcm body as the user agent holding ``private_key`` would.

    Args:
        body: The message body, as the push service delivered it.
        private_key: The user agent's P-256 private key.
        auth_secret: The user agent's 16-byte auth secret.

    Returns:
        The content, with the padding removed.

    Raises:
        ValueError: The header is malformed, the body does not decrypt with these keys, or the
            padding is not a final record's.
    """
    if len(body) < HEADER_BYTES + TAG_BYTES:
        raise ValueError("The body is too short to hold an aes128gcm header and one record.")
    salt, record_size, key_id_length = _HEADER_PREFIX.unpack_from(body)
    if key_id_length != P256_POINT_BYTES:
        raise ValueError("The key id is not an uncompressed P-256 point.")
    # A Web Push message is exactly one record (RFC 8291 section 4); more would need sequencing.
    if len(body) - HEADER_BYTES > record_size:
        raise ValueError("The body holds more than one record.")
    sender_public = body[_HEADER_PREFIX.size : HEADER_BYTES]
    sender_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
    receiver = ReceiverKeys(public_key=_point(private_key.public_key()), auth_secret=auth_secret)
    # The user agent's side of the same agreement: its private key against the sender's public.
    secret = private_key.exchange(ec.ECDH(), sender_key)
    key, nonce = _derive(secret, receiver, sender_public, salt)
    try:
        record = AESGCM(key).decrypt(nonce, body[HEADER_BYTES:], None)
    except InvalidTag as exc:
        raise ValueError("The body does not decrypt with these keys.") from exc
    content = record.rstrip(b"\x00")
    if not content or content[-1] != LAST_RECORD_DELIMITER:
        raise ValueError("The record's padding does not end a final record.")
    return content[:-1]


def _derive_key_and_nonce(
    private_key: ec.EllipticCurvePrivateKey,
    receiver: ReceiverKeys,
    sender_public: bytes,
    salt: bytes,
) -> tuple[bytes, bytes]:
    """Agree the ECDH secret with the receiver's key, then derive the content key and nonce."""
    if len(receiver.auth_secret) != AUTH_SECRET_BYTES:
        raise ValueError(f"The auth secret is {AUTH_SECRET_BYTES} bytes.")
    receiver_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver.public_key)
    secret = private_key.exchange(ec.ECDH(), receiver_key)
    return _derive(secret, receiver, sender_public, salt)


def _derive(
    secret: bytes, receiver: ReceiverKeys, sender_public: bytes, salt: bytes
) -> tuple[bytes, bytes]:
    """Run RFC 8291 section 3.4's two HKDF steps: the IKM, then the content key and nonce."""
    # key_info binds both public keys, user agent's first, so neither can be swapped.
    key_info = _KEY_INFO_PREFIX + receiver.public_key + sender_public
    ikm = _hkdf(salt=receiver.auth_secret, info=key_info, length=IKM_BYTES).derive(secret)
    key = _hkdf(salt=salt, info=_CEK_INFO, length=KEY_BYTES).derive(ikm)
    nonce = _hkdf(salt=salt, info=_NONCE_INFO, length=NONCE_BYTES).derive(ikm)
    return key, nonce


def _hkdf(salt: bytes, info: bytes, length: int) -> HKDF:
    """Build one HKDF-SHA-256 derivation (a fresh object each time: HKDF derives once)."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info)


def _point(public_key: ec.EllipticCurvePublicKey) -> bytes:
    """Encode a P-256 public key as the 65-byte uncompressed point RFC 8291 uses."""
    return public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
