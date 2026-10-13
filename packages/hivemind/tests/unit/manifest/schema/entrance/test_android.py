"""Tests for hivemind.manifest.schema.entrance.android: the [entrance.android] section.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/entrance/android.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0050-android-client-capacitor-and-push-delivery.md for what each setting does.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.entrance import (
    DEFAULT_WEBVIEW_ORIGIN,
    EntranceAndroidSection,
    EntranceSection,
)

_FINGERPRINT = ":".join(f"{byte:02X}" for byte in range(32))
_PACKAGE = "net.example.hivemind"


def test_the_section_defaults_to_unconfigured_with_capacitors_pinned_origin() -> None:
    section = EntranceSection().android

    assert section == EntranceAndroidSection()
    assert (section.package_name, section.cert_fingerprints) == ("", ())
    assert section.webview_origin == DEFAULT_WEBVIEW_ORIGIN == "https://observation.hive.local"
    assert section.configured is False


def test_a_package_and_a_fingerprint_together_configure_the_client() -> None:
    section = EntranceAndroidSection(package_name=_PACKAGE, cert_fingerprints=(_FINGERPRINT,))

    assert section.configured is True


def test_it_loads_from_the_entrance_table_as_manifest_toml_writes_it() -> None:
    section = EntranceSection.model_validate(
        {"android": {"package_name": _PACKAGE, "cert_fingerprints": [_FINGERPRINT]}}
    )

    assert section.android.cert_fingerprints == (_FINGERPRINT,)


def test_fingerprints_are_stored_upper_case_and_once() -> None:
    section = EntranceAndroidSection(
        package_name=_PACKAGE, cert_fingerprints=(_FINGERPRINT.lower(), _FINGERPRINT)
    )

    assert section.cert_fingerprints == (_FINGERPRINT,)


@pytest.mark.parametrize(
    "fingerprint",
    [
        "",
        "AA:BB",
        ":".join(["AA"] * 31),  # One pair short.
        ":".join(["AA"] * 33),  # One pair over.
        "".join(["AA"] * 32),  # No colons.
        ":".join(["ZZ"] * 32),  # Not hex.
        ":".join(["AAA"] * 32),  # Pairs of three.
        _FINGERPRINT + ":",
    ],
)
def test_a_fingerprint_must_be_32_colon_separated_hex_pairs(fingerprint: str) -> None:
    with pytest.raises(ValidationError, match="SHA-256 fingerprints"):
        EntranceAndroidSection(package_name=_PACKAGE, cert_fingerprints=(fingerprint,))


@pytest.mark.parametrize(
    "package", ["hivemind", ".example.app", "net..app", "1net.app", "net.app!"]
)
def test_a_package_name_must_be_a_dotted_application_id(package: str) -> None:
    with pytest.raises(ValidationError, match="package_name"):
        EntranceAndroidSection(package_name=package, cert_fingerprints=(_FINGERPRINT,))


@pytest.mark.parametrize(
    "fields",
    [{"package_name": _PACKAGE}, {"cert_fingerprints": (_FINGERPRINT,)}],
    ids=["package-only", "fingerprint-only"],
)
def test_half_an_app_identity_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="together"):
        EntranceAndroidSection.model_validate(fields)


@pytest.mark.parametrize(
    "origin",
    [
        "http://observation.hive.local",
        "https://observation.hive.local/",
        "https://observation.hive.local/app",
        "https://user@observation.hive.local",
        "observation.hive.local",
        "https://",
        "https://host?x=1",
    ],
)
def test_the_webview_origin_must_be_a_bare_https_origin(origin: str) -> None:
    with pytest.raises(ValidationError, match="webview_origin"):
        EntranceAndroidSection(webview_origin=origin)


def test_the_webview_origin_may_be_moved_and_may_carry_a_port() -> None:
    section = EntranceAndroidSection(webview_origin="https://app.hive.test:8443")

    assert section.webview_origin == "https://app.hive.test:8443"


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        EntranceAndroidSection.model_validate({"package": _PACKAGE})
