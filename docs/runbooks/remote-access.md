# Remote access: reaching the Hive from your phone or another machine

The Hive Entrance is the Hive's one door. It always listens on loopback on the Hive Stand, and that
is where every approval happens. To reach the Hive from anywhere else you add a second, remote
listener, and the recommended way is over a VPN overlay. There is no public mode, on purpose. This
page walks through exposing the Entrance, enrolling a phone or a program, dealing with a lost
device, and shutting the door fast with the Entrance Reducer. The design is ADR-0041, and
`docs/entrance/landing-board.md` is the guide for writing a client.

## 1. Pick how it is exposed

`[entrance] expose` takes one of four values:

| `expose` | Remote listener | Needs |
|---|---|---|
| `loopback` (default) | none | nothing |
| `vpn` (recommended) | on your overlay address, Tailscale's by default | TLS on a DNS name |
| `lan` | on a private address on your home network | TLS and mutual TLS |
| `tunnel` | on loopback, reached through a tunnel client the Hive supervises | TLS and mutual TLS |

Every remote mode needs TLS on a DNS name, because browsers only make passkeys in a secure context.
The rest of this page uses `vpn` with Tailscale, since it needs no certificates on your devices and
a phone can enrol by scanning a QR code.

## 2. Set up the VPN

1. Install Tailscale on the Hive Stand and on your phone, and sign both into the same tailnet.
2. Turn on MagicDNS and HTTPS certificates for the tailnet in Tailscale's admin console.
3. On the Hive Stand, get a certificate for its MagicDNS name. The name looks like
   `hivestand.example-tailnet.ts.net`, and `tailscale status` shows it.

   ```
   tailscale cert hivestand.example-tailnet.ts.net
   ```

   This writes a `.crt` and a `.key` into the current folder. Move them somewhere beside your
   manifest, like `tls/`.
4. Note the Hive Stand's Tailscale address with `tailscale ip -4`. It starts with `100.`.

Plain WireGuard works too. Set `vpn_interface` to its interface and `vpn_cidrs` to its subnet.

## 3. Configure the Entrance

Add this to `hive.toml`, with your own name and address:

```toml
[entrance]
expose = "vpn"
remote_bind = "100.101.102.103:8711"
public_url = "https://hivestand.example-tailnet.ts.net:8711"

[entrance.tls]
cert = "tls/hivestand.example-tailnet.ts.net.crt"
key = "tls/hivestand.example-tailnet.ts.net.key"
```

`remote_bind` has to be an address on the overlay's own interface, not a wildcard. The loopback
listener stays on `bind`, `127.0.0.1:8710` by default. `docs/manifests/full.toml` documents the rest
of the section, like session lengths, lockout and rate limits.

Check it before you start anything:

```
hive entrance expose --manifest hive.toml
```

It runs the same check `hive serve` runs at start, without binding anything or asking for a
password, and prints the plan or the rule your setup breaks.

## 4. Build the Observation Hive

The Entrance serves the Observation Hive, the web app you use from the phone, from
`packages/observation-web/dist`, resolved against the manifest's folder. Build it once with Node and
pnpm:

```
cd packages/observation-web
pnpm install
pnpm build
```

Without a build the Entrance still runs and every API route works, there is just no app to load.

## 5. Start the Hive

```
hive serve --manifest hive.toml
```

`hive serve` runs the Queen, her Warden on the Hive Stand and the Entrance in one process, and
prints where the loopback listener answers, plus a **Hive Stand browser link**
(`http://localhost:<port>/stand#key=...`). The approval routes on loopback need that boot's stand
key (ADR-0068), because a Virtual Cell can reach the Hive Stand's loopback too. The `hive entrance`
commands read it on their own. To approve devices from a browser on the Hive Stand, open the link
in it after each start of `hive serve`. The Observation Hive keeps the key for that tab only, so a
new tab needs the link again. If an approval says to open the Hive Stand browser link, that is
why.

Treat the link like a password until the serve stops. The key sits in the fragment, so no server
ever receives it, and the app never puts it in a cookie, which would reach every other server on
`localhost`. It can still be read from `hive serve`'s output or log, from the browser's history
(which may record the link before the app clears it), and by your own operating-system account. The remote listener starts once the exposure check
passes. If it does not, `hive serve` exits 2 naming the rule.

You need an operator password. `hive init` sets one, and `hive entrance operator password` sets or
changes it otherwise.

## 6. Enrol a phone

On the Hive Stand, with `hive serve` running:

```
hive entrance invite --device phone --manifest hive.toml
```

It prints the invite once: a code grouped for reading aloud, the enrolment link and a QR code of the
link. An invite works once and lapses after `[entrance] invite_ttl_minutes`, 15 by default.

1. Scan the QR code with the phone. The link opens the Observation Hive's join screen at your
   `public_url`. If the Android app is installed it can open there instead, and its invite box takes
   the link pasted by hand.
2. The phone makes a passkey and shows its fingerprint.
3. Back on the Hive Stand, list what is waiting and approve the phone:

   ```
   hive entrance pending --manifest hive.toml
   hive entrance approve <device id> --spend-cap 5 --manifest hive.toml
   ```

   `approve` shows the device's key fingerprint and what it says about itself, then asks for a yes.
   Check the fingerprint against the phone's before you say yes. `--spend-cap` is the most it may
   spend a day, in US dollars. `--capabilities` narrows what it may do, and `--expires 90d` makes the
   approval lapse.
4. Log in on the phone with the passkey plus the operator password.

From there the phone can submit goals, by typing or speaking, follow them live and answer the
inbox. A goal over `[entrance] step_up_spend` asks for the password again.

## 7. Enrol a laptop or a program

A laptop with HiveMind installed enrols with the `hive remote enrol` line the invite prints, which
looks like this:

```
hive remote enrol <invite link> --name laptop --hive <hive id>
```

It keeps an Ed25519 key in the laptop's own secret store and a profile for the Hive. Approve it on
the Hive Stand the same way as the phone. Then `hive run --remote "goal"` and `hive inbox --remote`
work from the laptop. Any other program enrols with its own Ed25519 key over the Landing Board,
section 4 of `docs/entrance/landing-board.md`.

A device that can reach no listener at all can be registered by hand on the Hive Stand with `hive
entrance register`, using its public key copied across.

## 8. A lost device

- From another approved device, lock it from the Observation Hive's device list. Locking only
  narrows, so it works from either listener after the password. A locked device cannot log in until
  it is unlocked on loopback.
- On the Hive Stand, revoke it for good:

  ```
  hive entrance revoke <device id> --cancel-goals --manifest hive.toml
  ```

  Its sessions end at once and its goals not yet planned are refused. `--cancel-goals` cancels the
  ones still open in the same step, and without it the command names what was left running. A
  revoked device needs a new invite to come back.

`hive entrance devices` lists every device with its status, key fingerprint and daily cap. Every
change to a device is recorded on the trail and pushed to your other devices.

## 9. Shut the door fast

```
hive entrance reduce --manifest hive.toml
```

The Entrance Reducer stops the remote listener and any tunnel child, ends every remote session and
closes their streams within about a second. The loopback listener stays up. The mode survives a
restart. The Guard Bee reduces by itself when it sees something wrong, and so does a remote listener
that fails, which also raises an Alarm.

Reopen it on the Hive Stand once you know why it closed:

```
hive entrance open --manifest hive.toml
hive entrance status --manifest hive.toml
```

`open` asks for the password again. `status` shows the mode, the listeners, the devices by status
and anything waiting on you.

## LAN and tunnel

Both need mutual TLS, and the Hive runs its own certificate authority for it. A device gets its
client certificate when it is approved. A program gets one signed from its certificate request, and
a browser gets a PKCS#12 bundle written to `--bundle-out`, with a passphrase shown once to type on
the device. `hive entrance reissue` gives an approved device a new one. For `tunnel`, put the client
command in `[entrance] tunnel_command` and any token it needs in `HIVEMIND_ENTRANCE_TUNNEL_<NAME>`.
Section 12 of the client guide has the details.

## Never forward TCP into loopback

The loopback listener is where approval lives. It refuses requests that carry proxy headers, but a
bare TCP forwarder is invisible to it, so never put `tailscale serve`, a reverse proxy, an SSH
tunnel or a port forward in front of it. Remote access goes through the remote listener only.

Virtual Cells are such forwarders whether you like it or not: QEMU's user networking and Docker
Desktop's `host.docker.internal` both let a guest reach the host's `127.0.0.1`. That is why the
approval routes also need the stand key, which no Cell is ever given. Anything else you run on the
Hive Stand's loopback (a model server, a dev server) is reachable from those Cells too.
