"""Provide SoftPasskey: a software WebAuthn authenticator that answers the way a browser does.

Tests and demos need to drive the Hive Entrance's (the Hive's one HTTP door) real passkey
verification (``hivemind.entrance.auth.passkeys``, the ``webauthn`` library underneath) without a
browser. ``SoftPasskey`` plays the browser and its platform authenticator together: ``create`` takes
the creation options JSON an Entrance issued and returns ``PublicKeyCredential.toJSON()`` of a fresh
ES256 credential (``none`` attestation); ``get`` takes request options JSON and returns an assertion
signed over ``authenticatorData || SHA-256(clientDataJSON)``, the counter advanced. The user
presence and user verification flags are set unless a test asks otherwise, and the origin it
reports, the backup flags and the counter step are constructor knobs, so every refusal the verifier
must make (wrong origin, wrong relying party, wrong challenge, no user verification, a counter that
went backwards) can be produced honestly. CBOR comes from ``webauthn.helpers. encode_cbor``, the
library's own public encoder, so no CBOR dependency is added.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Used by the
    passkey tests and by demo clients; never by production code paths (codingrules 14.4: fakes
    live in ``src/`` beside what they fake, and are production quality). Calls into
    ``cryptography`` and ``webauthn.helpers`` only.

Key invariants:
    - ``get`` answers only for the relying party ``create`` registered with, and only when the
      options allow this credential (or allow any), as a real authenticator would.
    - The private key never leaves the instance; nothing here logs or returns it.
    - The JSON produced has exactly the shape a browser's ``toJSON()`` produces: base64url
      without padding for every binary field.

See Also:
    - hivemind.entrance.auth.passkeys for the verification this authenticator feeds.
    - W3C WebAuthn Level 3, sections 6.1 (authenticator data) and 5.8.1 (client data).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import (
    encode_cbor,
    parse_authentication_options_json,
    parse_registration_options_json,
)

from hivemind.entrance.auth.canonical import b64url_encode

CREDENTIAL_ID_BYTES = 32  # A random credential id, the size many platform authenticators use.
_AAGUID = bytes(16)  # All zeroes: the AAGUID "none" attestation reports.
_FLAG_USER_PRESENT = 0x01  # UP: a person touched the authenticator.
_FLAG_USER_VERIFIED = 0x04  # UV: the authenticator verified who that person is.
_FLAG_BACKUP_ELIGIBLE = 0x08  # BE: the credential may be synced to other devices.
_FLAG_BACKUP_STATE = 0x10  # BS: the credential is synced right now.
_FLAG_ATTESTED_DATA = 0x40  # AT: attested credential data follows (registration only).
_COSE_EC2_P256_ES256 = {1: 2, 3: -7, -1: 1}  # kty EC2, alg ES256, crv P-256 (RFC 8152).
_COSE_X, _COSE_Y = -2, -3  # COSE_Key labels of the point's two coordinates.
_COORDINATE_BYTES = 32  # One P-256 coordinate, big-endian.

__all__ = ["CREDENTIAL_ID_BYTES", "SoftPasskey"]


class SoftPasskey:
    """One ES256 credential in software, answering creation and request options like a browser.

    State: the key pair and credential id (made at construction), the relying party and user
    handle (fixed by ``create``) and the signature counter (advanced by ``get``). ``user_verified``
    is a public knob a test may flip between ceremonies: the same authenticator, verified at
    registration, only touched at a later login.
    """

    def __init__(
        self,
        origin: str,
        *,
        user_verified: bool = True,
        backed_up: bool = False,
        counter_step: int = 1,
    ) -> None:
        """Make a new authenticator with a fresh P-256 key and credential id.

        Args:
            origin: The origin the "browser" reports in client data, e.g.
                ``"http://localhost:8710"``; a wrong one must be refused by the verifier.
            user_verified: Set the UV flag; False models an authenticator that only saw a touch.
            backed_up: Set the BE and BS flags, as a synced passkey (a password manager's) does.
            counter_step: How far each ``get`` advances the counter; 0 models the many platform
                authenticators that always report 0.
        """
        self._origin = origin
        self.user_verified = user_verified
        self._backed_up = backed_up
        self._counter_step = counter_step
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._credential_id = secrets.token_bytes(CREDENTIAL_ID_BYTES)
        self._sign_count = 0
        self._rp_id: str | None = None
        self._user_handle: bytes | None = None

    @property
    def credential_id(self) -> bytes:
        """The credential's raw id, as a registration reports it.

        Returns:
            ``CREDENTIAL_ID_BYTES`` random bytes, fixed for this instance.
        """
        return self._credential_id

    @property
    def sign_count(self) -> int:
        """The counter the last response carried.

        Returns:
            0 until the first ``get``, then the last assertion's counter.
        """
        return self._sign_count

    def create(self, options_json: str) -> str:
        """Answer creation options as ``navigator.credentials.create`` would.

        Args:
            options_json: ``PublicKeyCredentialCreationOptionsJSON`` from the Entrance.

        Returns:
            ``PublicKeyCredential.toJSON()`` of the new credential, as a JSON string.

        Raises:
            ValueError: The options name no relying party id.
        """
        options = parse_registration_options_json(options_json)
        if not options.rp.id:
            raise ValueError("The creation options name no relying party id.")
        self._rp_id = options.rp.id
        self._user_handle = options.user.id
        client_data = self._client_data("webauthn.create", options.challenge)
        attested = self._attested_credential_data()
        auth_data = self._authenticator_data(_FLAG_ATTESTED_DATA) + attested
        attestation = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        response = {
            "clientDataJSON": b64url_encode(client_data),
            "attestationObject": b64url_encode(attestation),
            "transports": ["internal"],
        }
        return self._credential_json(response)

    def get(self, options_json: str) -> str:
        """Answer request options as ``navigator.credentials.get`` would, advancing the counter.

        Args:
            options_json: ``PublicKeyCredentialRequestOptionsJSON`` from the Entrance.

        Returns:
            ``PublicKeyCredential.toJSON()`` of the assertion, as a JSON string.

        Raises:
            ValueError: No credential was created yet, the options are for another relying party,
                or they allow only other credentials: a real authenticator has nothing to offer.
        """
        options = parse_authentication_options_json(options_json)
        allowed = [descriptor.id for descriptor in options.allow_credentials or []]
        if self._rp_id is None or options.rp_id != self._rp_id:
            raise ValueError("This authenticator holds no credential for that relying party.")
        if allowed and self._credential_id not in allowed:
            raise ValueError("The request allows only credentials this authenticator lacks.")
        self._sign_count += self._counter_step
        client_data = self._client_data("webauthn.get", options.challenge)
        auth_data = self._authenticator_data(0)
        # WebAuthn section 6.3.3: the signature covers authData || SHA-256(clientDataJSON), DER.
        signed = auth_data + hashlib.sha256(client_data).digest()
        signature = self._private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
        response = {
            "clientDataJSON": b64url_encode(client_data),
            "authenticatorData": b64url_encode(auth_data),
            "signature": b64url_encode(signature),
            "userHandle": b64url_encode(self._user_handle or b""),
        }
        return self._credential_json(response)

    def _client_data(self, ceremony: str, challenge: bytes) -> bytes:
        """Serialise CollectedClientData for ``ceremony`` as a browser does (compact JSON)."""
        client_data = {
            "type": ceremony,
            "challenge": b64url_encode(challenge),
            "origin": self._origin,
            "crossOrigin": False,
        }
        return json.dumps(client_data, separators=(",", ":")).encode("utf-8")

    def _authenticator_data(self, extra_flags: int) -> bytes:
        """Build rpIdHash || flags || signCount for the registered relying party."""
        flags = _FLAG_USER_PRESENT | extra_flags
        if self.user_verified:
            flags |= _FLAG_USER_VERIFIED
        # BS is only meaningful with BE (the library refuses BS alone), so both move together.
        if self._backed_up:
            flags |= _FLAG_BACKUP_ELIGIBLE | _FLAG_BACKUP_STATE
        rp_id_hash = hashlib.sha256((self._rp_id or "").encode("utf-8")).digest()
        return rp_id_hash + bytes([flags]) + self._sign_count.to_bytes(4, "big")

    def _attested_credential_data(self) -> bytes:
        """Build aaguid || idLength || credentialId || COSE public key for a registration."""
        numbers = self._private_key.public_key().public_numbers()
        cose_key = {
            **_COSE_EC2_P256_ES256,
            _COSE_X: numbers.x.to_bytes(_COORDINATE_BYTES, "big"),
            _COSE_Y: numbers.y.to_bytes(_COORDINATE_BYTES, "big"),
        }
        id_length = len(self._credential_id).to_bytes(2, "big")
        return _AAGUID + id_length + self._credential_id + encode_cbor(cose_key)

    def _credential_json(self, response: Mapping[str, object]) -> str:
        """Wrap a ceremony's ``response`` member into the PublicKeyCredential JSON a page sends."""
        credential_id = b64url_encode(self._credential_id)
        credential = {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": response,
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }
        return json.dumps(credential)
