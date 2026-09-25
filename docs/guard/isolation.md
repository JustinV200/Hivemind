# Guard requests and Cell isolation

Roadmap step 10.6a, [ADR-0035](../adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md).
Code: `hivemind.queen.guard_requests` (the request, its decision), `hivemind.queen.isolation`
(the one isolation path and the lift), `hivemind.wardens.isolation` (a Cell's Warden tainting its
own store and refusing a tainted resume), `hivemind.hive.backends.docker` (the control network and
the egress lever), `hivemind.entrance.routes.isolation` (the human's levers).
State machines: [codingrules Appendix C](../../.claude/codingrules.md), "Cell isolation" and
"Guard request" rows.

The Guard Bee watches the Pheromone Trail and acts alone only to narrow the whole Hive. Anything
aimed at one Cell or one bee it can only **request**, and only the Queen decides. Only the Queen
isolates a Cell, and only the human isolates the Hive Stand's own lease or lifts an isolation.
Every Hive `hive run` or `hive serve` composes runs the Guard Bee on the Queen's tick, filing
through her own door ([the Guard Bee](guard-bee.md)).

The Guard Bee's shipped dire patterns are `injection_then_denial` (a scanner flag, then a denial
by the same bee in the same episode), the two Cell gate forgeries, `envelope_forgery` (a frame
that failed its signature on a Cell's own link) and `segment_forgery` (a trail segment a Cell
shipped under another node's identity), and `subject_forgery` (a Cell's node recording what
another Cell owns, which names the framer and never the framed Cell). The Queen decides each of
those by rule.

## The request path

1. **Filed.** The Guard Bee calls `GuardRequestDoor.file_guard_request(report)`. The running Queen
   is that door (the composition root hands it over as `Hive.guard_door`). The request is committed
   to her own table on the Hive's SQLite file before the call returns, so a request filed just
   before a restart is still decided after it. Filing wakes her. A report that asks for nothing is
   refused: the Guard Bee records it as `guard.alert` alone.
2. **Ordered.** On her next tick every undecided request becomes one `GUARD_REQUEST` inbox item.
   Its score is a fixed 100 with no task link and no latency term, times the Guard principal's
   multiplier. That puts it above a CRITICAL Alarm and above any message from the human, so only
   age orders two requests. A Warden's Attendant refuses the kind outright.
3. **Decided.** A report whose rule is listed in `[guard] dire_patterns` is decided by rule, with
   no model: isolate the Cell (or quarantine the named task when no Cell is known). Any other
   report goes to one awake episode, which sees the report's facts (ids, rule, confidence,
   recommendation, counts and the rule's own summary) and never any content. The episode may
   answer `ISOLATE_CELL`, `QUARANTINE_BEE` or `DISMISS`. If her awake mode is unavailable, if the
   model fails, or if it answers anything else, the fallback is to isolate, because isolation only
   removes access.
4. **Recorded, then carried out.** `queen.decided` goes on the trail first, with the report id,
   the action and its basis (`rule`, `awake`, `fallback`). Only then is the decision carried out.
   The request's row is stamped afterwards, so it is never decided twice. A crash in between
   decides it again, and every action is idempotent.
5. **Shown.** A report she acted on, or one at CRITICAL confidence, reaches the human as a
   SECURITY Alarm. It is pushed to every enrolled device and names the report id.

A CRITICAL report that asks for nothing (a raised audit rate, an Entrance reduce order, an
observation) reaches the human through `GuardRequestDoor.report_to_human`. Every Alarm about a
report carries the same id, the report's own ULID under `alarm_`. Its `alarm.escalated` row is
durable, so each report is shown at most once, whichever path gets there first. A CRITICAL
request is filed rather than shown, so the one Alarm the human gets for it also says what the
Queen did.

## The isolation path

`hivemind.queen.isolation.isolate_cell` is the one path, used by the Queen's decision, by her own
`ISOLATE` policy row (`PolicyAction.ISOLATE`, which a Warden's policy refuses to load) and by the
human's lever. In order:

1. The Cell's own Warden must be attached. An isolation that already stands changes nothing.
2. The Guard checks it at `EnforcementPoint.ISOLATION`. The Queen is refused the Hive Stand's own
   lease (`guard.denied`, rule `guard.scope.hive_stand`).
3. A `BLOCK` Cell Wax note is written first, so placement sends nothing more there.
4. The Warden's grants are revoked (`forage.revoked`, then `GrantRevoked` to the Warden).
5. Every task on the Cell gets Clustering's lever pair: `Intervene(HANDOFF)`, then `TaskPause`.
   The path then waits for each bee's `worker.paused`, bounded by `GuardDeps.pause_timeout_s`
   (5 s). A Virtual Cell's Warden ships its trail segment the moment a bee reports it paused. A
   bee that does not answer is named as unacknowledged, and the path goes on. Each task moves to
   PAUSED.
6. A Virtual Cell's egress is cut to its Waggle link alone (`hivemind.hive.CellEgress`). A backend
   that cannot do this, or cannot for this Cell, says so (`unsupported`). A Real Cell is left
   exactly as found (`untracked`). A Docker Cell's link rides the Hive's control network, so the
   cut leaves it alone (below).
7. `cell.isolated` is recorded with the reason, who ordered it, the report, the evidence, the
   decision and every step's result.
8. The Cell's memory is tainted from the first cited event on (`TaintSource.ISOLATION`, caused by
   `cell.isolated`, which keeps that instant as `suspect_at`), first on the Hive's own tables. Then
   the Cell's Warden gets a `CellTaintOrder` (Waggle 1.8) naming the same bees, tasks, instant and
   cause, and runs the same setter over the memory store it keeps inside the Cell, which the
   Hive's label cannot reach. An order lost to a closed link is sent again whenever the Warden
   attaches while the isolation stands; the setter is idempotent. See
   [tainted memory](tainted-memory.md).
9. A CRITICAL SECURITY Alarm tells the human.

Nothing on the path releases, tears down or overwinters the Cell. Its lease and scratch are kept
for forensics.

From then on the Cell's Warden refuses any `TaskAssign` that would resume a bee from a Handoff its
own store labels tainted: nothing spawns, the refusal is `guard.denied` at the `isolation` point
(`guard.scope.tainted_handoff`), the task's grant is withdrawn and the Queen is told the task is
held (PAUSED). A lift restores placement and egress; it never lets a resume through a tainted
Handoff. Only a judge's clearance does.

### Egress on the backends

| Backend | `can_cut_egress` | What it takes |
|---|---|---|
| fake | yes | The reference: a per-Cell flag. |
| Docker | yes, with `[virtual_cells] control_subnet` | Dual-homing (below): the cut detaches the Cell from its own network, the lift attaches it again. Without a control subnet, no: a network's `internal` flag is fixed at creation, and `disconnect` would take the Waggle link with it. |
| QEMU | no | `restrict=on` is fixed at creation, and QMP's link and netdev levers take the VM's one NIC down, the link's included. It would need the same split as Docker: a second NIC, so the link and the egress never share one (below). |

### Docker: the control network

A Hive that sets `control_subnet` dual-homes every Docker Cell whose link does not ride Tor
(`hivemind.hive.backends.docker.network`):

- **Control.** One per-Hive `internal` bridge network on that subnet, its gateway the host's own
  address there, inter-container traffic off. The Queen's listener binds the gateway, and a Cell
  dials it: the Waggle link rides this network alone. An `internal` network gives a container no
  default route, so over it a Cell reaches the gateway and nothing beyond the host, and no other
  Cell. `hive run` makes the network (or reuses it, if it matches) before the listener binds.
- **Egress.** The Cell's own per-policy network (as before), attached before the Cell starts. It
  carries the default route, so it is how the Cell reaches everything else, the host's docker0
  (`host.docker.internal`, which a provider's URL names) included.

The cut is `docker network disconnect` of the egress network, and the lift `connect`, both
idempotent. The link's connection lives on the control interface, so it never notices: no redial,
no missed heartbeat, no `CELL_UNREACHABLE`. The manifest holds `listen_host` to the gateway (and
`advertise_url`, if set), and the backend refuses to provision a dual-homed Cell whose endpoint
names another host, because that link would ride the egress and go with the first cut.

```toml
[virtual_cells]
backend = "docker"
control_subnet = "10.213.1.0/24"   # private IPv4, at least a /29; not used by any other network
listen_host = "10.213.1.1"         # its first host: the gateway
listen_port = 47800
```

What it does not do. Anything on the Hive Stand bound to every interface answers on the control
gateway too, so an isolated Cell that dials that address still reaches it; bind the model servers
Cells use to docker0 (`172.17.0.1`) or to loopback behind a proxy, never `0.0.0.0`. A Night Veil
(VPN_TOR) Cell is never dual-homed, since its link rides Tor over its egress, so its egress is not
cut (`unsupported`), and neither is a Cell provisioned before the control subnet was set. Under
`network_policy = "none"` a Cell's own network is `internal`, so the control network is its only
way to the Queen; without a control subnet such a Cell has no route to docker0 on native Linux.

Proved against a real daemon by `tests/integration/test_docker_egress.py`: from inside the
container an outside host and docker0 answer before the cut and not after it, the control
gateway's listener answers throughout, a stand-in for another Cell on the control network never
does, heartbeats keep arriving with no re-attach, the Cell's own Warden labels its store, the lift
restores both, a resume from the tainted Handoff is refused inside the Cell, and the container is
gone at teardown.

### QEMU: what a cut would need

QEMU's user networking gives a VM one NIC per `-netdev`, and `restrict=on` is fixed when it is
created. A cut that keeps the link needs two: a control NIC (`restrict=on` plus the one `guestfwd`
to the Queen, as the NONE policy builds today) and an egress NIC (an unrestricted user netdev)
carrying the default route, with the cloud-init network config bringing both up and routing the
Queen's forwarded address through the control NIC. The cut is then QMP `set_link` on the egress
NIC (`up=false`) and the lift `up=true`. Not built: this host has no QEMU to prove it on
(ADR-0026).

## The Hive Stand exception

The Queen may not isolate the Hive Stand's own lease. When her decision on a request reaches for
it, the isolation point refuses her. Her decision then falls back to three things:

- She quarantines every implicated task through its Warden (the 10.6c order).
- She holds the implicated goals off the Hive Stand. This is a `PlacementHold` on her decision's
  row, and placement reads it as a block on that Cell for those goals only.
- She raises a CRITICAL SECURITY Alarm naming the report.

The human can then isolate the Hive Stand themselves, citing the report, or lift the hold.

## The human's levers

Both levers need an interactive device inside its step-up window, holding `entrance:steward`, the
family ADR-0031 reserves to the human. A program can never step up, so nothing is held for it.
Both are served on both listeners, and both go through the Queen's door.

| Method | Path | Does |
|---|---|---|
| `POST` | `/v1/cells/{cell_id}/isolate` | The one isolation path, ordered by the human (`{"reason": ..., "report_id": ...}`; a cited report's first event dates the taint). The only way to isolate the Hive Stand. |
| `POST` | `/v1/cells/{cell_id}/lift` | Releases every placement hold on the Cell, clears the isolation's `BLOCK` wax, restores a Virtual Cell's egress and records `cell.isolation_lifted`. A lift with nothing to lift answers `409`. |

A lift restores placement and egress. It does not restore trust. Tainted memory stays tainted until
a judge clears it (`clear_taint`), and the tasks the isolation paused stay paused.

### From the terminal

The operator pulls both levers from the Hive Stand's own terminal while `hive serve` runs:

```
hive cells isolate CELL_ID --reason "why, in a short phrase" [--report GUARD_REPORT_ID] [--json]
hive cells lift CELL_ID [--json]
```

Each acts as the Hive Stand's console device (ADR-0033: its key is wrapped under the operator
password, approved and loopback-bound) over the running serve's loopback listener, exactly as
`hive entrance` commands do. It logs in with the operator password and steps up with the same
password when the route asks; `--password-stdin` reads it once, for both. A malformed report id is
refused before anything is sent. A refusal from the route (a Cell the Queen does not know, a lift
with nothing to lift) prints `hive cells isolate refused: ...` or `hive cells lift refused: ...`
and exits 1. With no `hive serve` running there is nothing to act through, and the command says
so. An isolation prints its `cell.isolated` event, the `BLOCK` Cell Wax it wrote, the grants it
revoked, the tasks it paused, the Cell's egress and how many memory items it tainted. A lift prints
the isolation it ended, the placement holds it released and the egress, and says that tainted
memory stays tainted. `--json` prints the route's own view instead.

## The way out of a quarantine

When a judge's verdict clears the checkpoint a quarantine wrote (`memory.taint_cleared` about the
Handoff that `warden.intervened` names), the Queen's tick resumes the task from exactly that
checkpoint (`hivemind.queen.quarantine.resume_cleared`). She does this only when the verdict is
newer than the task's last pause, and never onto an isolated Cell. The Warden's gate admits that
resume and nothing else.

## Known limits

- The Cell's Warden labels what its store holds when the order arrives; memory written there
  afterwards is not labelled by it. The paused bees write none: a paused bee holds before its next
  tool call, and nothing resumes it in place (a resume is a fresh `TaskAssign`, which meets the
  gate).
- The QEMU backend cannot cut egress yet (see above), and neither can Docker without a control
  subnet or for a Night Veil Cell.
- An isolation the Queen orders on a Cell whose link is gone (the Cell gate closes a link that
  carried a forged frame) finds no attached Warden: her decision records `cell_not_attached`,
  and the human still gets the CRITICAL Alarm naming the report.
