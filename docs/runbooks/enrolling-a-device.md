# Enrolling a device

How to borrow a machine for the Swarm: put the Pollen Packet on it, enrol it with a single-use
invite, run a goal on it, change what the Hive may do there, and take it back. The ceremony itself
is specified in [`../waggle/enrolment.md`](../waggle/enrolment.md); this page is what the operator
types. The Hive borrows the device and leaves it as found: a lease's scratch directory is removed
when the lease ends, and revoking the device ends the Hive's reach.

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
the packet up to start at login (a systemd user unit on Linux). Where there is no user session
manager, such as a container or an SSH login without one, it says so and prints the command that
starts the packet by hand; run that, or run `pollen run` in a terminal to watch the packet work. On
a headless Pi, `sudo loginctl enable-linger $USER` keeps the unit running with nobody logged in.
`pollen status` says whether the packet is running and connected, and why not if it is not.

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
