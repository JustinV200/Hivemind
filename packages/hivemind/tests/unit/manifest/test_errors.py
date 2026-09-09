"""Tests for hivemind.manifest.errors: ManifestError.

Fits into the Hive:
    Mirrors src/hivemind/manifest/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.errors for the module under test.
"""

from __future__ import annotations

from hivemind.common.errors import ConfigurationError, HiveMindError
from hivemind.manifest.errors import ManifestError


def test_manifest_error_is_a_configuration_error() -> None:
    assert issubclass(ManifestError, ConfigurationError)
    assert issubclass(ManifestError, HiveMindError)


def test_manifest_error_carries_its_own_code() -> None:
    assert ManifestError.code == "hivemind.manifest_error"


def test_manifest_error_message_is_preserved() -> None:
    error = ManifestError("Hive Manifest x.toml failed validation: hive.node_id: field required")

    assert "hive.node_id" in str(error)
