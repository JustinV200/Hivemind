# hivemind.cli.in_cell.redial

A Night Veil Cell's link to the Queen is a Tor circuit to her hidden service, and Tor can die under
it. This package is what the in-Cell Warden entry point (`hivemind-in-cell`) builds the Warden of a
Night Veil Cell over, so that the tier's promise holds when it does: a Cell whose Tor dies, with
its VPN and default route still up, is **cut off and clusters**; it does not fall back to a direct
or VPN-only connection to the Hive Stand (codingrules 8.7, phase 11's exit criteria). Every other
Cell keeps the bare transport it always had, and nothing in this package runs for it.

The link is the one a colonized device's Warden has (`hivemind.wardens.offline`,
ADR-0053): a `ReconnectingQueenLink` that, on a lost connection, redials with capped backoff,
announces the Cell again, replays what its outbox queued while it was down, ships the trail
segment and goes online. What is new is only what makes it a Night Veil Cell's:

- **No other route.** `ProxyDialer` is built from the Cell's config and cannot be built without a
  SOCKS proxy; every attempt goes through it, naming the onion service (the Cell holds no address
  of the Hive Stand to dial and no resolver that would answer the name). A proxy that is down or
  refusing is a failed attempt, and the backoff tries the same proxy again.
- **The Cell says who it is again.** `CellAnnouncer` sends the `cell.ready` with the control-link
  checks, the capacity and a heartbeat on every connection, in the order the Queen's readiness
  gate reads them. The Cell's first connection is the same announcement it always made, and
  `AfterRedial` records no `warden.reconnected` for it.
- **The Warden is cut off, then clustered.** `RedialingLink.equip` gives the built Warden an
  `Outage` and the link in `DeviceDeps`, and wakes its tick when the link changes. It goes OFFLINE
  as soon as the link does, keeping working what it can think for with a model of its own
  (a Night Veil Cell's bindings are local-only), and clusters fully past `HIVEMIND_MAX_OFFLINE_S`,
  the Hive's `[supervision] max_offline_s`, which a Cell has no manifest to read and so is told at
  provisioning. It does not send a Drone to look into its own Tor circuit (no reconnect budget).

When Tor answers again the Queen, who detached the Warden when its connection ended, takes it back
as a Warden she has had before (`warden.reconnected`). What is not built: she does not resume the
tasks of a Virtual Cell that clustered fully (a device's come back through the swarm gate's hold);
they wait for a fresh `TaskAssign`.

## Public API

- **Link** (`build`): `redials(config) -> bool` (a Night Veil Cell's link mends),
  `connect_redialing(config, source, trail, clock) -> RedialingLink` (builds the link and makes the
  first connection, retrying through the proxy until Tor answers), `RedialingLink.equip(deps)`,
  `OUTBOX_DIR`.
- **Collaborators** (`dial`): `ProxyDialer`, `CellAnnouncer`, `AfterRedial`.

The outbox that queues a cut-off Warden's results and Alarms is a file below the Cell's scratch
root (`<scratch>/outbox/outbox.jsonl`), destroyed with the Cell, which a Night Veil task never
outlives.

## Tests

`tests/unit/cli/in_cell/redial/` covers the dialer (through the proxy, and nothing reached when the
proxy is down or refusing), the announcer and the link's deps. `test_main_night_veil_redial.py`
kills a fake Tor under a connected Cell at the entry point, and `tests/e2e/test_night_veil_tor_loss.py`
does so under a working Cell in a whole Hive, with the listener it could have reached directly
right there beside the proxy.
