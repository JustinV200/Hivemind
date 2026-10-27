# hivemind.cli.in_cell.redial

Every Virtual Cell's Warden is built over a link that mends. Two things can cut it. Tor can die
under a Night Veil Cell's circuit to the Queen's hidden service, and the tier's promise must hold
when it does: a Cell whose Tor dies, with its VPN and default route still up, is **cut off and
clusters**; it does not fall back to a direct or VPN-only connection to the Hive Stand (codingrules
8.7, phase 11's exit criteria). And the Queen herself can die under any Cell's link and come back
(Requeening, ADR-0062): the Cell's work outlives her, and its Warden dials her again and is taken
back by the restarted Queen.

The link is the one a colonized device's Warden has (`hivemind.wardens.offline`,
ADR-0053): a `ReconnectingQueenLink` that, on a lost connection, redials with capped backoff,
announces the Cell again, replays what its outbox queued while it was down, ships the trail
segment and goes online. What is new is only what makes it a Virtual Cell's:

- **No other route for Night Veil.** `dialer_for` gives a Night Veil Cell `ProxyDialer` and every
  other Cell `DirectDialer` (one attempt at the Queen's URL, as its first connection dialled her).
  `ProxyDialer` is built from the Cell's config and cannot be built without a SOCKS proxy; every attempt goes through it, naming the onion service (the Cell holds no address
  of the Hive Stand to dial and no resolver that would answer the name). A proxy that is down or
  refusing is a failed attempt, and the backoff tries the same proxy again.
- **The Cell says who it is again.** `CellAnnouncer` sends the `cell.ready` with its tier's checks
  (a Night Veil Cell's control-link checks, a Propolis Cell's tunnel checks read afresh, none for a
  Meadow Cell), the capacity and a heartbeat on every connection, in the order the Queen's readiness
  gate reads them. The Cell's first connection is the same announcement it always made, and
  `AfterRedial` records no `warden.reconnected` for it.
- **The Warden is cut off, then clustered.** `RedialingLink.equip` gives the built Warden an
  `Outage` and the link in `DeviceDeps`, and wakes its tick when the link changes. It goes OFFLINE
  as soon as the link does, keeping working what it can think for with a model of its own
  (a Night Veil Cell's bindings are local-only; a sub-bee on a hosted model stops at a Handoff and
  is started again when the link returns), and clusters fully past `HIVEMIND_MAX_OFFLINE_S`,
  the Hive's `[supervision] max_offline_s`, which a Cell has no manifest to read and so is told at
  provisioning. It sends no Drone to look into its own link (no reconnect budget).

When Tor answers again the Queen, who detached the Warden when its connection ended, takes it back
as a Warden she has had before (`warden.reconnected`); a restarted Queen re-arms her readiness gate
with the Cell's public key from its backend label and takes it back the same way. Its first Heartbeat past OFFLINE settles
how the cut ended: a Warden that clustered fully says CLUSTERED, and she resumes its tasks from the
Handoff she can recall (`hivemind.queen.cluster.returned`); a device's come back through the swarm
gate's hold.

## Public API

- **Link** (`build`): `connect_redialing(config, source, trail, clock) -> RedialingLink` (builds
  the link and makes the first connection, retrying until the Queen, or Tor, answers),
  `RedialingLink.equip(deps)`, `OUTBOX_DIR`.
- **Collaborators** (`dial`): `dialer_for`, `ProxyDialer`, `DirectDialer`, `CellAnnouncer`,
  `AfterRedial`.

The outbox that queues a cut-off Warden's results and Alarms is a file below the Cell's scratch
root (`<scratch>/outbox/outbox.jsonl`), destroyed with the Cell.

## Tests

`tests/unit/cli/in_cell/redial/` covers the dialers (Night Veil's through the proxy, and nothing
reached when the proxy is down or refusing; every other tier's direct), the announcer and the
link's deps. `test_main.py` drops a Meadow Cell's connection and sees the same Warden announce
itself again. `test_main_night_veil_redial.py`
kills a fake Tor under a connected Cell at the entry point, and `tests/e2e/test_night_veil_tor_loss.py`
does so under a working Cell in a whole Hive, with the listener it could have reached directly
right there beside the proxy. The same unit module destroys a Cell, as its backend does, to show
that the Cell closes its link on every way out of `run_in_cell_warden` (a stop that hangs or
raises, a start still under way) and a link left open is never there to redial for a Cell that is
gone.
