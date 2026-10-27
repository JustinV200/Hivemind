# Sting Cut: one Cell's emergency disconnect

Roadmap step 13.4a. Code: `hivemind.queen.sting_cut` (the one cut path, the human's door, the
Virtual side's seam and its `LifecycleCellCut`), `hivemind.entrance.routes.isolation.sting_cut`
(the human's route), `hivemind.cli.readback.isolation` (`hive cells sting-cut`). It builds on
[isolation](isolation.md).

Isolation keeps a Cell for forensics: its lease, its scratch and its link stay. A Sting Cut goes
further and cuts the Cell off for good:

- every grant on the Cell is revoked;
- its Warden releases the lease, which kills every process the Hive started for it, removes its
  scratch and closes its session;
- a Virtual Cell's key is forgotten at the readiness gate and its link closed, so its Warden can
  never prove itself again, not even after a restart (a restarted Queen skips every Cell the trail
  shows cut);
- a Night Veil Cell is torn down after the cut is recorded, which purges its segment and every
  record its tier retained.

`cell.sting_cut` records the reason, who cut (`cut_by`), the Guard report and evidence, the
isolation it follows, the snapshot kept and each step's result. Central audit state is left alone:
the trail, the Brood Chamber, Meadow and Propolis records all stay. A Virtual Cell that is not
Night Veil is left standing, cut off, with its evidence snapshot (kept for a month). Destroy it
with `hive cells destroy` once you are done with it.

## The human's cut

```
hive cells sting-cut <cell_...|lease_...> --reason "a forged frame from this Cell"
```

It works on any Cell, the Hive Stand's own lease included. A lease id names the Cell its
`cell.leased` event recorded. The command acts as the Hive Stand's console over the running `hive
serve`'s loopback listener (`POST /v1/cells/{target}/sting-cut`). It is break-glass: it needs a
step-up (the operator password again) and the typed phrase `sting cut this cell`. The command asks
for the phrase once the console is logged in. A program, which no person types at, can never ask for
a cut. If no isolation stands on the Cell, the cut isolates it first through the one isolation path,
so its BLOCK wax, paused bees and tainted memory hold.

## The Queen's cut

The Queen cuts a Cell on her own only for the dire patterns `[guard.sting_cut] dire_patterns`
lists. The defaults are `envelope_forgery`, `segment_forgery`, `replay_refusal_burst` and
`frame_ceiling_repeats`. Her awake episode may also decide `STING_CUT` on any other Guard request.
Her decision is `queen.decided` with action `STING_CUT`. She isolates first, and then cuts only if
every condition holds:

- an isolation stands on the Cell (her cut follows one, never replaces it);
- the Cell is not the Hive Stand's own lease (only the human cuts that; `guard.denied`, rule
  `guard.scope.hive_stand`);
- she has cut fewer than `max_autonomous_sting_cuts_per_hour` Cells in the last hour (default 2;
  0 leaves every cut to the human; past it, `guard.denied` with rule `guard.scope.hourly_cap`);
- the Cell's evidence was kept first: a snapshot through its backend. She never cuts a Real Cell or
  a Virtual Cell whose backend cannot snapshot on her own. Copying a Real Cell's scratch in as
  tainted C2 Nectar is not built yet, so such a Cell stays isolated and the human decides. A Night
  Veil Cell keeps nothing by design.

A cut she could not make leaves the Cell isolated. The request's decision row then says why, for
example `isolated_cut_refused_no_evidence`. Every cut reaches the human as a CRITICAL SECURITY
Alarm pushed to every device.
