"""Define ExposeError and every way the Entrance's exposure layer refuses something on purpose.

Exposure (ADR-0033) is where the Hive Entrance meets other machines: the mode check that decides
whether the remote listener may run at all, the Hive's own certificate authority that signs every
device's client certificate for mutual TLS, and the tunnel client the Entrance supervises. Each
refuses a few things by design: a half-configured remote mode, a certificate authority whose stored
halves do not belong together, a certificate request whose signature does not verify. Every such
refusal is one class here, rooted at ``ExposeError``, itself an ``EntranceError`` so a caller that
catches the whole Entrance family catches these too; each also subclasses the
``hivemind.common.errors`` category it belongs to, and carries its own stable, dotted ``code``
(codingrules section 10).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Raised by
    ``hivemind.entrance.expose.plan``, ``hivemind.entrance.expose.tls`` and
    ``hivemind.entrance.expose.tunnel``. Imports only the error roots and the rule names.

Key invariants:
    - Every class sets its own ``code``, prefixed ``hivemind.entrance.``, and no two share one.
    - No message ever carries a private key, a passphrase, a token or a certificate request's
      bytes: messages name settings, secrets, devices and rules only (codingrules 12 and 13).

See Also:
    - hivemind.entrance.errors for the Entrance's root and its other refusals.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for what is refused.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import ConfigurationError, PermissionDeniedError
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.expose.rules import ExposureRule
from hivemind.manifest.schema import EntranceExposure

__all__ = [
    "CertificateAuthorityError",
    "CertificateIssueError",
    "CertificateRequestError",
    "ExposeError",
    "ExposureRefusedError",
]


class ExposeError(EntranceError):
    """Root of every error the Entrance's exposure layer raises on purpose."""

    code: ClassVar[str] = "hivemind.entrance.expose_error"


class ExposureRefusedError(ExposeError, ConfigurationError):
    """Raise when ``[entrance]`` asks for an exposure this host or manifest cannot honour safely.

    Carries the one ``ExposureRule`` that failed, so ``hive serve`` can say exactly what to fix;
    the Entrance never starts a listener the rules did not allow.
    """

    code: ClassVar[str] = "hivemind.entrance.exposure_refused"

    def __init__(self, mode: EntranceExposure, rule: ExposureRule, detail: str) -> None:
        """Build the error for one broken rule.

        Args:
            mode: The ``[entrance] expose`` value that was refused.
            rule: The first rule it breaks, in ``plan_exposure``'s order.
            detail: A sentence naming the offending setting's non-secret value, e.g. "remote_bind
                is 192.0.2.7:8711."
        """
        super().__init__(
            f"The Hive Entrance refuses to start with expose = {mode.value!r} ({rule.value}): "
            f"{rule.requirement}. {detail}"
        )
        self.mode = mode
        self.rule = rule


class CertificateAuthorityError(ExposeError):
    """Raise when the Hive's certificate authority cannot be loaded, or cannot sign right now.

    The stored key and certificate do not belong together, one is missing, neither is what it
    should be, or the authority is outside its validity window. The message names the secrets,
    never their contents.
    """

    code: ClassVar[str] = "hivemind.entrance.certificate_authority_invalid"


class CertificateRequestError(ExposeError, PermissionDeniedError):
    """Raise when a device's certificate signing request will not be signed.

    It does not parse, its signature does not verify (whoever sent it does not hold the key), or
    its key is of a kind or size the Hive does not certify. The request's bytes never appear.
    """

    code: ClassVar[str] = "hivemind.entrance.certificate_request_refused"

    def __init__(self, device_id: str, reason: str) -> None:
        """Build the error for one refused request.

        Args:
            device_id: The device the certificate was asked for.
            reason: Why, as a clause naming no key material, e.g. "its signature does not
                verify".
        """
        super().__init__(f"The certificate request for device {device_id} was refused: {reason}.")
        self.device_id = device_id


class CertificateIssueError(ExposeError, ConfigurationError):
    """Raise when a certificate cannot be issued as asked: a malformed device id or passphrase.

    Both come from the Entrance's own code, not from the device, so this is a caller's mistake;
    the message states the rule, never the passphrase or its length.
    """

    code: ClassVar[str] = "hivemind.entrance.certificate_issue_invalid"
