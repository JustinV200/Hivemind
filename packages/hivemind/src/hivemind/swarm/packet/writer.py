"""Write a Pollen Packet's folder: the pollen build, the installer, the README and the two secrets.

A Pollen Packet is one folder an operator carries to a new device and runs one script from. This
module lays it out:

    pollen.exe | pollen           the cached build for the packet's platform
    pollinate.ps1 | pollinate.sh  the installer, with the Hive Stand's host and port filled in
    README.txt                    one screen of plain instructions
    secrets/invite.txt            the `pollen1.` invite, for `pollen enrol -`
    secrets/tailscale-authkey.txt the single-use tailnet key, for `tailscale up --auth-key=file:`

The installer and README are templates shipped beside this module and filled by plain string
replacement. Only values this module has validated reach a script: the host must be a DNS name or
an IP literal and the port an integer, so nothing the manifest holds can inject a command. The two
secrets go into an owner-only folder (`acl`) made before either is written; the installer deletes
that folder when it finishes, whether it worked or not.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Called by `hivemind.swarm.packet.builder`. Calls into `acl`, `platforms`, the `templates`
    resources and the standard library.

Key invariants:
    - Secrets are written only under `secrets/`, owner-only; nothing else in the folder holds one.
    - A target that is not `wss://` on a DNS name or IP literal is refused before anything is
      minted (`HiveTarget.from_url`).

See Also:
    - hivemind/swarm/packet/templates/ for the installer and README.
    - docs/runbooks/enrolling-a-device.md, "Pollen Packet over Tailscale".
"""

from __future__ import annotations

import ipaddress
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet.acl import make_private_dir, write_private
from hivemind.swarm.packet.platforms import PacketPlatform

SECRETS_DIR = "secrets"  # The owner-only folder the installer deletes when it is done.
INVITE_FILE = "invite.txt"  # The `pollen1.` string, piped to `pollen enrol -`.
AUTH_KEY_FILE = "tailscale-authkey.txt"  # Read by `tailscale up --auth-key=file:<path>`.
README_FILE = "README.txt"  # One screen of plain instructions for the device's user.
TEMPLATES = "hivemind.swarm.packet.templates"  # The package the templates ship in.
DEFAULT_WSS_PORT = 443  # What a `wss://` URL with no port means.
PACKET_FILE_COUNT = 5  # The build, the installer, the README and the two secrets.
# A DNS name: dot-separated labels of letters, digits and hyphens; the only text a script embeds.
# Each label is bounded and the dots are explicit, so a long almost-name fails in linear time.
_HOST_NAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)
# The only characters a host may hold, whatever it is: no quote, `%`, space or shell character can
# reach an installer, even before each shell's own quoting below.
_HOST_CHARS = re.compile(r"^[A-Za-z0-9.:-]+$")

__all__ = [
    "AUTH_KEY_FILE",
    "INVITE_FILE",
    "PACKET_FILE_COUNT",
    "README_FILE",
    "SECRETS_DIR",
    "HiveTarget",
    "PacketFiles",
    "write_packet",
]


@dataclass(frozen=True, slots=True)
class HiveTarget:
    """Where the installer checks the Hive Stand answers: the invite's host and port."""

    host: str  # A DNS name (the Hive Stand's MagicDNS name) or an IP literal.
    port: int  # The swarm listener's port.

    @classmethod
    def from_url(cls, url: str) -> HiveTarget:
        """Take the host and port from the URL an invite tells a device to dial.

        Args:
            url: The swarm listener's advertised URL.

        Returns:
            The target.

        Raises:
            PacketError: The URL is not `wss://` on a DNS name or a routable IP literal (no
                loopback, unspecified or scoped address), so a device could not reach it from
                another network, or it could not be embedded safely in an installer script.
        """
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "wss" or not _is_host(host):
            raise PacketError(
                f"[swarm] advertise_url {url!r} is not a wss:// URL a device can reach; set it "
                "to the Hive Stand's MagicDNS name, e.g. wss://hivestand.example.ts.net:8720."
            )
        return cls(host=host, port=parts.port or DEFAULT_WSS_PORT)


@dataclass(frozen=True, slots=True)
class PacketFiles:
    """Everything about a packet that is not a secret."""

    platform: PacketPlatform  # Which build and which installer.
    pollen: Path  # The build to copy in.
    target: HiveTarget  # Where the installer checks the Hive Stand answers.
    label: str  # The device's label, shown in the README.
    expires_at: datetime  # When the invite and the tailnet key lapse, shown in the README.


def write_packet(folder: Path, files: PacketFiles, invite: str, auth_key: SecretStr) -> int:
    """Write the packet into `folder`, an empty folder the caller just made.

    Args:
        folder: The packet folder; empty.
        files: The build, the platform, the target and what the README says.
        invite: The `pollen1.` invite string.
        auth_key: The single-use tailnet key.

    Returns:
        How many files were written.

    Raises:
        FileExistsError: `folder` already holds a file of the same name.
        PacketError: The secrets folder could not be made owner-only.
        OSError: A file could not be written or the build copied.
    """
    platform = files.platform
    # The build and the two filled templates: nothing secret, ordinary permissions.
    shutil.copyfile(files.pollen, folder / platform.binary_name)
    installer = _fill(platform.installer_name, files)
    (folder / platform.installer_name).write_text(installer, encoding="utf-8", newline="\n")
    (folder / README_FILE).write_text(_fill(README_FILE, files), encoding="utf-8", newline="\r\n")
    # The secrets last, into a folder that is owner-only before the first byte lands in it.
    secrets = folder / SECRETS_DIR
    make_private_dir(secrets)
    write_private(secrets / INVITE_FILE, invite)
    write_private(secrets / AUTH_KEY_FILE, auth_key.get_secret_value())
    return PACKET_FILE_COUNT


def _fill(template: str, files: PacketFiles) -> str:
    """Read one shipped template and fill its `@@NAME@@` placeholders with validated values."""
    text = resources.files(TEMPLATES).joinpath(template).read_text(encoding="utf-8")
    run = (
        f"powershell -ExecutionPolicy Bypass -File .\\{files.platform.installer_name}"
        if files.platform.is_windows
        else f"bash ./{files.platform.installer_name}"
    )
    values = {
        "HIVE_HOST": files.target.host,
        "HIVE_HOST_QUOTED": _quoted(files.target.host, is_powershell=files.platform.is_windows),
        "HIVE_PORT": str(files.target.port),
        "PLATFORM": files.platform.value,
        "BINARY": files.platform.binary_name,
        "RUN": run,
        "LABEL": files.label,
        "EXPIRES": files.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    # Each placeholder is replaced verbatim; the label and expiry appear only in the README.
    for name, value in values.items():
        text = text.replace(f"@@{name}@@", value)
    return text


def _is_host(host: str) -> bool:
    """Whether `host` is a DNS name or a routable IP literal, and so safe to embed in a script."""
    # The character rule first: it alone keeps a quote or `%` (a scope id) out of every script.
    if _HOST_CHARS.fullmatch(host) is None:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _HOST_NAME.fullmatch(host) is not None
    # A device on another machine can never reach the Hive Stand at loopback or "any".
    scoped = isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None
    return not (scoped or address.is_loopback or address.is_unspecified)


def _quoted(value: str, *, is_powershell: bool) -> str:
    """Quote `value` as a single-quoted literal for PowerShell or POSIX sh."""
    # Belt and braces: `_is_host` already forbids quotes, but a template must never rely on that.
    if is_powershell:
        return "'" + value.replace("'", "''") + "'"
    return "'" + value.replace("'", "'\\''") + "'"
