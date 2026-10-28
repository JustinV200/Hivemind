"""Make a packet's secrets folder readable by its owner only, on Windows and POSIX alike.

A Pollen Packet's `secrets/` folder holds a live invite and a live tailnet key until the device
spends them, so nobody but the operator's account may open it. On POSIX that is the folder's mode,
0700, set at creation, and each file's, 0600. On Windows a mode means nothing, and a packet folder
the operator names may sit somewhere other accounts can read (a shared drive), so the folder gets an
explicit, protected access list: full control for the owner (OWNER RIGHTS) and for SYSTEM, inherited
by every file put in it, and nothing inherited from above. It is set before any secret is written,
so no file is ever readable by anyone else, not even for an instant. `is_owner_only` reads the same
thing back, for `hive doctor`-style checks and for tests.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Called by `hivemind.swarm.packet.writer`. Calls into `os` and, on Windows, advapi32 through
    `ctypes`.

Key invariants:
    - `make_private_dir` refuses a folder that already exists, so it never narrows, or trusts,
      an access list someone else set.
    - `write_private` creates a new file (never overwrites) with mode 0600, inside a folder
      `make_private_dir` made.

See Also:
    - https://learn.microsoft.com/windows/win32/secauthz/security-descriptor-string-format for
      the SDDL string below.
    - hivemind.common.secrets.file for the Hive's own owner-only files.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from pathlib import Path

from hivemind.swarm.errors import PacketError

DIRECTORY_MODE = 0o700  # POSIX: the owner may list, read and write; nobody else anything.
FILE_MODE = 0o600  # POSIX: the owner may read and write; nobody else anything.
# Protected DACL (P: nothing inherited from above), full access (FA) for the owner (OW, OWNER
# RIGHTS) and SYSTEM (SY), inherited by files and folders created inside (OICI).
OWNER_ONLY_SDDL = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)"
OWNER_ONLY_TRUSTEES = frozenset({"OW", "SY"})  # The only SIDs an owner-only DACL may name.
SDDL_REVISION_1 = 1  # sddl.h: the only revision there is.
SE_FILE_OBJECT = 1  # accctrl.h: the object is a file or folder.
DACL_SECURITY_INFORMATION = 0x4  # winnt.h: read or write the DACL.
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000  # winnt.h: and block inheritance from above.
_ACE = re.compile(r"\(([^)]*)\)")  # One ACE in an SDDL string; its last field is the trustee.
_O_BINARY = getattr(os, "O_BINARY", 0)  # Windows only: no "\n" -> "\r\n" in a secret's bytes.

__all__ = ["DIRECTORY_MODE", "FILE_MODE", "is_owner_only", "make_private_dir", "write_private"]


def make_private_dir(path: Path) -> None:
    """Create `path` so only its owner can open it or anything later put in it.

    Args:
        path: A folder that must not exist yet; its parent must.

    Raises:
        FileExistsError: `path` already exists.
        PacketError: Windows refused the access list.
    """
    # Mode applies on POSIX at creation (under the umask, which only removes bits).
    path.mkdir(mode=DIRECTORY_MODE)
    if sys.platform == "win32":
        # Private handles: argtypes set here change no other caller's view of advapi32.
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32]
        advapi32.SetNamedSecurityInfoW.argtypes += [ctypes.c_void_p] * 4
        descriptor = ctypes.c_void_p()
        # SAFETY: documented Win32 calls on a folder this process just created; freed below.
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            OWNER_ONLY_SDDL, SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise PacketError(f"Could not build an access list (Win32 {ctypes.get_last_error()}).")
        try:
            present, defaulted, dacl = ctypes.c_int(), ctypes.c_int(), ctypes.c_void_p()
            advapi32.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            )
            flags = DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
            failed = advapi32.SetNamedSecurityInfoW(
                str(path), SE_FILE_OBJECT, flags, None, None, dacl, None
            )
        finally:
            kernel32.LocalFree(descriptor)
        if failed:
            raise PacketError(f"Could not make {path} owner-only (Win32 error {failed}).")


def write_private(path: Path, text: str) -> None:
    """Create `path` holding `text`, readable by its owner only.

    Args:
        path: A new file inside a folder `make_private_dir` made.
        text: What to write, as UTF-8.

    Raises:
        FileExistsError: `path` already exists.
    """
    # O_EXCL: a secret never lands in a file someone else created first.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def is_owner_only(path: Path) -> bool:
    """Say whether only `path`'s owner (and, on Windows, SYSTEM) may open it.

    Args:
        path: A file or folder.

    Returns:
        On POSIX, whether the group and others have no permission bits; on Windows, whether
        every entry of its access list names the owner or SYSTEM.
    """
    # Both platforms in one if/else: mypy exempts Win32-only ctypes names only in a matching branch.
    if sys.platform == "win32":
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        descriptor, dacl, text = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_wchar_p()
        # SAFETY: read-only Win32 queries; both allocations are freed below.
        if advapi32.GetNamedSecurityInfoW(
            str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None,
            ctypes.byref(dacl), None, ctypes.byref(descriptor),
        ):  # fmt: skip
            return False
        try:
            advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, SDDL_REVISION_1, DACL_SECURITY_INFORMATION, ctypes.byref(text), None
            )
            sddl = text.value or ""
        finally:
            kernel32.LocalFree(text)
            kernel32.LocalFree(descriptor)
        trustees = {ace.rsplit(";", 1)[-1] for ace in _ACE.findall(sddl)}
        return bool(trustees) and trustees <= OWNER_ONLY_TRUSTEES
    else:
        return path.stat().st_mode & 0o077 == 0
