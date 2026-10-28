# Runbooks

How-tos for running a Hive day to day and for the rare bad day. Each one says what to type, what
the Hive does on its own, and what to check when it does not work. `docs/security.md` is the
security model they all sit inside.

- [`real-cells.md`](real-cells.md): running work on the Hive Stand and other borrowed machines: the
  access levels, what a lease does and leaves behind, Leavings (`hive cells leavings`), and what to
  do with a stuck lease or a Cell that misbehaves (release, isolate, sting-cut, abscond).
- [`wardens-and-alarms.md`](wardens-and-alarms.md): how an Alarm climbs from a Worker to you, what
  the shipped escalation policy does with each kind, answering `hive inbox`, and swapping in your
  own policy file.
- [`clustering.md`](clustering.md): what the Hive does when a model provider goes away, clustering
  and waking by hand (`hive cluster`, `hive wake`, `hive cluster status`), and a goal that stays
  paused.
- [`nucs.md`](nucs.md): giving a Swarm device its own Warden and model server, what it does while
  its link is down, and taking it back down.
- [`remote-access.md`](remote-access.md): exposing the Entrance over Tailscale, enrolling a phone, a
  laptop or a program, a lost device, and the Entrance Reducer.
- [`moving-a-slot-to-local.md`](moving-a-slot-to-local.md): moving one model slot (worker, ripener,
  judge, embedder, transcriber) from a hosted provider to a local server with manifest edits only:
  add the provider and its Forage map entry, bind the slot with the hosted binding as fallback, set
  `offline` when everything is local, check with `hive llm`, then replace the hand-set grade with a
  measured one.
- [`enrolling-a-device.md`](enrolling-a-device.md): borrowing a machine for the Swarm: put the
  Pollen binary on the device (a Raspberry Pi included), mint an invite with `hive swarm invite`,
  `pollen enrol` and `pollen install`, see the device with `hive swarm list` and `hive cells list`,
  run a goal on it with `hive swarm run`, change its access, tier and rung, and revoke it.
- [`requeening.md`](requeening.md): what a restarted Queen does on her own after a crash (every
  start is a Requeening, ADR-0062): the Hive Stand's orphaned lease given back, its tasks placed
  afresh from their Handoffs, Virtual Cells' Wardens waited for `requeening_grace_s` and taken back,
  one `queen.requeened` of counts; what to check when a Warden stays missing; the container and Nuc
  legs to run by hand.
- [`absconding.md`](absconding.md): tearing the whole Hive down with `hive abscond` (roadmap step
  13.4): the operator password, the scope review, ticking Leavings, the typed phrase; what goes,
  what is kept, and what `left as found` means.
- [`backup.md`](backup.md): `hive backup` (one checksummed archive of the stores, safe while the
  Hive runs; never the secret store) and `hive restore` (stop the Hive, put the secret store in
  place, restore with `--sha256`; refused for another Hive or a different identity key), and what
  a restore leaves to Requeening.
- [`supersedure.md`](supersedure.md): moving the Hive Stand to another colonized machine with `hive
  supersede` (roadmap step 13.2a, ADR-0065): the candidate test (`hive doctor --supersedure`), the
  freeze, the copy, the held candidate and the handover; the automatic rollback; settling a
  Supersedure a crash left open with `hive supersede --settle`; what the old machine becomes; and
  what is not wired for a real machine yet.
- [`chaos.md`](chaos.md): the nightly chaos suite (roadmap step 13.6): the bar every scenario meets
  (nothing hangs, every goal ends explicitly, no lease, grant or Virtual Cell left open), what each
  scenario breaks, how to run it and the Nuc leg, and what a failure means.
- [`phase6-windows-exit-check.md`](phase6-windows-exit-check.md): the one-time check, run by hand on
  a Windows Hive Stand, that a browser-only goal succeeds through Playwright with no display.
