"""Read one generic credential from Windows Credential Manager, through the Win32 API.

The Tailscale OAuth client's secret can be kept in the Windows Credential Manager of the account
that runs the Hive Stand (`cmdkey /generic:<target> /user:<client id> /pass`, which prompts for the
password so it never lands in shell history). `read_generic_credential` reads it back with
`CredReadW` through `ctypes`, which ships with Python, so no keyring package is added for one
read. Windows stores a password typed into `cmdkey` or the Control Panel as UTF-16LE, which is how
the blob is decoded.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Called by `hivemind.swarm.packet.credentials.CredmanSecretSource`. Calls into `ctypes`
    (advapi32) only.

Key invariants:
    - The blob is copied out and the API's buffer freed with `CredFree` before returning, so no
      Win32 allocation outlives the call.
    - Errors name the target and the Win32 error number, never a value.
    - Off Windows the call is a `PacketError`, never an `AttributeError` on `ctypes.windll`.

See Also:
    - https://learn.microsoft.com/windows/win32/api/wincred/nf-wincred-credreadw for the call.
    - hivemind.cell.local.probe for the same `sys.platform` narrowing around `ctypes.windll`.
"""

from __future__ import annotations

import ctypes
import sys

from hivemind.swarm.errors import PacketError

CRED_TYPE_GENERIC = 1  # wincred.h: a credential any program may read for its own account.

# wincred.h's types spelled with portable ctypes names: `ctypes.wintypes` is Windows-only in spirit
# and this module must import cleanly on Linux, where the read itself is refused.
_DWORD = ctypes.c_uint32
_LPWSTR = ctypes.c_wchar_p

__all__ = ["read_generic_credential"]


class _FileTime(ctypes.Structure):
    """FILETIME: the credential's last-written time; laid out only to reach the fields after it."""

    _fields_ = [("low", _DWORD), ("high", _DWORD)]


class _Credential(ctypes.Structure):
    """CREDENTIALW, field for field as wincred.h declares it."""

    _fields_ = [
        ("Flags", _DWORD),
        ("Type", _DWORD),
        ("TargetName", _LPWSTR),
        ("Comment", _LPWSTR),
        ("LastWritten", _FileTime),
        ("CredentialBlobSize", _DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", _DWORD),
        ("AttributeCount", _DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", _LPWSTR),
        ("UserName", _LPWSTR),
    ]


def read_generic_credential(target: str) -> tuple[str, str]:
    """Return the user name and password of the generic credential named `target`.

    Args:
        target: The credential's target name.

    Returns:
        (user name, password); either may be empty if the credential was saved that way.

    Raises:
        PacketError: Not on Windows, or Credential Manager holds no such credential.
    """
    # mypy only exempts `ctypes.windll` inside a matching platform branch of this very function.
    if sys.platform == "win32":
        found = ctypes.POINTER(_Credential)()
        advapi32 = ctypes.windll.advapi32
        # SAFETY: a documented, read-only Win32 call; the out-pointer is freed below on every path.
        if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
            raise PacketError(
                f"Credential Manager has no generic credential {target!r} for this account "
                f"(Win32 error {ctypes.GetLastError()})."
            )
        try:
            credential = found.contents
            blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return credential.UserName or "", blob.decode("utf-16-le")
        finally:
            advapi32.CredFree(found)
    else:
        raise PacketError(
            f"credman:{target} needs Windows Credential Manager; on this system use env:<prefix>."
        )
