"""Make the self-signed certificate a device container pins when it dials the Hive Stand.

A device in a container reaches the Hive Stand over `wss://` at a host name that the container's
`--add-host` maps to the bridge gateway, so the Stand's certificate has to name that host, and the
invite has to carry the certificate's pin: the SHA-256 of its DER form, which is all a device
checks (it trusts no authority and compares no host name; docs/waggle/enrolment.md section 9).
`certificate_for` writes a fresh certificate and key for one host name and returns them with that
pin. Nothing is cached and nothing is committed: a private key never lives in the repository, and
a certificate cannot expire under a test run.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `e2e.swarm.enrolled` for
    the Stand it starts, and by any scenario that wants a second certificate to show a wrong pin
    refused; builds on `builders.swarm.tls`.

Key invariants:
    - The certificate covers the host name asked for, `localhost` and 127.0.0.1, and is valid a
      day either side of now.
    - Each call makes a fresh key, so two certificates never share a pin.

See Also:
    - builders.swarm.tls for the certificate builder this wraps.
    - waggle.transport.tls for how a dialling device applies the pin.
"""

from __future__ import annotations

from pathlib import Path

from builders.swarm.tls import TlsMaterial, make_tls_material

__all__ = ["certificate_for"]


def certificate_for(directory: Path, hostname: str) -> TlsMaterial:
    """Write a fresh self-signed certificate and key for `hostname` and name its pin.

    Args:
        directory: Where the `.crt` and `.key` files are written.
        hostname: The name the device dials (the one its container maps with `--add-host`).

    Returns:
        The certificate and key paths, and `sha256`, the pin an invite carries.
    """
    return make_tls_material(directory, hostname, hosts=(hostname,))
