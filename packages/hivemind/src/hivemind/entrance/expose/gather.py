"""Gather the facts the exposure check decides on: interfaces, TLS files, platform and time.

``plan_exposure`` is pure; this module is the adapter that reads the real host for it (codingrules
8.3). It asks the injected ``LocalInterfaces`` for the host's interfaces (only in ``vpn`` and
``lan``, the modes judged against them), reads the ``[entrance.tls]`` files in a worker thread
(only in a remote mode) and records what they turned out to be: readable or not, parseable or not,
whether the key belongs to the certificate, the certificate's DNS names and its validity window.
Key material is parsed only to compare public keys and is dropped before this returns; nothing
here logs or raises with a file's contents. TLS paths are resolved the way every manifest path is,
against the manifest's own directory, through the ``resolve_path`` the caller hands in
(``HiveManifest.resolve_path``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Called by
    the Entrance's composition root before it plans the listeners. Calls into the
    ``LocalInterfaces`` it is given, ``cryptography`` and the file system.

Key invariants:
    - A TLS file is never read past ``MAX_TLS_FILE_BYTES``; a larger file is ``UNREADABLE``.
    - No failure to read or parse a TLS file raises: each becomes a ``FileState`` the check turns
      into its precise refusal.
    - The facts hold no private key, only whether it matched.

See Also:
    - hivemind.entrance.expose.facts for the values built here.
    - hivemind.entrance.expose.plan for the check that reads them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from hivemind.entrance.expose.facts import ExposureFacts, FileState, TlsFacts, host_platform
from hivemind.entrance.expose.interfaces import LocalInterfaces
from hivemind.manifest.schema import EntranceExposure, EntranceSection, EntranceTlsSection
from waggle.clock import Clock

MAX_TLS_FILE_BYTES = 1024 * 1024  # A certificate chain is a few KiB; a megabyte is not one.
# The modes whose rules read the host's interfaces; loopback and tunnel never bind a real one.
_INTERFACE_MODES = frozenset({EntranceExposure.VPN, EntranceExposure.LAN})
_PUBLIC_KEY_ENCODING = serialization.Encoding.DER  # Compared as bytes, one canonical encoding.
_PUBLIC_KEY_FORMAT = serialization.PublicFormat.SubjectPublicKeyInfo  # Works for every key type.

__all__ = ["MAX_TLS_FILE_BYTES", "PathResolver", "gather_facts", "read_tls_facts"]

# How a manifest path becomes a real one: HiveManifest.resolve_path, or the identity.
type PathResolver = Callable[[Path], Path]


async def gather_facts(
    section: EntranceSection,
    interfaces: LocalInterfaces,
    clock: Clock,
    resolve_path: PathResolver | None = None,
) -> ExposureFacts:
    """Read everything ``plan_exposure`` needs to know about this host.

    Args:
        section: The manifest's ``[entrance]`` section.
        interfaces: Where the host's interfaces come from (``SystemInterfaces`` in production).
        clock: Stamps the facts; the TLS certificate is judged at this time.
        resolve_path: Resolves a manifest path, normally ``HiveManifest.resolve_path``; None uses
            the TLS paths as written (relative to the working directory).

    Returns:
        The frozen facts. Interfaces are listed only for ``vpn`` and ``lan``, TLS files read only
        for a remote mode; otherwise those parts are empty.
    """
    resolver = resolve_path if resolve_path is not None else _as_written
    # Only vpn and lan are judged against interfaces, so loopback never asks the kernel.
    snapshot = await interfaces.snapshot() if section.expose in _INTERFACE_MODES else ()
    tls = TlsFacts.not_configured()
    if section.expose is not EntranceExposure.LOOPBACK:
        # Latency: two small local file reads and a parse, milliseconds; blocking, so off the
        # event loop. No timeout: a thread cannot be cancelled, and a hung disk hangs startup.
        tls = await asyncio.to_thread(read_tls_facts, section.tls, resolver)
    return ExposureFacts(platform=host_platform(), interfaces=snapshot, tls=tls, now=clock.now())


def read_tls_facts(tls: EntranceTlsSection, resolve_path: PathResolver) -> TlsFacts:
    """Read and describe the configured TLS certificate chain and key. Blocking.

    Args:
        tls: ``[entrance.tls]``, the two paths.
        resolve_path: Resolves a manifest-relative path.

    Returns:
        Each file's state, whether the key matches, and the certificate's names and validity.
    """
    cert_path = resolve_path(Path(tls.cert)) if tls.cert else None
    key_path = resolve_path(Path(tls.key)) if tls.key else None
    cert_state, leaf = _read_certificate(cert_path)
    key_state, key = _read_private_key(key_path)
    # The key is kept only long enough to compare public halves, then goes out of scope.
    matches = leaf is not None and key is not None and _same_public_key(leaf, key)
    return TlsFacts(
        cert_path=cert_path,
        key_path=key_path,
        cert=cert_state,
        key=key_state,
        key_matches_cert=matches,
        dns_names=_dns_names(leaf) if leaf is not None else (),
        not_before=leaf.not_valid_before_utc if leaf is not None else None,
        not_after=leaf.not_valid_after_utc if leaf is not None else None,
    )


def _as_written(path: Path) -> Path:
    """Return ``path`` unchanged: the resolver used when the caller gives none."""
    return path


def _read_certificate(path: Path | None) -> tuple[FileState, x509.Certificate | None]:
    """Read the PEM chain at ``path`` and return its state and first (leaf) certificate."""
    if path is None:
        return FileState.NOT_CONFIGURED, None
    data = _read_bounded(path)
    if data is None:
        return FileState.UNREADABLE, None
    try:
        leaf = x509.load_pem_x509_certificates(data)[0]
        # Extensions parse lazily; touching them here turns a malformed one into UNPARSEABLE
        # now rather than an exception later, in the middle of a name check.
        _dns_names(leaf)
    except ValueError:
        return FileState.UNPARSEABLE, None
    return FileState.VALID, leaf


def _read_private_key(path: Path | None) -> tuple[FileState, PrivateKeyTypes | None]:
    """Read the unencrypted PEM private key at ``path`` and return its state and the key."""
    if path is None:
        return FileState.NOT_CONFIGURED, None
    data = _read_bounded(path)
    if data is None:
        return FileState.UNREADABLE, None
    try:
        # password=None: the manifest has no field for a key passphrase, and the listener could
        # not load an encrypted key without one, so an encrypted key is UNPARSEABLE (TypeError).
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return FileState.UNPARSEABLE, None
    return FileState.VALID, key


def _read_bounded(path: Path) -> bytes | None:
    """Return the file's bytes, or None when it cannot be read or exceeds MAX_TLS_FILE_BYTES."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_TLS_FILE_BYTES + 1)
    except OSError:
        # Missing, a directory, or not readable by the Hive's user: all mean "cannot use it".
        return None
    return data if len(data) <= MAX_TLS_FILE_BYTES else None


def _dns_names(leaf: x509.Certificate) -> tuple[str, ...]:
    """Return the certificate's subjectAltName DNS entries, lowercase; empty when it has none."""
    try:
        extension = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        # Browsers ignore the subject's common name, so no SAN means no usable name at all.
        return ()
    return tuple(name.lower() for name in extension.value.get_values_for_type(x509.DNSName))


def _same_public_key(leaf: x509.Certificate, key: PrivateKeyTypes) -> bool:
    """Return whether ``key`` is the private half of the certificate's public key."""
    theirs = leaf.public_key().public_bytes(_PUBLIC_KEY_ENCODING, _PUBLIC_KEY_FORMAT)
    return key.public_key().public_bytes(_PUBLIC_KEY_ENCODING, _PUBLIC_KEY_FORMAT) == theirs
