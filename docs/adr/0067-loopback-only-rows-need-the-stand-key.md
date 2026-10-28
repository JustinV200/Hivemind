# ADR-0067: Loopback-only rows need a per-boot stand key, because a Virtual Cell's guest can reach loopback

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

Fixing it in the network alone is not possible today. `restrict=on` also cuts the outbound NAT
that `EGRESS_ONLY`, `ALLOWLIST` and `VPN_TOR` Cells need, and QEMU's `-netdev user` documents
no option to keep NAT but drop the host-loopback mapping (libslirp has `disable_host_loopback`,
but QEMU does not pass it through). The Cell legitimately dials the host's loopback for its Waggle link (a loopback
`listen_host` is rewritten to `10.0.2.2` or `host.docker.internal`) and for any model server bound
to loopback (`rewrite_loopback_base_url`). Docker Desktop's host gateway cannot be scoped to one
port either. And no peer-address check helps, because both forwarders connect from `127.0.0.1`.

## Decision

**The loopback-only rows need a per-boot stand key that never enters a Cell.** Each
`build_entrance` draws a 256-bit key (`hivemind.entrance.expose.mint_stand_key`), kept in memory
and never persisted beyond the running serve. The loopback application's `LoopbackGate`, after its
`Host` and forwarding-header checks and before the rate limit or any route, refuses with a bare
`403` every request whose method and path match a loopback-only row (compiled with Starlette's own
path compiler from the same route table) unless it carries the key, compared in constant time, in
the `X-Hive-Stand` header or the `hive_stand` cookie. Rows served on both listeners need no stand
key on loopback, so a program on the Hive Stand that only submits, answers or observes is
unchanged.

**Only the Hive Stand holds it.** `hive serve` writes it into its serve record
(`<db>.serve.json`, beside the database, created `0600` on POSIX), which the console commands
already read to find the listener; they now send it on every request. The guard's state floor
names the serve record with the database, so a bee on the Hive Stand is refused `fs:read` of it as
it is of the database. No Virtual Cell backend mounts the Hive's state directory.

**The Hive Stand's browser gets it as a cookie from a printed link.** `hive serve` prints
`http://localhost:<port>/stand?key=<key>`. The gate answers `GET /stand` with the right key with a
`303` to `/` and `Set-Cookie: hive_stand=<key>; Path=/; HttpOnly; SameSite=Strict`, and any other
key or method with a bare `403`. The cookie is same-origin, so the Observation Hive's approval
screens work with no client change; script cannot read it and no other site's page makes the
browser send it. The key changes with every serve, so the link is opened once per serve.

**The stand key's holder gets its own rate bucket.** A request carrying the key is counted under
`stand:<address>`, so a guest flooding `127.0.0.1` can no longer starve the console of the door it
administers the Hive through.

The network stays as it is, documented plainly in the backends: QEMU's non-`NONE` policies and
Docker Desktop still let a guest reach the host's loopback services, the Entrance's public and
both-listener routes included. Those routes are the remote listener's too, so a guest reaching them
gains nothing a remote client lacks.

## Consequences

Positive: a Virtual Cell's guest cannot reach a loopback-only row even holding the console's key
and the operator's password, without a change to any Cell's network or a guest-side firewall it
could undo. The protection is the same on every platform and backend, and needs no knowledge of
which forwarder a guest used. The refusal happens before the rate limit, so a guest's attempts
cost the operator nothing.

Negative: a third credential exists, and a program on the Hive Stand that calls a loopback-only
row directly (rather than through `hive entrance`) must read the serve record for it. The browser
on the Hive Stand opens the printed link once per serve before it can approve. The link appears in
`hive serve`'s output, so whoever can read that terminal or its log holds the key until the serve
stops; that is the Hive Stand's operator, or a process with the Hive's own operating-system user,
which ADR-0041 already records as a risk. On Windows the serve record's access is the folder's
own access list, not `0600`. Host-loopback services other than the Entrance (a model server bound
to `127.0.0.1`, anything else the operator runs) remain reachable from QEMU and Docker Desktop
guests, as before.

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
