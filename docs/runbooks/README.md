# Runbooks

Operational how-tos for running a Hive: requeening a crashed Queen, Supersedure (moving the Hive
Stand to another machine), remote access, Absconding (the human-only break-glass teardown of the
whole Hive), and backups. Populated as each of those capabilities lands, mostly in phase 13
(Resilience).

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
