"""Keep a laptop's remote Hive profiles: where each Entrance is, and the device key it enrolled.

A laptop reaches a Hive the way every client does, as an enrolled device (ADR-0033): it holds an
Ed25519 key the Hive Stand approved, and remembers which Entrance (the Hive's HTTP door) it
belongs to. A ``RemoteProfile`` is that memory, one per Hive: the Entrance's origin, the CA file
its TLS is pinned to (for a Hive that runs its own authority), the Hive's id (which every login
signature names), the device id, the key's fingerprint as the Hive Stand saw it, and the Hive's
public key a program pins to verify webhooks. It is plain JSON (nothing in it is secret) under the
user's config directory (``typer.get_app_dir``: ``$XDG_CONFIG_HOME`` or ``~/.config`` on Linux,
``%APPDATA%`` on Windows, ``~/Library/Application Support`` on macOS), one file per profile.
The device's **private key is never in the profile**: it lives beside it in a
``hivemind.common.secrets.FileSecretStore`` (owner-only files in an owner-only directory), under
``<profile>.ed25519``, and is written before the profile, so a profile never names a key that is
not there. A device's mutual-TLS client certificate (public) is kept beside its profile, as
``<profile>.crt``; the key it certifies is the same device key, still only in the secret store. A
profile enrolled offline (``hive remote enrol --offline``) knows its Hive and its key but not yet
its device id, which its certificate brings (``hive remote certificate import``).

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. Written by ``hive remote enrol``;
    read by ``hive run --remote``, ``hive inbox --remote`` and ``hive remote profiles``. Calls into
    ``hivemind.common.secrets``, ``hivemind.cli.landing`` (the address rules) and pydantic.

Key invariants:
    - No private key, token or password is ever written to a profile file.
    - A profile's key is in the store before the profile file names it, and leaves after it.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md: "an Ed25519 key in
      a program's secure storage".
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hivemind.cli.landing import EntranceAddress, LandingError, entrance_address
from hivemind.common.secrets import FILE_MODE, FileSecretStore
from waggle.messages.base import DeviceIdField, HiveIdField, UtcDatetime
from waggle.signing import Ed25519Signer

APP_NAME = "hivemind"  # The user's config directory is named for the package.
DEFAULT_PROFILE = "default"  # The profile a command uses when none is named.
KEY_SUFFIX = ".ed25519"  # A profile's key in the secret store: <profile>.ed25519.
PROFILE_SUFFIX = ".json"  # A profile's file: <profile>.json.
CERTIFICATE_SUFFIX = ".crt"  # A profile's client certificate, beside it: <profile>.crt.
# A profile's name: short, lower case, and a safe file and secret name on every platform.
PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")

__all__ = [
    "APP_NAME",
    "CERTIFICATE_SUFFIX",
    "DEFAULT_PROFILE",
    "KEY_SUFFIX",
    "PROFILE_NAME",
    "ProfileStore",
    "RemoteProfile",
    "check_profile_name",
]


class RemoteProfile(BaseModel):
    """One Hive this laptop is enrolled with: its Entrance, and which device the laptop is there.

    Crosses a boundary only as a local JSON file the laptop's own user owns; nothing in it is
    secret (the key is in the secret store beside it).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="The profile's name, e.g. default or garden-hive.")
    entrance_url: str = Field(description="The Entrance's origin: https, or http on loopback.")
    ca_file: str | None = Field(
        default=None, description="The only CA the Entrance's TLS may chain to; None: the system's."
    )
    hive_id: HiveIdField = Field(description="The Hive every login signature names.")
    device_id: DeviceIdField | None = Field(
        default=None,
        description="This laptop's device at that Hive; None until an offline enrolment's "
        "certificate names it.",
    )
    device_name: str = Field(max_length=64, description="What the laptop called itself.")
    fingerprint: str = Field(description="The device key's fingerprint, as the Hive Stand saw it.")
    hive_public_key_hex: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="The Hive's Ed25519 key, pinned at enrolment; None when enrolled offline.",
    )
    enrolled_at: UtcDatetime = Field(description="When the invite was redeemed (or the key made).")

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        """Refuse a name that is not a safe file and secret name."""
        return check_profile_name(value)

    def address(self) -> EntranceAddress:
        """The Entrance and the TLS it is verified with.

        Returns:
            The address, validated again as it is used.
        """
        return entrance_address(self.entrance_url, Path(self.ca_file) if self.ca_file else None)


class ProfileStore:
    """This user's remote profiles (JSON files) and their device keys (a secret store)."""

    def __init__(self, root: Path) -> None:
        """Point the store at ``root``; nothing is created until the first save.

        Args:
            root: The directory holding ``profiles/`` and ``keys/``.
        """
        self.root = root
        self._keys = FileSecretStore(root / "keys")

    @classmethod
    def for_user(cls) -> ProfileStore:
        """The store under this user's own config directory.

        Returns:
            ``<config dir>/hivemind/remote``.
        """
        return cls(Path(typer.get_app_dir(APP_NAME)) / "remote")

    def load(self, name: str) -> RemoteProfile:
        """Read one profile.

        Args:
            name: The profile's name.

        Returns:
            The profile.

        Raises:
            LandingError: No such profile, or its file cannot be read as one.
        """
        path = self._path(check_profile_name(name))
        try:
            return RemoteProfile.model_validate_json(path.read_bytes())
        except FileNotFoundError as exc:
            raise LandingError(
                f"No remote profile {name!r}; enrol this device first (hive remote enrol ...)."
            ) from exc
        except ValidationError as exc:
            raise LandingError(f"The remote profile {name!r} at {path} is damaged.") from exc

    def names(self) -> tuple[str, ...]:
        """Every profile's name, sorted.

        Returns:
            The names; empty before the first enrolment.
        """
        folder = self.root / "profiles"
        if not folder.is_dir():
            return ()
        return tuple(sorted(entry.stem for entry in folder.glob(f"*{PROFILE_SUFFIX}")))

    async def save(self, profile: RemoteProfile, signer: Ed25519Signer) -> None:
        """Store the device key, then the profile that names it.

        Args:
            profile: The new profile.
            signer: The device's key; into the secret store only.
        """
        # Latency: two small local writes, each atomic.
        await self._keys.put(profile.name + KEY_SUFFIX, signer.private_key_bytes)
        _write_atomically(self._path(profile.name), profile.model_dump_json(indent=2))

    def update(self, profile: RemoteProfile) -> None:
        """Rewrite an existing profile (a new address, a device id); its key stays as it is.

        Args:
            profile: The profile, changed.

        Raises:
            LandingError: No such profile to update.
        """
        if profile.name not in self.names():
            raise LandingError(f"No remote profile {profile.name!r} to update.")
        _write_atomically(self._path(profile.name), profile.model_dump_json(indent=2))

    def save_certificate(self, name: str, pem: bytes) -> Path:
        """Keep a profile's client certificate beside it (public; its key stays in the store).

        Args:
            name: The profile's name.
            pem: The certificate, PEM, already checked against the profile's key.

        Returns:
            Where it was written.
        """
        path = self._certificate_path(check_profile_name(name))
        _write_atomically(path, pem.decode("ascii"))
        return path

    def certificate(self, name: str) -> bytes | None:
        """Read a profile's client certificate.

        Args:
            name: The profile's name.

        Returns:
            The certificate, PEM; None when the profile holds none.
        """
        try:
            return self._certificate_path(check_profile_name(name)).read_bytes()
        except FileNotFoundError:
            return None

    async def signer(self, profile: RemoteProfile) -> Ed25519Signer:
        """Read a profile's device key.

        Args:
            profile: The profile.

        Returns:
            The key.

        Raises:
            LandingError: The key is missing or damaged.
        """
        stored = await self._keys.get(profile.name + KEY_SUFFIX)
        try:
            return Ed25519Signer(stored or b"")
        except ValueError as exc:
            raise LandingError(
                f"The device key of profile {profile.name!r} is missing or damaged; forget the "
                "profile and enrol again."
            ) from exc

    async def forget(self, name: str) -> bool:
        """Remove a profile and then its key.

        Args:
            name: The profile's name.

        Returns:
            Whether there was a profile to remove.
        """
        path = self._path(check_profile_name(name))
        existed = path.exists()
        path.unlink(missing_ok=True)
        self._certificate_path(name).unlink(missing_ok=True)
        await self._keys.delete(name + KEY_SUFFIX)
        return existed

    def _path(self, name: str) -> Path:
        """Where the profile ``name`` is kept."""
        return self.root / "profiles" / f"{name}{PROFILE_SUFFIX}"

    def _certificate_path(self, name: str) -> Path:
        """Where the profile ``name``'s client certificate is kept."""
        return self.root / "profiles" / f"{name}{CERTIFICATE_SUFFIX}"


def check_profile_name(name: str) -> str:
    """Refuse a profile name that is not short, lower case and file-safe.

    Args:
        name: What the user typed.

    Returns:
        ``name``, unchanged.

    Raises:
        LandingError: It is not.
    """
    if PROFILE_NAME.fullmatch(name) is None:
        raise LandingError(
            f"{name!r} is not a profile name: up to 32 of a-z, 0-9, - and _, starting with a "
            "letter or digit."
        )
    return name


def _write_atomically(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` owner-only, so a reader sees the old file or the new, whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temporary, FILE_MODE)
    os.replace(temporary, path)
