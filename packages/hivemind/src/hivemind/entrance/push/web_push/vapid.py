"""Hold the Hive's VAPID key and sign the RFC 8292 header every Web Push request carries.

A push service only relays a message to a subscription when the request proves it comes from the
application server the subscription was made for (RFC 8292, VAPID): the browser subscribed with
the Hive's VAPID public key (the ``applicationServerKey`` the subscribe route hands out), and
every push carries ``Authorization: vapid t=<JWT>, k=<that public key>``, where the JWT is signed
ES256 by the matching private key and claims the push service's origin (``aud``), an expiry at
most 24 hours out (``exp``) and a contact for the push service's operator (``sub``). The private
key comes from ``HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY`` when the operator set one (read by
``hivemind.manifest.env``, handed in as a ``SecretStr``), otherwise from the secret store under
``entrance.vapid``, minted there on first use; either way it is the raw 32-byte P-256 scalar, the
form ``web-push generate-vapid-keys`` prints (base64url) and the store keeps (raw). The contact
comes from ``HIVEMIND_ENTRANCE_VAPID_SUBJECT``, a ``mailto:`` or ``https:`` URI.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.web_push``.
    ``load_or_mint_vapid_key`` is called by the Entrance's composition root; ``VapidSigner`` by
    ``WebPush`` for every delivery, and by the subscribe route for the public key. Calls into
    ``cryptography``, the ``SecretStore`` it is given, and ``hivemind.entrance.auth`` (base64url).

Key invariants:
    - The private scalar leaves ``VapidKey`` only through ``private_scalar``, to the secret store;
      never a log line, an error message or ``repr``.
    - Every JWT expires ``VAPID_JWT_TTL_S`` after it is signed, well inside RFC 8292's 24 hours.
    - A stored or configured key that is not a valid P-256 scalar is refused loudly, never
      replaced: replacing it would silently break every existing Web Push subscription.

See Also:
    - RFC 8292 sections 2 and 3 for the JWT and the header.
    - docs/adr/0034-landing-board-versioning-and-push.md for where the key comes from.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from pydantic import SecretStr

from hivemind.common.secrets import SecretStore
from hivemind.entrance.auth import b64url_decode, b64url_encode
from hivemind.entrance.push.errors import PushConfigError
from waggle.clock import Clock

VAPID_KEY_NAME = "entrance.vapid"  # The secret store name of the minted VAPID private key.
VAPID_KEY_VARIABLE = "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY"  # Named in errors; read by manifest.env.
VAPID_SUBJECT_VARIABLE = "HIVEMIND_ENTRANCE_VAPID_SUBJECT"  # Named in errors; read by manifest.env.
VAPID_JWT_TTL_S = 12 * 60 * 60  # Half RFC 8292's 24-hour cap: slack for a push service's skew.
P256_SCALAR_BYTES = 32  # A P-256 private key is one 256-bit scalar.
MAX_SUBJECT_CHARS = 256  # A contact URI; push services reject oversized JWTs.
_SUBJECT_SCHEMES = ("mailto:", "https://")  # RFC 8292 section 2.1: an email or a web contact.
_JWT_HEADER = {"alg": "ES256", "typ": "JWT"}  # RFC 8292 section 2: ES256, the only algorithm.
_JSON_SEPARATORS = (",", ":")  # Compact JSON keeps the header short.
_SCALAR_RULE = "base64url of a raw 32-byte P-256 private scalar"  # For error messages.

__all__ = [
    "VAPID_JWT_TTL_S",
    "VAPID_KEY_NAME",
    "VapidKey",
    "VapidSigner",
    "load_or_mint_vapid_key",
    "vapid_audience",
]


class VapidKey:
    """The Hive's VAPID key pair (P-256): its public half is every subscription's server key."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        """Wrap a P-256 private key; prefer ``from_scalar`` or ``generate``.

        Args:
            private_key: A key on SECP256R1.
        """
        self._private_key = private_key

    @classmethod
    def from_scalar(cls, scalar: bytes) -> VapidKey:
        """Load the key from its raw 32-byte private scalar.

        Args:
            scalar: The big-endian scalar, as the secret store keeps it.

        Returns:
            The key.

        Raises:
            ValueError: ``scalar`` is not 32 bytes, or is zero or not below the curve's order.
        """
        if len(scalar) != P256_SCALAR_BYTES:
            raise ValueError(f"A P-256 private scalar is {P256_SCALAR_BYTES} bytes.")
        value = int.from_bytes(scalar, "big")
        return cls(ec.derive_private_key(value, ec.SECP256R1()))

    @classmethod
    def generate(cls) -> VapidKey:
        """Mint a fresh key pair from the CSPRNG.

        Returns:
            The key; persist its ``private_scalar`` to keep every subscription working.
        """
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @property
    def public_key_bytes(self) -> bytes:
        """The public key as a 65-byte uncompressed point (RFC 8292 section 3.2's ``k``)."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

    @property
    def application_server_key(self) -> str:
        """The public key, base64url: what a browser's ``subscribe`` takes, and the header's k."""
        return b64url_encode(self.public_key_bytes)

    @property
    def private_scalar(self) -> bytes:
        """The raw 32-byte private scalar, for the secret store and nothing else."""
        return self._private_key.private_numbers().private_value.to_bytes(P256_SCALAR_BYTES, "big")

    def sign(self, message: bytes) -> bytes:
        """Sign ``message`` with ES256, returning JOSE's raw ``r || s`` (64 bytes).

        Args:
            message: The JWT signing input, ``header.claims``.

        Returns:
            The signature, fixed width.
        """
        der = self._private_key.sign(message, ec.ECDSA(hashes.SHA256()))
        # cryptography answers in DER; JOSE (RFC 7518 section 3.4) wants r and s side by side.
        r, s = decode_dss_signature(der)
        return r.to_bytes(P256_SCALAR_BYTES, "big") + s.to_bytes(P256_SCALAR_BYTES, "big")

    def __repr__(self) -> str:
        """Identify the key by its public half only."""
        return f"VapidKey(public_key={self.application_server_key[:16]}...)"


class VapidSigner:
    """Sign the ``Authorization`` header for a push to one endpoint: key, contact and clock."""

    def __init__(self, key: VapidKey, subject: str, clock: Clock) -> None:
        """Build the signer.

        Args:
            key: The Hive's VAPID key.
            subject: The contact a push service may use: ``mailto:`` or ``https:``, e.g. the
                value of ``HIVEMIND_ENTRANCE_VAPID_SUBJECT``, or ``[entrance] public_url``.
            clock: Stamps each JWT's expiry.

        Raises:
            PushConfigError: ``subject`` is not a ``mailto:`` or ``https:`` URI on one line.
        """
        if (
            not subject.startswith(_SUBJECT_SCHEMES)
            or len(subject) > MAX_SUBJECT_CHARS
            or any(character.isspace() for character in subject)
        ):
            raise PushConfigError(VAPID_SUBJECT_VARIABLE, "a mailto: or https: URI")
        self._key = key
        self._subject = subject
        self._clock = clock

    @property
    def application_server_key(self) -> str:
        """The VAPID public key, base64url, for the subscribe route to hand to a browser."""
        return self._key.application_server_key

    def authorization(self, endpoint: str) -> str:
        """Return the ``Authorization`` header value for a push to ``endpoint``.

        Args:
            endpoint: The subscription's push service endpoint; its origin is the JWT's ``aud``.

        Returns:
            ``vapid t=<JWT>, k=<public key>``, the JWT expiring ``VAPID_JWT_TTL_S`` from now.
        """
        expires = int(self._clock.now().timestamp()) + VAPID_JWT_TTL_S
        claims = {"aud": vapid_audience(endpoint), "exp": expires, "sub": self._subject}
        signing_input = f"{_segment(_JWT_HEADER)}.{_segment(claims)}"
        signature = b64url_encode(self._key.sign(signing_input.encode("ascii")))
        return f"vapid t={signing_input}.{signature}, k={self._key.application_server_key}"


def vapid_audience(endpoint: str) -> str:
    """Return a push endpoint's origin, the JWT audience RFC 8292 section 2 requires.

    Args:
        endpoint: The push service endpoint URL.

    Returns:
        ``scheme://host`` with the port only when it is not the scheme's default.
    """
    url = httpx.URL(endpoint)
    return f"{url.scheme}://{url.netloc.decode('ascii')}"


async def load_or_mint_vapid_key(
    store: SecretStore, configured: SecretStr | None = None
) -> VapidKey:
    """Return the VAPID key: the configured one, else the stored one, else a new stored one.

    Args:
        store: The Hive's secret store, where a minted key is kept under ``VAPID_KEY_NAME``.
        configured: ``HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY`` as ``hivemind.manifest.env`` read
            it (base64url of the raw scalar), or None when the operator did not set it.

    Returns:
        The key; the same one on every call once it exists.

    Raises:
        PushConfigError: The configured or stored value is not a valid P-256 scalar.
    """
    # An operator who pins the key (to keep subscriptions across a reinstall) sets it here.
    if configured is not None:
        return _key_from(configured.get_secret_value().rstrip("="), VAPID_KEY_VARIABLE)
    # Latency: one small file read (a dict lookup in tests).
    stored = await store.get(VAPID_KEY_NAME)
    if stored is not None:
        return _key_from(stored, VAPID_KEY_NAME)
    # First use: mint and persist before handing it out, so no subscription is ever made
    # against a key the next process could not read back.
    key = VapidKey.generate()
    await store.put(VAPID_KEY_NAME, key.private_scalar)
    return key


def _key_from(value: str | bytes, source: str) -> VapidKey:
    """Load a key from base64url text or raw bytes, naming ``source`` (never the value) if bad."""
    try:
        scalar = b64url_decode(value) if isinstance(value, str) else value
        return VapidKey.from_scalar(scalar)
    except ValueError as exc:
        raise PushConfigError(source, _SCALAR_RULE) from exc


def _segment(document: Mapping[str, object]) -> str:
    """Encode one JWT segment: compact, sorted JSON in unpadded base64url."""
    text = json.dumps(document, separators=_JSON_SEPARATORS, sort_keys=True)
    return b64url_encode(text.encode("utf-8"))
