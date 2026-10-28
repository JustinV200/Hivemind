"""Read the Tailscale OAuth client that mints a packet's tailnet key, from where the manifest says.

`hive swarm packet` mints a single-use Tailscale auth key for every packet, and Tailscale only
issues one to an OAuth client (a client id and a client secret with the `auth_keys` write scope).
The client is a secret, so `[swarm.packet] tailscale_oauth` names only where it lives: a source
string `credman:<target>` (a generic credential in Windows Credential Manager, whose user name is
the client id and whose password is the client secret) or `env:<prefix>` (the `<prefix>_CLIENT_ID`
and `<prefix>_CLIENT_SECRET` environment variables). `SecretSource` is the small seam both sit
behind, and `parse_secret_source` turns the manifest's string into one. The environment is handed in
as a mapping by the composition root, as `hivemind.manifest.env.provider_api_key` is for a model
provider's key, so nothing here reads `os.environ` (codingrules section 13).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Called by `hive swarm packet` (Layer 7) when it composes the Tailscale key minter; read by
    `hivemind.swarm.packet.tailscale`. Calls into `hivemind.swarm.packet.credman` and pydantic.

Key invariants:
    - The client secret is held in a `SecretStr` from the moment it is read, so no `repr`, log
      line or error ever shows it; errors name the source, never a value.
    - A source string that is empty or of an unknown kind is refused before anything is minted.

See Also:
    - docs/adr/0067-the-pollen-packet-carries-a-tailnet-key-and-an-invite.md for why a packet
      mints its own tailnet key.
    - hivemind.manifest.schema.swarm.SwarmPacketSection for the `tailscale_oauth` key.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet.credman import read_generic_credential

ENV_KIND = "env"  # `env:<prefix>`: two environment variables named from the prefix.
CREDMAN_KIND = "credman"  # `credman:<target>`: a Windows Credential Manager generic credential.

__all__ = [
    "CREDMAN_KIND",
    "ENV_KIND",
    "CredmanSecretSource",
    "EnvSecretSource",
    "OAuthClient",
    "SecretSource",
    "parse_secret_source",
]


class OAuthClient(BaseModel):
    """A Tailscale OAuth client: what the client-credentials token request sends."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str = Field(min_length=1, description="The OAuth client's id; not secret.")
    client_secret: SecretStr = Field(description="The OAuth client's secret; never shown.")


class SecretSource(Protocol):
    """Somewhere a Tailscale OAuth client can be read from."""

    def read(self) -> OAuthClient:
        """Return the OAuth client this source holds.

        Returns:
            The client id and secret.

        Raises:
            PacketError: The source holds no client, or cannot be read on this machine.
        """
        ...


@dataclass(frozen=True, slots=True)
class EnvSecretSource:
    """`env:<prefix>`: the client in `<prefix>_CLIENT_ID` and `<prefix>_CLIENT_SECRET`."""

    prefix: str  # The variable names' common prefix, e.g. `TS_OAUTH`.
    environ: Mapping[str, str]  # The environment, handed in by the composition root.

    def read(self) -> OAuthClient:
        """Return the client the two variables hold.

        Returns:
            The client id and secret.

        Raises:
            PacketError: Either variable is unset or empty.
        """
        id_name, secret_name = f"{self.prefix}_CLIENT_ID", f"{self.prefix}_CLIENT_SECRET"
        client_id, secret = self.environ.get(id_name, ""), self.environ.get(secret_name, "")
        # Both halves or nothing: the names go in the message, the values never do.
        if not client_id or not secret:
            raise PacketError(
                f"Set both {id_name} and {secret_name} to the Tailscale OAuth client's id and "
                "secret, or point [swarm.packet] tailscale_oauth at another source."
            )
        return OAuthClient(client_id=client_id, client_secret=SecretStr(secret))


@dataclass(frozen=True, slots=True)
class CredmanSecretSource:
    """`credman:<target>`: a generic credential in Windows Credential Manager."""

    target: str  # The credential's target name, as `cmdkey /generic:<target>` stored it.

    def read(self) -> OAuthClient:
        """Return the client the credential holds: its user name and its password.

        Returns:
            The client id and secret.

        Raises:
            PacketError: Not on Windows, or no such credential, or one with an empty half.
        """
        client_id, secret = read_generic_credential(self.target)
        if not client_id or not secret:
            raise PacketError(
                f"The Credential Manager entry {self.target!r} needs the client id as its user "
                "name and the client secret as its password."
            )
        return OAuthClient(client_id=client_id, client_secret=SecretStr(secret))


def parse_secret_source(spec: str, environ: Mapping[str, str]) -> SecretSource:
    """Turn `[swarm.packet] tailscale_oauth` into the source it names.

    Args:
        spec: `credman:<target>` or `env:<prefix>`; the schema has already checked its shape.
        environ: The environment an `env:` source reads; the composition root's `os.environ`.

    Returns:
        The source. Nothing is read yet.

    Raises:
        PacketError: `spec` is empty or names a kind other than `credman` or `env`.

    Example:
        >>> parse_secret_source("env:TS_OAUTH", {}).prefix
        'TS_OAUTH'
    """
    kind, _, name = spec.partition(":")
    # The schema allows "" (not set up yet); building a packet is where that becomes a refusal.
    if kind == ENV_KIND and name:
        return EnvSecretSource(prefix=name, environ=environ)
    if kind == CREDMAN_KIND and name:
        return CredmanSecretSource(target=name)
    raise PacketError(
        "Set [swarm.packet] tailscale_oauth to `credman:<target>` or `env:<prefix>`, naming where "
        "the Tailscale OAuth client (auth_keys write scope) is kept."
    )
