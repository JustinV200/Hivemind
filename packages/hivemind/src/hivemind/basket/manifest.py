"""Define BlobManifest: a Blob's description, signed by the Hive key at deposit.

A manifest is how a receiving Cell knows a blob it was offered is the one the Hive deposited:
the Hive Stand signs the blob's canonical JSON with an Ed25519 key (`waggle.signing`) when the
deposit is accepted, and the receiver verifies that signature, by the signer's node id, before it
keeps a single byte (ADR-0048). The signature covers every fact fixed at deposit (hash, size,
kind, platforms, label, origin, taint) and deliberately not `state`, the one fact `retire` changes
later, so retiring a blob never invalidates the manifest of a copy already shipped. The signed
bytes carry a purpose string so a signature made here can never be replayed as an envelope
signature, or the reverse, even though both use the node's key.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Made by
    `hivemind.basket.deposits`, stored by `hivemind.basket.store`, offered by
    `hivemind.basket.transfer.sender` and verified by `transfer.receiver`. Calls into
    `waggle.codec` (canonical_bytes, Signer, Verifier) and `waggle.errors` only.

Key invariants:
    - `canonical_bytes()` is a pure function of the blob's signed facts and the purpose string:
      the same blob always gives the same bytes, whatever order its fields were built in.
    - `verify` returns only when the signature was made by the key registered for `signer` over
      exactly those bytes; an unknown signer is a refusal, a bad signature is an integrity error.
    - `state` is never signed, so `BlobManifest.sign(b)` and `sign(b.model_copy(state=RETIRED))`
      verify against each other's bytes.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the signing decision.
    - waggle.signing for Ed25519Signer/Ed25519Verifier, the implementations used in practice.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.basket.errors import BlobIntegrityError, BlobRefusedError
from hivemind.basket.models import Blob
from waggle.codec import Signer, Verifier, canonical_bytes
from waggle.errors import InvalidSignatureError, UnknownSignerError
from waggle.ids import IdKind
from waggle.messages.base import id_validator

MANIFEST_PURPOSE = "hivemind.basket.blob_manifest.v1"  # Domain separation for the signed bytes.
MAX_SIGNATURE_CHARS = 128  # An Ed25519 signature is 88 base64 characters; this leaves headroom.
MAX_MANIFEST_JSON_CHARS = 8_192  # A manifest is a few hundred bytes; the wire bounds it too.

__all__ = ["MANIFEST_PURPOSE", "MAX_MANIFEST_JSON_CHARS", "MAX_SIGNATURE_CHARS", "BlobManifest"]

_SignerId = Annotated[str, id_validator(IdKind.NODE)]


class BlobManifest(BaseModel):
    """A Blob plus the node that vouched for it and the signature that proves it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blob: Blob = Field(description="The blob as it was when the Hive accepted the deposit.")
    signer: _SignerId = Field(description="Node id whose Ed25519 key made the signature.")
    signature: str = Field(
        min_length=1,
        max_length=MAX_SIGNATURE_CHARS,
        description="Standard base64 Ed25519 signature over `canonical_bytes()`.",
    )

    def canonical_bytes(self) -> bytes:
        """Return the bytes the signature covers: the blob's sorted-key JSON minus its state.

        Returns:
            UTF-8 canonical JSON (sorted keys, no spaces) of the blob's fixed facts plus the
            purpose string; identical for equal blobs.
        """
        return _signing_bytes(self.blob)

    @classmethod
    def sign(cls, blob: Blob, signer_id: str, signer: Signer) -> BlobManifest:
        """Sign `blob` with the Hive's key and return the manifest.

        Args:
            blob: The blob to vouch for, exactly as it will be stored.
            signer_id: The node id the verifier will look the public key up under.
            signer: The Ed25519 signer (`waggle.signing.Ed25519Signer`) holding the private key.

        Returns:
            A manifest whose `verify` succeeds against a verifier trusting `signer_id`.
        """
        # The signature is made over the same function `verify` recomputes, so the two can never
        # drift apart.
        return cls(blob=blob, signer=signer_id, signature=signer.sign(_signing_bytes(blob)))

    def verify(self, verifier: Verifier) -> None:
        """Check the signature against the key registered for `signer`.

        Args:
            verifier: Trusted public keys by node id (`waggle.signing.Ed25519Verifier`).

        Raises:
            BlobRefusedError: `reason_code` is `unknown_signer`: no key is registered for the
                signer, so the manifest is unattributable (an enrolment gap, not tampering).
            BlobIntegrityError: The signature does not verify: the manifest was altered or signed
                with another key.
        """
        # Waggle separates "nobody we know" from "does not verify"; the Basket keeps the same
        # split because a Warden answers them with different refusal codes.
        try:
            verifier.verify(self.signer, self.canonical_bytes(), self.signature)
        except UnknownSignerError as exc:
            raise BlobRefusedError(
                "unknown_signer",
                f"blob {self.blob.sha256} is signed by {self.signer}, "
                "which this node does not trust.",
            ) from exc
        except InvalidSignatureError as exc:
            raise BlobIntegrityError(
                self.blob.sha256, "the manifest signature does not verify"
            ) from exc

    def to_json(self) -> str:
        """Serialise the manifest as JSON text, for the wire and the store."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, text: str) -> BlobManifest:
        """Parse and validate a manifest from JSON text.

        Args:
            text: JSON produced by `to_json`; untrusted when it arrives in an offer.

        Returns:
            The validated manifest (its signature is not checked: call `verify`).

        Raises:
            ValueError: `text` is longer than `MAX_MANIFEST_JSON_CHARS`.
            pydantic.ValidationError: `text` is not a well-formed manifest.
        """
        # Bounded before parsing so a hostile peer cannot make validation the slow path.
        if len(text) > MAX_MANIFEST_JSON_CHARS:
            raise ValueError(
                f"manifest JSON is {len(text)} characters, over the {MAX_MANIFEST_JSON_CHARS} cap."
            )
        return cls.model_validate_json(text)


def _signing_bytes(blob: Blob) -> bytes:
    """Return the canonical bytes for a blob: its fixed facts plus the purpose string."""
    # `state` is excluded: retiring a blob must not invalidate a copy already shipped.
    facts = blob.model_dump(mode="json", exclude={"state"})
    return canonical_bytes({"purpose": MANIFEST_PURPOSE, **facts})
