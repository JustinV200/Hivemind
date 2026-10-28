# ADR-0067: The Pollen Packet carries a single-use tailnet key and a single-use invite

- Status: Proposed
- Date: 2026-10-28

## Context

Enrolling a device (ADR-0051) assumed the device could already reach the Hive Stand's swarm
listener. In practice the Hive Stand sits behind a home router and the devices worth borrowing
are laptops and boards that move between networks, so the operator had to put each device on the
Hive's private network by hand, then copy a binary over, then carry an invite across, then run
three `pollen` commands. `docs/runbooks/remote-access.md` already recommends a Tailscale overlay
(`vpn` exposure) for the Entrance, and the swarm listener can bind the overlay address the same
way. What was missing was one thing an operator can hand a new device that does all of it, with no
step that needs the operator's own Tailscale login on that device.

## Decision

**`hive swarm packet` writes one folder that enrols a device from nothing.** It holds the cached
pollen build for the platform, an installer script (`pollinate.ps1` on Windows, `pollinate.sh`
elsewhere), a one-screen README and two secrets in an owner-only `secrets/` folder: a tailnet auth
key and a swarm invite.

**The tailnet key is minted per packet, through an OAuth client, with fixed terms.** The Hive Stand
holds a Tailscale OAuth client with the `auth_keys` write scope, read from the source
`[swarm.packet] tailscale_oauth` names (`credman:<target>` for Windows Credential Manager,
`env:<prefix>` for two environment variables; never the secret itself). Each packet gets a fresh
key that is single use (it admits one device), preauthorized (no admin click), not ephemeral (the
device stays in the tailnet while offline, so it reconnects by itself from any network) and tagged
`tag:hive-device`. The tailnet policy grants that tag the Hive Stand's swarm and Entrance ports and
nothing else, so a packet's device can reach the Hive and no other machine in the tailnet. The key
lapses with the invite (`[swarm] invite_ttl_minutes`).

**The invite is the same invite.** It is minted through the enrolment door exactly as `hive swarm
invite` mints one, so its token, its pins and its trail event are those of ADR-0051 and the
enrolment ceremony does not change. The tailnet key gets a device onto the network; only the invite
gets it into the Hive.

**Nothing minted outlives a failed build.** The key is minted first, because the API call is the
likeliest step to fail and a refusal there has minted nothing. If the invite or the folder then
fails, the invite is revoked, the key deleted and the folder removed before the error is shown. A
built packet is recorded as `swarm.packet_built` with the key's id, the invite's hash, the platform
and a file count; the secrets are never printed, logged or recorded.

**The installer spends the secrets and deletes them.** It installs Tailscale if it is missing
(winget, else the official MSI checked against its published SHA-256 and its Authenticode
signature), brings the device up with the key read from its file (`--auth-key=file:`, never on a
command line), waits until the Hive Stand's advertised host resolves and answers, installs pollen
for the user, enrols with the invite on standard input, sets pollen to start at login and prints
its status. It is idempotent, and it deletes `secrets/` when it ends, whether it worked or not.

## Consequences

Positive: one folder and one command take a device from nothing to an enrolled Real Cell that
reconnects by itself, and the operator never signs in to Tailscale on it. The device's reach is
bounded by the tailnet policy, not by trusting the device: a tagged node reaches the Hive Stand's
two ports and nothing else, and revoking it in the Hive (`hive swarm revoke`) and in the Tailscale
admin console are two independent off switches. Both secrets are single use and short-lived, so a
packet lost after use or after its window is worthless.

Negative: a packet is a bearer credential until it is used or lapses: whoever holds it can join
the tailnet as a `hive-device` node and enrol a device up to the access cap. It must be carried by
a channel the operator trusts, like the invite string alone. The OAuth client is a long-lived
secret on the Hive Stand that can mint such keys at will; the tag the client may assign and the
policy's grant bound what a key can reach, and the client can be revoked in the admin console. A
failed run spends the packet, so the operator builds another. The Hive does not build pollen: the
binary must be cached per platform (`[swarm.packet] pollen_dir`) or passed with `--pollen`,
because PyInstaller cannot cross-compile and the CLI may not run a subprocess. The swarm listener
must keep a self-signed certificate rather than `tailscale cert`'s: invites pin the certificate,
and a Let's Encrypt certificate renewed every few months would break every device's pin.

## Alternatives considered

A reusable auth key baked into every packet: one leak would admit any number of devices until it
is noticed. An ephemeral key: the device would leave the tailnet whenever it went offline and could
not reconnect without a new key. The operator signing the device in to Tailscale by hand: the step
the packet exists to remove, and it makes the device a member's, not a tagged node the policy can
confine. Exposing the swarm listener publicly instead of over the overlay: ADR-0041 rules out a
public mode. Minting the invite first and the key second: a refused key would leave a live invite
behind, which then has to be revoked, and a revocation can itself fail.
