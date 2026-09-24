"""Define the devices resource's bodies: a device as the Landing Board shows it, and its changes.

Every client of the Hive Entrance is an enrolled device (ADR-0033). ``DeviceView`` is what a device
may see of one: its name, standing, key kind and fingerprint, what it may do and spend, and the
self-description it sent at redemption, which every surface escapes because the device wrote it,
and its mutual-TLS client certificate by serial, fingerprint and expiry; it never carries the public
key or a credential id. ``CertificateView`` is a device's own certificate, which it fetches after
approval (``GET /v1/devices/me/certificate``). ``ApprovedDeviceView`` is what the loopback approval
answers: the view, the certificate issued from the device's request (for an operator carrying it
to a device that registered offline) and, for a browser, its PKCS#12 bundle and passphrase, handed
to the operator this once and stored nowhere. The change bodies are the operator's decisions on
loopback: revoking (optionally cancelling the device's open goals in the same step) and widening
what an approved device may do.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.devices`` and ``.entrance``; published in the OpenAPI document.
    Calls into the enrolment model and pydantic.

Key invariants:
    - A view carries no key material beyond the fingerprint; only ``ApprovedDeviceView`` ever
      carries a private key (sealed in a bundle), and only in the loopback approval's answer.

See Also:
    - hivemind.entrance.enrol for the device model and its state machine.
"""

from __future__ import annotations

import base64
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.enrol import Approval
from hivemind.entrance.enrol.models import (
    MAX_CAPABILITIES,
    CapabilityText,
    DeviceDescription,
    EnrolledDevice,
)
from hivemind.entrance.enrol.state import DeviceStatus
from waggle.messages.base import DeviceIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = [
    "ApprovedDeviceView",
    "BundleView",
    "CertificateView",
    "DeviceList",
    "DeviceView",
    "RevocationView",
    "RevokeBody",
    "WidenBody",
    "approved_view",
    "device_view",
]


class DeviceView(BaseModel):
    """One enrolled device, as the Landing Board shows it."""

    model_config = _CONFIG

    id: DeviceIdField = Field(description="The device's id.")
    name: str = Field(description="Its name, as the operator bound it at approval.")
    status: DeviceStatus = Field(description="INVITED, PENDING, APPROVED, LOCKED, DENIED, ...")
    key_kind: str | None = Field(description="ed25519 or passkey; null before redemption.")
    fingerprint: str | None = Field(description="Its key's fingerprint; null before redemption.")
    interactive: bool = Field(description="A person types at it, so it may step up.")
    capabilities: list[str] = Field(description="What it may do, as capability strings.")
    spend_cap_usd_per_day: float | None = Field(description="Its daily cap; null: uncapped.")
    expires_at: datetime | None = Field(description="When its current standing lapses.")
    loopback_bound: bool = Field(description="The Hive Stand console: loopback sessions only.")
    backup_eligible: bool = Field(description="A passkey that may be synced elsewhere.")
    backup_state: bool = Field(description="A passkey that was synced when registered.")
    description: DeviceDescription | None = Field(
        description="What it said about itself at redemption (display text; escape it)."
    )
    created_at: datetime = Field(description="When its record was created (invite minted).")
    approved_at: datetime | None = Field(description="When the operator approved it.")
    last_seen_at: datetime | None = Field(description="When it last authenticated.")
    certificate_requested: bool = Field(
        default=False,
        description="It sent (or was registered with) a certificate signing request: a client "
        "certificate is signed from it at approval when the Hive runs its own authority.",
    )
    certificate_serial: str | None = Field(
        default=None, description="Its client certificate's serial, lowercase hex; null: none."
    )
    certificate_fingerprint: str | None = Field(
        default=None,
        description="SHA-256 of its client certificate, lowercase hex; compare it on the device.",
    )
    certificate_not_after: datetime | None = Field(
        default=None, description="When its client certificate expires."
    )


class CertificateView(BaseModel):
    """A device's own mutual-TLS client certificate, as it fetches it after approval."""

    model_config = _CONFIG

    serial: str = Field(description="Its serial number, lowercase hex.")
    fingerprint: str = Field(description="SHA-256 of the certificate (DER), lowercase hex.")
    not_after: datetime = Field(description="When it expires; the device enrols again after.")
    certificate_pem: str = Field(
        description="The certificate, PEM: present it with the device's own key on every "
        "connection to a remote listener that demands client certificates."
    )


class BundleView(BaseModel):
    """A browser's PKCS#12 bundle, answered to the approving operator once and stored nowhere."""

    model_config = _CONFIG

    pkcs12_base64: str = Field(
        description="The bundle (a fresh key and its certificate, no authority), standard base64."
    )
    passphrase: str = Field(description="What seals it; the operator types it on the device.")


class ApprovedDeviceView(DeviceView):
    """An approval's answer: the device, and the certificate or bundle issued with it."""

    certificate_pem: str | None = Field(
        default=None,
        description="The client certificate issued from the device's request, PEM (public: the "
        "device can also fetch it); null when none was issued.",
    )
    bundle: BundleView | None = Field(
        default=None,
        description="A passkey device's PKCS#12 bundle, sealed under mutual TLS; answered once, "
        "stored nowhere; null otherwise.",
    )


class DeviceList(BaseModel):
    """Every device matching a listing."""

    model_config = _CONFIG

    devices: list[DeviceView] = Field(description="The devices, oldest first.")


class RevokeBody(BaseModel):
    """Revoke a device (loopback only)."""

    model_config = _CONFIG

    cancel_goals: bool = Field(
        default=False,
        description="Also cancel the device's open goals in the same step: placed work is "
        "stopped on its Warden; a goal whose Warden cannot be reached is named as left running. "
        "Its goal requests not planned yet are refused whatever this says.",
    )


class RevocationView(BaseModel):
    """What a revocation did."""

    model_config = _CONFIG

    device: DeviceView = Field(description="The device, REVOKED.")
    goals_cancelled: list[str] = Field(description="The open goals cancelled in the same step.")
    goals_left_running: list[str] = Field(description="The open goals still running.")
    requests_refused: list[str] = Field(
        default_factory=list,
        description="Its goal requests not planned yet, refused in the same step so they never "
        "run.",
    )


class WidenBody(BaseModel):
    """Change what an approved device may do (loopback only, after step-up)."""

    model_config = _CONFIG

    capabilities: list[CapabilityText] = Field(
        max_length=MAX_CAPABILITIES,
        description="Its whole new capability set, inside the device role's ceiling.",
    )
    spend_cap_usd_per_day: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="A new daily cap; null keeps it."
    )


def device_view(device: EnrolledDevice) -> DeviceView:
    """Shape a stored device for the Landing Board.

    Args:
        device: The record as the Entrance tables hold it.

    Returns:
        Its view: no public key, no credential id.
    """
    return DeviceView(
        id=device.id,
        name=device.name,
        status=device.status,
        key_kind=device.key_kind.value if device.key_kind is not None else None,
        fingerprint=device.fingerprint,
        interactive=device.interactive,
        capabilities=list(device.capabilities),
        spend_cap_usd_per_day=device.spend_cap_usd_per_day,
        expires_at=device.expires_at,
        loopback_bound=device.loopback_bound,
        backup_eligible=device.backup_eligible,
        backup_state=device.backup_state,
        description=device.description,
        created_at=device.created_at,
        approved_at=device.approved_at,
        last_seen_at=device.last_seen_at,
        **_certificate_fields(device),
    )


def approved_view(approval: Approval) -> ApprovedDeviceView:
    """Shape an approval's outcome for the operator who approved it.

    Args:
        approval: The approved device and the bundle sealed for it, if any.

    Returns:
        The device's view, its certificate's PEM when one was issued, and the bundle.
    """
    device, sealed = approval.device, approval.bundle
    bundle = None
    if sealed is not None:
        encoded = base64.b64encode(sealed.pkcs12).decode("ascii")
        bundle = BundleView(pkcs12_base64=encoded, passphrase=sealed.passphrase.get_secret_value())
    certificate = device.certificate
    return ApprovedDeviceView(
        **device_view(device).model_dump(),
        # A bundle's certificate is inside the bundle; only a request's is answered as PEM.
        certificate_pem=certificate.pem if certificate is not None and bundle is None else None,
        bundle=bundle,
    )


def _certificate_fields(device: EnrolledDevice) -> dict[str, object]:
    """The view's certificate fields: whether one was requested, and the one issued."""
    certificate = device.certificate
    return {
        "certificate_requested": device.certificate_request is not None,
        "certificate_serial": certificate.serial if certificate is not None else None,
        "certificate_fingerprint": certificate.fingerprint if certificate is not None else None,
        "certificate_not_after": certificate.not_after if certificate is not None else None,
    }
