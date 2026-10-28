# Enrolling a device

How to borrow a machine for the Swarm: put the Pollen Packet on it, enrol it with a single-use
invite, run a goal on it, change what the Hive may do there, and take it back. The ceremony itself
is specified in [`../waggle/enrolment.md`](../waggle/enrolment.md); this page is what the operator
types. The Hive borrows the device and leaves it as found: a lease's scratch directory is removed
when the lease ends, and revoking the device ends the Hive's reach.

For a device that is not on the Hive Stand's network yet, which is most of them, skip to [Pollen
Packet over Tailscale](#pollen-packet-over-tailscale): one folder and one script do sections 1 to 3
and put the device on the Hive's private network as well.

## Before you start

On the Hive Stand, `hive.toml` has `[swarm] enabled = true` and an `advertise_url` the device can
actually reach, such as `wss://hive.example.net:8720`. A listener that is not on loopback needs
`tls_cert` and `tls_key`; a self-signed certificate is enough, because the invite carries its
fingerprint and the device checks nothing else (`enrolment.md` section 2). Run the Hive with `hive
serve`: a device can enrol only into a running Hive, though minting the invite needs just the
manifest. Every `hive swarm` command below takes `--manifest hive.toml` before its name when the
manifest is not the file called `hive.toml` in the current directory.

## 1. Get the binary onto the device

`pollen` is one file with Python inside, so the device needs no Python and no pip. The Pollen
workflow (`.github/workflows/pollen.yml`) builds it for each platform on every `v*` tag and on
demand; open the run under Actions, Pollen, and download the artifact for the device:

| Device | Artifact |
|---|---|
| Raspberry Pi 3, 4, 5 or Zero 2 W on 64-bit Raspberry Pi OS (Debian 12 "bookworm" or newer; `uname -m` prints `aarch64`) | `pollen-linux-arm64` |
| Linux PC or server, x86-64 (Debian 12, Ubuntu 22.04 or newer) | `pollen-linux-x86_64` |
| Windows, 64-bit | `pollen-windows-x86_64` (the file is `pollen.exe`) |
| Mac with Apple silicon, or with an Intel chip | `pollen-macos-arm64`, `pollen-macos-x86_64` |

The Linux binaries are built in Debian 12 and run on it and on anything newer; a Debian 11 device
cannot run them, and neither can the 32-bit Raspberry Pi OS (`armv7l`), so reflash a Pi that runs
it with the 64-bit image. To put the binary on a Pi, from the machine that downloaded the artifact
(a zip does not keep the executable bit; `install -m 0755` sets it):

    unzip pollen-linux-arm64.zip
    scp pollen pi@raspberrypi.local:/tmp/pollen
    ssh pi@raspberrypi.local 'sudo install -m 0755 /tmp/pollen /usr/local/bin/pollen && pollen version'

`pollen version` prints `pollen 0.1.0.dev0 (waggle 1.13)`: the packet's version and the Waggle
protocol it speaks.

## 2. Mint an invite

On the Hive Stand:

    hive swarm invite --access scratch --shield meadow --label kitchen-pi

`--access` is the most the device may be granted (`read_only`, `scratch` or `full`; the device gets
the lower of that and what it asks for). `--shield` is the Comb Shield tier you vouch for: `meadow`,
or `propolis` if the device already runs a VPN that the Hive may verify. The string it prints,
`pollen1.` followed by letters, is the only copy of the invite and the only secret in the command's
output. It works once and expires after `invite_ttl_minutes` (15 by default), so mint it when the
device is in front of you and carry it there by a channel only you can read.

## 3. Enrol and start the packet

On the device:

    pollen enrol -          # paste the invite, then press Ctrl-D (Ctrl-Z, Enter on Windows)
    pollen install

Reading the invite from standard input (`-`) keeps it out of the process list and the shell
history. `pollen enrol` checks the invite, pins the Hive's key and certificate, makes the device's
identity and prints `Enrolled as device_... on Cell cell_..., access SCRATCH.` `pollen install` sets
the packet up to start at login (a systemd user unit on Linux, a Scheduled Task on Windows). Where
there is no user session manager, such as a container or an SSH login without one, it says so and
prints the command that starts the packet by hand; run that, or run `pollen run` in a terminal to
watch the packet work. On a headless Pi, `sudo loginctl enable-linger $USER` keeps the unit
running with nobody logged in.
`pollen status` says whether the packet is running and connected, and why not if it is not.

### No pop-ups

The packet works out of sight so it never interrupts whoever is using the device. On Windows the
Scheduled Task starts it under a console host with no window (`conhost.exe --headless`), so nothing
appears at login, and it writes its log to `packet.log` in the data directory
(`%LOCALAPPDATA%\pollen`) instead. `conhost.exe --headless` needs Windows 10 1809 or later. Every
console program a command runs gets a console with no window. On Linux and macOS a command's
program does not inherit `DISPLAY` or `WAYLAND_DISPLAY`, so it cannot draw on your screen by
accident (a Wayland program can still find the desktop's default socket through `XDG_RUNTIME_DIR`,
which programs need for more than a display). A colonized device's Warden gets them only when the
device manifest you recorded with `--warden-manifest` sets `[hive_stand] exoskeleton_real_display
= true`, which lends it your own screen; otherwise it never sees it either. Nothing is hidden
from you: every command is still on the Pheromone Trail, in the Observation Hive and in `pollen
status`. Two limits remain. A GUI program a command deliberately launches still appears; bees are
expected not to do that on a Real Cell. And a UAC prompt always shows, because Windows puts it on
a secure desktop nothing else can draw over.

## 4. See the device and run a goal on it

On the Hive Stand:

    hive swarm list
    hive cells list
    hive swarm run kitchen-pi "Write this machine's host name to a file, then read it back."

`hive swarm list` shows the device ONLINE once its first heartbeat arrives, with its level, access
and tier; `hive cells list` shows it as a REAL Cell from source `swarm`. NODE is the device id, the
Cell id or the label. A goal run this way can be placed only on that device's Cell, never on the
Hive Stand. `hive swarm run` builds a Hive of its own and starts the swarm listener, so stop `hive
serve` first (they would both want the port); the packet reconnects by itself.

## 5. Change what the Hive may do there

    hive swarm access kitchen-pi read_only
    hive swarm shield kitchen-pi propolis
    hive swarm promote kitchen-pi
    hive swarm demote kitchen-pi

`access` and `shield` change the device's limits; the device applies them from its next request.
`night_veil` is refused, because that tier exists only for Virtual Cells. `promote` and `demote`
record that you want the device one rung up or down the ladder (the packet alone, then the Warden on
the device, then a model server there); the Hive moves it a rung at a time when it can, and never
two rungs at once. To let a device be colonized, tell `pollen install` where the Hive's runtime
comes from, after enrolling: `--runtime-wheelhouse DIR` or `--runtime-index URL`, and optionally
`--warden-manifest FILE` for the device's local Warden manifest.

While the device has no task, its Warden watches it read-only (ADR-0054): the process list and
resource use always, and the log lines and file changes of the directories you name, none until you
do:

    hive swarm watch kitchen-pi /var/log/pihole /srv/data
    hive swarm watch kitchen-pi
    hive swarm watch kitchen-pi --clear

Each directory is absolute on the device's own system (`C:\Users\me\logs` on Windows), at most
sixteen are taken, and one inside another you named is dropped. The Hive refuses a directory the
device's granted capabilities do not let it read; every access level grants `fs:read:**`, so a
device enrolled today accepts any. With no directory the command shows what is set, and `--clear`
stops the file watching. The running Hive applies the new list by itself: it rebuilds the device's
Warden on the Hive Stand, or, for a device whose Warden has moved onto it, tells the packet and
restarts that Warden under the new list, as for a new access level (a task running on it is paused
at a Handoff and resumes). The packet, and a Warden on the device, read only directories inside
the device's own `fs:read` grant and never follow a symbolic link out of one. A Warden on the
device looks as often, and keeps what it sees as long, as the `[supervision.watch]` table of its
own `warden.toml` says (`docs/manifests/device.toml`), and wakes on its fifteen-second heartbeat, so
that is the closest it can look. What the Warden sees is kept for a day, labelled personal, and the
Hive sends the device no command to see it; a Warden on the device keeps it on the device.

## 6. Take the device back

On the Hive Stand, `hive swarm revoke kitchen-pi` ends the enrolment for good: the running Hive
closes the link, stops the device's Warden, which releases any lease and leaves the device as it
found it, and forgets its key. The device's row stays, marked revoked, for the audit. On the
device, stop the packet (Ctrl-C if you ran it by hand), then `pollen uninstall` removes the unit
and stops the Warden the packet started, if one runs, and nothing else; delete the data directory
(`~/.local/share/pollen` on Linux) to forget the enrolment, and the binary (`sudo rm
/usr/local/bin/pollen`) to take the packet off the machine. Nothing else of the packet's is left:
the single-file build unpacks itself into a temporary directory and removes it when it ends.
Enrolling the same machine again takes a new invite.

## Pollen Packet over Tailscale

`hive swarm packet` writes one folder that takes a device from nothing to enrolled: the pollen
binary, an installer script, a README, a single-use Tailscale auth key and a single-use invite.
The script installs Tailscale if it is missing, joins the Hive's tailnet (its private Tailscale
network) with the key, checks the Hive Stand answers, installs pollen for the user, enrols, and sets
pollen to start at login. The device is a tagged tailnet node that reconnects by itself from any
network and can reach the Hive Stand and nothing else. ADR-0067 records why.

### One-time tailnet setup

In the Tailscale admin console:

1. Under DNS, turn on MagicDNS and HTTPS certificates. Devices dial the Hive Stand by its MagicDNS
   name (`hivestand.example-tailnet.ts.net`); the certificates are for the Entrance
   (`remote-access.md` section 2).
2. In the access controls (the tailnet policy file), add the two tags and one grant, and remove the
   default rule that lets everything reach everything (`"src": ["*"], "dst": ["*"]`), or a packet's
   device could reach every machine you own:

   ```json
   {
     "tagOwners": {
       "tag:hive-stand":  ["autogroup:admin"],
       "tag:hive-device": ["autogroup:admin"]
     },
     "grants": [
       {"src": ["tag:hive-device"],  "dst": ["tag:hive-stand"], "ip": ["tcp:8720"]},
       {"src": ["autogroup:member"], "dst": ["tag:hive-stand"], "ip": ["*"]}
     ]
   }
   ```

   `tag:hive-device` reaches only the Hive Stand, and there only the swarm listener's port (8720;
   use whatever `[swarm] listen_port` names). A borrowed device has no business with the Entrance,
   whose phones and laptops are your own signed-in devices. The second grant keeps your own
   signed-in devices able to reach the Hive Stand; keep whatever other rules you had for them.
   Nothing needs to reach a device: the packet dials out.
3. Tag the Hive Stand: on it, `tailscale up --advertise-tags=tag:hive-stand`, or edit its tags under
   Machines.
4. Under Settings, OAuth clients, generate a client with the Auth Keys write scope, allowed to
   assign `tag:hive-device`. Store it on the Hive Stand, never in the manifest. On Windows, in
   Credential Manager (the password prompt keeps it out of your shell history):

       cmdkey /generic:tailscale-oauth-hivemind /user:<client id> /pass

   Elsewhere, in two environment variables of the account that runs `hive`, for example
   `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_CLIENT_SECRET`.

### The Hive Stand's manifest

Bind the swarm listener on the Hive Stand's Tailscale address (`tailscale ip -4`) and advertise its
MagicDNS name:

```toml
[swarm]
enabled = true
listen_host = "100.101.102.103"
listen_port = 8720
advertise_url = "wss://hivestand.example-tailnet.ts.net:8720"
tls_cert = "tls/swarm.crt"
tls_key = "tls/swarm.key"
invite_ttl_minutes = 60.0              # a packet, and its key, lapse with the invite

[swarm.packet]
tailscale_oauth = "credman:tailscale-oauth-hivemind"   # or "env:TS_OAUTH"
pollen_dir = "dist/pollen"
```

Give the swarm listener a long-lived self-signed certificate, not the one `tailscale cert` makes:
every invite pins the certificate, and Tailscale's are renewed every few months, which would break
every enrolled device's pin. One way, from the manifest's folder:

    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
      -subj "/CN=hivestand.example-tailnet.ts.net" -keyout tls/swarm.key -out tls/swarm.crt

Tailscale must be up before `hive serve` starts, or there is no overlay address to bind. The
Entrance stays as `remote-access.md` sets it up (`expose = "vpn"` with `vpn_interface` and
`remote_bind` on the same overlay address, and the `tailscale cert` certificate).

### Cache the pollen builds

`hive swarm packet` copies the binary from `<pollen_dir>/<platform>/`; it does not build one,
because a build needs the platform itself. On a machine of that platform, from the repository:

    uv sync --frozen --group pollen-build
    uv run --frozen python scripts/build_pollen.py --output-dir dist/pollen/windows-x86_64

or unzip the Pollen workflow's `pollen-<platform>` artifact (section 1) into that folder. The
platforms are `windows-x86_64`, `linux-x86_64`, `linux-arm64`, `macos-arm64` and `macos-x86_64`.
`--pollen FILE` uses another binary for one packet.

### Build a packet and use it

On the Hive Stand:

    hive swarm --manifest hive.toml packet --label kitchen-pc --platform windows-x86_64 --access scratch

It prints the packet's folder (`./pollen-packet-kitchen-pc` unless `--out` names another) and, on
standard error, the tailnet key's id and when the packet lapses. It never prints a secret.
`--access` and `--shield` mean what they mean for `hive swarm invite`. If anything fails after the
key is minted, the key and the invite are revoked and the folder removed, so a failed build leaves
nothing live.

Build it on NTFS, ext4 or APFS, not straight onto a FAT32 or exFAT stick: `secrets/` is made
owner-only, and those file systems keep no permissions to make it so. Once built, the folder can be
copied to a stick.

Carry the folder to the device yourself (a USB stick), not by chat, email or a shared drive: until
it is used, its `secrets/` folder is a key to the Hive. On the device, open a terminal in the folder
as the account pollen should run under, and run what the README says:

    powershell -ExecutionPolicy Bypass -File .\pollinate.ps1      # Windows
    bash ./pollinate.sh                                            # Linux, macOS

Windows may ask to allow the Tailscale install, and Linux for your password. A Mac needs Tailscale
installed by hand first (the Tailscale app). Two things stop the script before it uses anything, so
the same packet works once they are fixed: a Mac without Tailscale, and a machine already signed in
to another Tailscale account (a personal one, say); run `tailscale logout` there and run the script
again. Otherwise the script can be run again safely, and it deletes `secrets/` when it ends,
whether it worked or not, so a run that failed needs a new packet. `hive swarm list` then shows the
device, as in section 4.

To take the device back, `hive swarm revoke` it (section 6) and remove it under Machines in the
Tailscale admin console; either one alone cuts the Hive off from it.

## A device that can run a desktop

A task that needs a display (it clicks, types or reads a screen) can be placed on a Linux device
that has `Xvfb`, `openbox`, `xdotool` and ImageMagick's `import` on its `PATH`; on Ubuntu that is
`sudo apt install xvfb openbox xdotool imagemagick`. The packet reports the device able to start a
display when it finds all four, and nothing runs at boot. The device's access level must be `full`
(`hive swarm access`): `scratch` allows a browser and no display, and the Hive does not place a
desktop task on such a device. The display belongs to one task. Whichever Warden supervises the
device (on the Hive Stand, or on the device once it is colonized) starts a private Xvfb and window
manager in the lease's scratch, drives them for the task and stops them when it ends, so the
device's own screen, if it has one, is never used and nothing is left running. A colonized device's
Warden keeps the task's flight recording in its memory for a day and writes none of its frames to
the device's disk. On a colonized device the browser fast path also needs the `browser` extra in
the runtime the Warden runs from, which `pollen install` does not add.

## When it does not work

- `pollen enrol` reports a pin mismatch: the listener's certificate is not the one in the invite.
  Check the address and the certificate; mint a new invite if the certificate changed.
- `hive.swarm.enrol_denied`: the token is unknown, expired or already used. Mint a new invite.
- The device is enrolled but never ONLINE: `pollen status` on the device shows the last error. A
  device clock more than five minutes ahead of the Hive's makes the Hive drop its frames.
- `hive swarm run` says the device did not connect: the packet is not running, or the Hive's
  listener is held by another `hive` process. Start the packet, stop the other process.

`enrolment.md` section 13 lists the rest, with what the Hive does in each case.
