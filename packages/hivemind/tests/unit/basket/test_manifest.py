"""Tests for hivemind.basket.manifest: signing, verification, tamper detection and bounds.

Fits into the Hive:
    Mirrors src/hivemind/basket/manifest.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.manifest for the module under test.
"""

from __future__ import annotations

import pytest
from builders.basket import make_blob, make_manifest, make_signer
from pydantic import ValidationError

from hivemind.basket.errors import BlobIntegrityError, BlobRefusedError
from hivemind.basket.manifest import MAX_MANIFEST_JSON_CHARS, BlobManifest
from hivemind.basket.models import BlobState
from hivemind.cell import HoneyClearance, OsFamily
from waggle.signing import Ed25519Verifier


def test_a_signed_manifest_verifies_for_the_node_that_signed_it() -> None:
    signer, signer_id, verifier = make_signer()
    manifest = BlobManifest.sign(make_blob(), signer_id, signer)

    manifest.verify(verifier)


def test_a_manifest_survives_a_json_round_trip_and_still_verifies() -> None:
    signer, signer_id, verifier = make_signer()
    manifest = BlobManifest.sign(make_blob(platform_tags=(OsFamily.LINUX,)), signer_id, signer)

    parsed = BlobManifest.from_json(manifest.to_json())

    assert parsed == manifest
    parsed.verify(verifier)


@pytest.mark.parametrize(
    "change",
    [
        {"clearance": HoneyClearance.C2},
        {"tainted": True},
        {"size_bytes": 999},
        {"sha256": "0" * 64},
        {"platform_tags": (OsFamily.WINDOWS,)},
    ],
)
def test_changing_any_signed_fact_breaks_the_signature(change: dict[str, object]) -> None:
    signer, signer_id, verifier = make_signer()
    manifest = BlobManifest.sign(make_blob(), signer_id, signer)
    forged = manifest.model_copy(update={"blob": manifest.blob.model_copy(update=change)})

    with pytest.raises(BlobIntegrityError):
        forged.verify(verifier)


def test_retiring_a_blob_does_not_invalidate_its_manifest() -> None:
    # State is the one unsigned fact: a copy already shipped must stay verifiable after a retire.
    signer, signer_id, verifier = make_signer()
    manifest = BlobManifest.sign(make_blob(), signer_id, signer)
    retired = manifest.model_copy(
        update={"blob": manifest.blob.model_copy(update={"state": BlobState.RETIRED})}
    )

    retired.verify(verifier)


def test_a_signer_the_verifier_does_not_know_is_a_refusal_not_an_integrity_error() -> None:
    manifest = make_manifest()
    _, _, stranger = make_signer()

    with pytest.raises(BlobRefusedError) as raised:
        manifest.verify(stranger)

    assert raised.value.reason_code == "unknown_signer"


def test_a_signature_from_another_key_under_the_same_node_id_fails() -> None:
    signer, signer_id, _ = make_signer()
    other, _, _ = make_signer()
    manifest = BlobManifest.sign(make_blob(), signer_id, signer)
    verifier = Ed25519Verifier({signer_id: other.public_key_bytes})

    with pytest.raises(BlobIntegrityError):
        manifest.verify(verifier)


def test_the_canonical_bytes_do_not_depend_on_field_order_and_carry_the_purpose() -> None:
    blob = make_blob()
    first = make_manifest(blob).canonical_bytes()
    second = make_manifest(blob).canonical_bytes()

    assert first == second
    assert b"hivemind.basket.blob_manifest.v1" in first
    assert b'"state"' not in first


def test_an_oversized_manifest_json_is_refused_before_parsing() -> None:
    with pytest.raises(ValueError, match="cap"):
        BlobManifest.from_json("x" * (MAX_MANIFEST_JSON_CHARS + 1))


def test_a_malformed_manifest_json_is_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        BlobManifest.from_json('{"blob": {}}')
