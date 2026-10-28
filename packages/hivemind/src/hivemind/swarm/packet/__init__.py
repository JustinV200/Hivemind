"""Re-export the packet package: build a self-contained Pollen Packet for one new device.

A Pollen Packet, in the sense of `hive swarm packet`, is one folder that takes a new machine from
nothing to an enrolled Real Cell (a device the Hive borrows) on the Hive's private network: the
pollen build for its platform, an installer script, a README and two single-use secrets, a tailnet
key (Tailscale's auth key, so the device joins the overlay network the Hive Stand listens on) and
an invite (so it enrols). `credentials` and `credman` read the Tailscale OAuth client the key is
minted with; `tailnet` is the minting seam, `tailscale` its real implementation and `fake` its
test double; `platforms` names the builds; `acl` makes the secrets owner-only; `writer` lays the
folder out from `templates`; `builder` orders the whole thing so a failure leaves nothing live.
This file is the package's face (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Used by
    `hive swarm packet` (Layer 7). Calls into the rest of `hivemind.swarm`, `hivemind.cell`,
    `hivemind.pheromone`, `httpx` and the standard library.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - A packet's secrets exist only in its owner-only `secrets/` folder; nothing here prints,
      logs or records one.

See Also:
    - docs/adr/0067-the-pollen-packet-carries-a-tailnet-key-and-an-invite.md for the decision.
    - docs/runbooks/enrolling-a-device.md, "Pollen Packet over Tailscale".

Public API:
    - build_packet, PacketDeps, PacketOrder, BuiltPacket: build one packet (builder).
    - TailnetKeys, TailnetKey, HIVE_DEVICE_TAG: the tailnet key seam (tailnet).
    - TailscaleApiKeys, TAILSCALE_API: the real minter over Tailscale's API (tailscale).
    - FakeTailnetKeys: the in-memory minter (fake).
    - SecretSource, OAuthClient, EnvSecretSource, CredmanSecretSource, parse_secret_source:
      where the OAuth client is read from (credentials).
    - PacketPlatform, find_pollen_build: the platforms and their cached builds (platforms).
    - HiveTarget, write_packet and the file names: the folder's layout (writer).
    - is_owner_only: whether a path is readable by its owner only (acl).
"""

from hivemind.swarm.packet.acl import is_owner_only
from hivemind.swarm.packet.builder import BuiltPacket, PacketDeps, PacketOrder, build_packet
from hivemind.swarm.packet.credentials import (
    CredmanSecretSource,
    EnvSecretSource,
    OAuthClient,
    SecretSource,
    parse_secret_source,
)
from hivemind.swarm.packet.fake import FakeTailnetKeys
from hivemind.swarm.packet.platforms import PacketPlatform, find_pollen_build
from hivemind.swarm.packet.tailnet import HIVE_DEVICE_TAG, TailnetKey, TailnetKeys
from hivemind.swarm.packet.tailscale import TAILSCALE_API, TailscaleApiKeys
from hivemind.swarm.packet.writer import (
    AUTH_KEY_FILE,
    INVITE_FILE,
    README_FILE,
    SECRETS_DIR,
    HiveTarget,
    write_packet,
)

__all__ = [
    "AUTH_KEY_FILE",
    "HIVE_DEVICE_TAG",
    "INVITE_FILE",
    "README_FILE",
    "SECRETS_DIR",
    "TAILSCALE_API",
    "BuiltPacket",
    "CredmanSecretSource",
    "EnvSecretSource",
    "FakeTailnetKeys",
    "HiveTarget",
    "OAuthClient",
    "PacketDeps",
    "PacketOrder",
    "PacketPlatform",
    "SecretSource",
    "TailnetKey",
    "TailnetKeys",
    "TailscaleApiKeys",
    "build_packet",
    "find_pollen_build",
    "is_owner_only",
    "parse_secret_source",
    "write_packet",
]
