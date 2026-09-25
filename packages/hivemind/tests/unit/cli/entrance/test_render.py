"""Test hivemind.cli.entrance.render: the approval view, a device row and an invite as printed.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/render.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.cli.entrance import device_lines, device_row, invite_lines
from hivemind.entrance.enrol import DeviceDescription, DeviceStatus
from hivemind.entrance.models import DeviceView, InviteView

_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _view(description: DeviceDescription | None) -> DeviceView:
    """A waiting device as the Landing Board shows it."""
    return DeviceView(
        id="device_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        name="laptop",
        status=DeviceStatus.PENDING,
        key_kind="ed25519",
        fingerprint="MFRG-GZDF-MZTW-Q2LK",
        interactive=False,
        capabilities=[],
        spend_cap_usd_per_day=None,
        expires_at=_AT,
        loopback_bound=False,
        backup_eligible=True,
        backup_state=False,
        description=description,
        created_at=_AT,
        approved_at=None,
        last_seen_at=None,
    )


def test_the_approval_view_shows_the_fingerprint_backup_flags_and_self_description() -> None:
    description = DeviceDescription(name="Pixel 9", platform="Android 16", user_agent="UA")
    view = _view(description)

    lines = "\n".join(device_lines(view))

    assert "fingerprint MFRG-GZDF-MZTW-Q2LK" in lines
    assert "eligible=True, synced=False" in lines
    assert "says it is: Pixel 9" in lines and "platform: Android 16" in lines
    assert "device_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3" in device_row(view)


def test_the_device_view_says_whether_a_certificate_was_requested_or_is_held() -> None:
    waiting = _view(None).model_copy(update={"certificate_requested": True})
    holding = _view(None).model_copy(
        update={"certificate_serial": "5f3a", "certificate_not_after": _AT}
    )

    asked, held, neither = (
        "\n".join(device_lines(view)) for view in (waiting, holding, _view(None))
    )

    assert "client certificate: requested" in asked
    assert "client certificate: serial 5f3a, until 2026-09-24T12:00:00+00:00" in held
    assert "client certificate" not in neither


def test_an_invite_prints_its_code_link_hive_and_a_qr_code() -> None:
    invite = InviteView(
        device_id="device_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        code="ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ",
        url="http://localhost:8710/enrol#code=ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ",
        qr_svg="<svg/>",
        expires_at=_AT,
    )

    lines = invite_lines(invite, "hive_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3", "work laptop")

    text = "\n".join(lines)
    assert "Code: ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ" in text
    assert (
        "hive remote enrol http://localhost:8710 --hive hive_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3 "
        "--code ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ --name 'work laptop'"
    ) in text
    assert len(lines) > 10  # The QR code's rows follow the text.
