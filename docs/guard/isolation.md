# Guard requests and Cell isolation

Roadmap step 10.6a, [ADR-0035](../adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md).
Code: `hivemind.queen.guard_requests` (the request, its decision), `hivemind.queen.isolation`
(the one isolation path and the lift), `hivemind.entrance.routes.isolation` (the human's levers).
State machines: [codingrules Appendix C](../../.claude/codingrules.md), "Cell isolation" and
"Guard request" rows.

The Guard Bee watches the Pheromone Trail and acts alone only to narrow the whole Hive. Anything
aimed at one Cell or one bee it can only **request**, and only the Queen decides. Only the Queen
isolates a Cell, and only the human isolates the Hive Stand's own lease or lifts an isolation.

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
   that cannot do this says so (`unsupported`). A Real Cell is left exactly as found
   (`untracked`).
7. `cell.isolated` is recorded with the reason, who ordered it, the report, the evidence, the
   decision and every step's result.
8. The Cell's memory is tainted from the first cited event on (`TaintSource.ISOLATION`, caused by
   `cell.isolated`). See [tainted memory](tainted-memory.md).
9. A CRITICAL SECURITY Alarm tells the human.

Nothing on the path releases, tears down or overwinters the Cell. Its lease and scratch are kept
for forensics.

### Egress on the backends

| Backend | `can_cut_egress` | What it takes |
|---|---|---|
| fake | yes | The reference: a per-Cell flag. |
| Docker | no | A network's `internal` flag is fixed at creation. The only runtime lever, `disconnect`, drops the very WebSocket isolation must keep, and an internal network has no route to `host-gateway`. A real cut needs host firewall rules (DOCKER-USER, per Cell bridge subnet, installed by a privileged helper) or an egress proxy. |
| QEMU | no | `restrict=on` is fixed at creation; QMP's link and netdev levers take the one NIC down. |

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

## The way out of a quarantine

When a judge's verdict clears the checkpoint a quarantine wrote (`memory.taint_cleared` about the
Handoff that `warden.intervened` names), the Queen's tick resumes the task from exactly that
checkpoint (`hivemind.queen.quarantine.resume_cleared`). She does this only when the verdict is
newer than the task's last pause, and never onto an isolated Cell. The Warden's gate admits that
resume and nothing else.

## Known limits

- A Virtual Cell's own memory store lives inside the Cell (ADR-0027), so the Queen's taint
  reaches only the Hive's own tables. The isolated Cell is kept whole, and nothing resumes on it
  while it stands. Tainting the in-Cell store needs a Waggle message that asks the Cell's Warden to
  run the setter (a follow-up).
- The Docker and QEMU backends cannot cut egress yet (see the table above).
- No CLI levers exist yet (`hive cells isolate|lift` is a follow-up for the CLI owner).
