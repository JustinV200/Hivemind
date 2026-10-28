# ADR-0068: Loopback-only rows need a per-boot stand key, because a Virtual Cell's guest can
# reach loopback

- Status: Accepted
- Date: 2026-10-28
- Amends: ADR-0041 ("Exposure never means the open internet": the loopback listener's checks)

## Context

ADR-0041 puts every route that admits or empowers a device (invites, approval, denial, unlock,
capability widening, revocation, certificate re-issue, reopening, operator add, Supersedure) on
the loopback listener alone, on the premise that only software on the Hive Stand can reach
`127.0.0.1`. The listener refuses a foreign `Host` and any forwarding header, which stops proxies
and DNS rebinding, but it cannot see who is really on the other end of the socket.

Virtual Cells break the premise. QEMU's user networking (SLIRP) without `restrict=on`, which every
policy but `NONE` uses because the Cell needs outbound NAT, maps the guest's host alias `10.0.2.2`
(and every other unclaimed address in `10.0.2.0/24`) to the host's `127.0.0.1`; QEMU opens the
host socket itself, so the Entrance sees a peer of `127.0.0.1`. Docker Desktop on Windows and macOS
proxies `host.docker.internal`, which every Docker Cell gets as an `/etc/hosts` entry, to services
on the host's loopback, again from a host process. In both cases the guest writes its own `Host:
localhost:<port>`, so the request is indistinguishable from the operator's console. An untrusted
bee, or a prompt-injected Worker with `exec` in a Virtual Cell, is exactly the actor the
loopback-only rows exist to keep out. The guard's state floor refuses a bee's `net` to the
host-gateway names, but that checks tool calls, not packets, and arbitrary code in a guest is not
a tool call.

What still stood: every loopback-only row needs a session holding `entrance:steward`, which in
practice means the console device (its Ed25519 key wrapped under the operator password in the
host's secret store, which no Cell mounts) plus the operator password, or a steward device's key
plus the password. A demonstration against `main` showed a guest-shaped request passing the
loopback check (`401` from the session gate rather than the loopback `403`) and, with both
factors, minting an invite (`201`). So the hole was a lost layer of defence, not an open door; it
also let a guest flood the `127.0.0.1` rate bucket the operator's console shares, and exposed the
loopback-only `GET /v1/hive/supersede/{node_id}/preflight` and every public route's parser to it.

Fixing it in the network alone is not possible today. `restrict=on` also cuts the outbound NAT that
`EGRESS_ONLY`, `ALLOWLIST` and `VPN_TOR` Cells need, and QEMU's `-netdev user` documents no option
to keep NAT but drop the host-loopback mapping (libslirp has `disable_host_loopback`, but QEMU does
not pass it through). The Cell legitimately dials the host's loopback for its Waggle link (a
loopback `listen_host` is rewritten to `10.0.2.2` or `host.docker.internal`) and for any model
server bound to loopback (`rewrite_loopback_base_url`). Docker Desktop's host gateway cannot be
scoped to one port either. And no peer-address check helps, because both forwarders connect from
`127.0.0.1`.

## Decision

**The loopback-only rows need a per-boot stand key that never enters a Cell.** Each start of
the Entrance's listeners (`EntranceListeners.__init__`) draws a 256-bit key
(`hivemind.entrance.expose.mint_stand_key`), kept in memory and in the serve record while the
serve runs; the record is removed on exit. The loopback application's `LoopbackGate`, after its
`Host` and forwarding-header checks and before the rate limit or any route, refuses with a bare
`403` every request whose method and path match a loopback-only row (compiled with Starlette's own
path compiler from the same route table) unless it carries the key, compared in constant time, in
the `X-Hive-Stand` header. That header is the only channel: no cookie carries the key, because a
browser sends a `localhost` cookie to every port on the machine (cookies are not port-scoped), so
any local dev server or model web UI would receive and could log it. Rows served on both listeners
need no stand key on loopback, so a program on the Hive Stand that only submits, answers or
observes is unchanged.

**Only the Hive Stand holds it.** `hive serve` writes it into its serve record
(`<db>.serve.json`, beside the database, created `0600` on POSIX), which the console commands
already read to find the listener; they now send it on every request. The guard's state floor
names the serve record with the database, so a bee on the Hive Stand is refused `fs:read` of it as
it is of the database. No Virtual Cell backend mounts the Hive's state directory.

**The Hive Stand's browser takes it from a printed link's fragment.** `hive serve` prints
`http://localhost:<port>/stand#key=<key>`. The fragment never leaves the browser, so no server
(the Entrance included) sees the key in a request line or a log. `/stand` serves the Observation
Hive's page, as `/enrol` does; before the app renders, it keeps a well-formed key in the tab's
`sessionStorage` (scoped to this origin, scheme, host and port, and to this tab) and replaces the
address with `/`. Its one HTTP wrapper then sends the key as `X-Hive-Stand` on every request, and
a bare `403` (only the loopback gate answers one) is shown as "Open the Hive Stand browser link
`hive serve` printed (it changes every serve), on localhost." The key changes with every serve, so
the link is opened again after each start, and in each new tab.

**The stand key's holder gets its own rate bucket.** A request carrying the key is counted under
`stand:<address>`, so a guest flooding `127.0.0.1` can no longer starve the console of the door it
administers the Hive through.

**Loopback logins that stand for the Hive Stand need the key too.** The gate marks the request
scope when the key came along, and the arrival carries that mark (`Arrival.stand`). The console
device is `loopback_bound` (ADR-0041); without the key, its login on loopback is refused as if it
had come from the remote listener. While the Entrance is reduced, every login without the key is
refused, loopback included, so a reduction leaves only the Hive Stand able to log in.

The network stays as it is, documented plainly in the backends: QEMU's non-`NONE` policies and
Docker Desktop still let a guest reach the host's loopback services, the Entrance's public and
both-listener routes included. A guest there is like a remote client with one factor fewer to
pass: it skips the network factor a remote device faces (the VPN overlay, or mutual TLS for `lan`
and `tunnel`), and it reaches the loopback listener even while the remote one is reduced. It
still needs an enrolled, approved device key and the operator's password to open any session, and
since this decision it cannot open the console's, nor any session while reduced, nor reach a
loopback-only row.

## Consequences

Positive: a Virtual Cell's guest cannot reach a loopback-only row even holding the console's key
and the operator's password, without a change to any Cell's network or a guest-side firewall it
could undo. The protection is the same on every platform and backend, and needs no knowledge of
which forwarder a guest used. The refusal happens before the rate limit, so a guest's attempts
cost the operator nothing.

Negative: a third credential exists, and a program on the Hive Stand that calls a loopback-only
row directly (rather than through `hive entrance`) must read the serve record for it. The browser
on the Hive Stand opens the printed link after every serve start and in every new tab before it
can approve. Where the key can still be read, until the serve stops:

- `hive serve`'s output and whatever logs it, so whoever reads that terminal or log; that is the
  operator, or a process running as the Hive's own operating-system user, which ADR-0041 already
  records as a risk. On Windows the serve record's access is the folder's own access list, not
  `0600`.
- The browser's own history may keep the link with its fragment, since it records the address
  before the app replaces it; the browser profile is the operator's.
- Script running in the Entrance's own origin could read `sessionStorage`; the Entrance serves only
  its own build, under `default-src 'self'` (ADR-0041), so that needs a compromised build. Pages on
  other `localhost` ports cannot read it, and no request to them carries it.

While the Entrance is reduced, a program on the Hive Stand that does not send the stand key
cannot log in either, though it could before; it reads the key from the serve record like the
console does, or waits for the operator to reopen.

A guest that holds an ordinary device's key and the operator's password can log in on loopback
(while the Entrance is open) without the overlay or mutual TLS a remote copy of that device would
need, and use that device's both-listener routes. Its rights are the device's own, never the
console's or a loopback-only row's.

Host-loopback services other than the Entrance (Ollama, LM Studio, llama-server or any model server
bound to `127.0.0.1`, a dev server, anything else the operator runs) remain reachable from QEMU
guests under every policy but `NONE` and from Docker Desktop guests under every policy, as before.
This ADR does not close that; see the follow-up below.

## Follow-up (open)

Close host-loopback reach in the network, tracked in `.claude/roadmap.md` (open decision 17 and
the risk table): for QEMU, the control-NIC split the backend's module docstring describes (a
control NIC with `restrict=on` and one `guestfwd` per host service a Cell needs: its Waggle link
and each loopback model server, plus an egress NIC for NAT); for Docker Desktop, either the
control subnet everywhere with no `host.docker.internal` alias, or a documented refusal to run
untrusted work on Desktop. Both need a real QEMU and Docker Desktop host to verify.

## Alternatives considered

SLIRP `restrict=on` with explicit `guestfwd` rules for every policy: cuts the outbound NAT that
`EGRESS_ONLY`, `ALLOWLIST` and `VPN_TOR` exist for; recorded as the direction for a later control
NIC split (the QEMU backend's module docstring), not a fix for today. Dropping Docker's
host-gateway alias: breaks the control link on Docker Desktop without a control subnet. A peer
address or peer process check: both forwarders connect from `127.0.0.1`, and a process denylist
(`qemu-system-*`, Docker Desktop's backend) is platform-specific, incomplete and unverifiable here.
Binding the loopback listener to another loopback address, IPv6 or a Unix socket: SLIRP maps IPv6
`fec0::2` to `::1`, a browser reaches the listener only as `localhost`, and WebAuthn needs that
name. Requiring the stand key on every loopback request: breaks every program on the Hive Stand
that uses only both-listener routes, for no gain, since those routes are already remote-reachable.
An HttpOnly, SameSite=Strict cookie set by `/stand?key=`: needs no client change, but cookies are
not port-scoped, so every other server on `localhost` would receive the key on every request the
browser makes to it.
