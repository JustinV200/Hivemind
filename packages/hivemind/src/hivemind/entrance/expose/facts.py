"""Define ExposureFacts, the frozen picture of this host the exposure check decides on.

``plan_exposure`` is pure (codingrules 8.3): it never reads a file, asks the kernel or looks at the
clock. Everything it needs to know about the host the Hive Entrance runs on is gathered first into
the values here: the platform (it decides the default overlay interface), the network interfaces
and their addresses, what the configured TLS files turned out to hold, and the time the TLS
certificate's validity is judged against. ``TlsFacts`` records states and public properties only
(names, validity, whether the key matches), never key material, so the facts can be logged or
shown in a refusal without leaking anything. ``hivemind.entrance.expose.gather`` builds them from
the real host; a test builds them by hand to put any host in front of the check.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Built by
    ``hivemind.entrance.expose.gather`` (or a test) and read by ``hivemind.entrance.expose.plan``.
    Imports the standard library and the interface snapshot value.

Key invariants:
    - Every value here is frozen; nothing here holds a private key or a secret.
    - ``TlsFacts.dns_names``, ``not_before`` and ``not_after`` describe the certificate only when
      ``cert`` is ``FileState.VALID``; otherwise they are empty and None.

See Also:
    - hivemind.entrance.expose.gather for how the facts are read from the host.
    - hivemind.entrance.expose.plan for the rules that read them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from hivemind.entrance.expose.interfaces import InterfaceAddresses

# sys.platform to HostPlatform's value: "linux" on every Linux distribution (since Python 3.3),
# "win32" on 32- and 64-bit Windows alike, "darwin" on macOS; anything else is "other".
_PLATFORM_NAMES = {"linux": "linux", "win32": "windows", "darwin": "macos"}

__all__ = [
    "ExposureFacts",
    "FileState",
    "HostPlatform",
    "TlsFacts",
    "host_platform",
]


class HostPlatform(Enum):
    """The operating system family, as far as the exposure rules care."""

    LINUX = "linux"  # Tailscale's interface is tailscale0.
    WINDOWS = "windows"  # Tailscale's interface is "Tailscale".
    MACOS = "macos"  # Tailscale's interface is a utun<N> whose number is not fixed.
    OTHER = "other"  # No known default; the overlay interface must be named.


class FileState(Enum):
    """What one configured TLS file turned out to be when the facts were gathered."""

    NOT_CONFIGURED = "not_configured"  # The manifest names no file at all.
    UNREADABLE = "unreadable"  # Missing, a directory, too large, or not readable by the Hive.
    UNPARSEABLE = "unparseable"  # Read, but not the PEM content it must hold.
    VALID = "valid"  # Read and parsed.


@dataclass(frozen=True, slots=True)
class TlsFacts:
    """What ``[entrance.tls]`` names on disk, as public properties only.

    Attributes:
        cert_path: The certificate chain's path, resolved against the manifest's directory, or
            None when not configured.
        key_path: The private key's path, resolved the same way, or None when not configured.
        cert: The certificate file's state.
        key: The private key file's state.
        key_matches_cert: True when both parsed and the key is the certificate's own.
        dns_names: The server certificate's subjectAltName DNS entries, lowercase, as written
            (wildcards included); browsers ignore the subject's common name, and so does this.
        not_before: The start of the server certificate's validity, or None.
        not_after: The end of the server certificate's validity, or None.
    """

    cert_path: Path | None
    key_path: Path | None
    cert: FileState
    key: FileState
    key_matches_cert: bool
    dns_names: tuple[str, ...]
    not_before: datetime | None
    not_after: datetime | None

    @classmethod
    def not_configured(cls) -> TlsFacts:
        """Return the facts for a manifest that names no TLS files at all.

        Returns:
            Facts with both files ``NOT_CONFIGURED`` and no certificate properties.
        """
        return cls(
            cert_path=None,
            key_path=None,
            cert=FileState.NOT_CONFIGURED,
            key=FileState.NOT_CONFIGURED,
            key_matches_cert=False,
            dns_names=(),
            not_before=None,
            not_after=None,
        )


@dataclass(frozen=True, slots=True)
class ExposureFacts:
    """Everything about this host the exposure check reads, gathered once, frozen.

    Attributes:
        platform: The operating system family; decides the default overlay interface.
        interfaces: Every network interface with its addresses; empty when the mode reads none.
        tls: What the TLS files hold.
        now: When the facts were gathered; the certificate's validity is judged at this time.
    """

    platform: HostPlatform
    interfaces: tuple[InterfaceAddresses, ...]
    tls: TlsFacts
    now: datetime

    def interface(self, name: str) -> InterfaceAddresses | None:
        """Return the interface called ``name``, or None when this host has none by that name.

        Args:
            name: The interface's name, compared exactly (names are case-sensitive on Linux).

        Returns:
            The interface, or None.
        """
        return next((entry for entry in self.interfaces if entry.name == name), None)


def host_platform() -> HostPlatform:
    """Return the platform the Hive runs on, from ``sys.platform``.

    Returns:
        The matching ``HostPlatform``; anything that is not Linux, Windows or macOS is ``OTHER``.
    """
    # A table lookup rather than platform-check branches: mypy treats `sys.platform ==` tests as
    # compile-time facts, which would make every other branch unreachable on the checking host.
    return HostPlatform(_PLATFORM_NAMES.get(sys.platform, HostPlatform.OTHER.value))
