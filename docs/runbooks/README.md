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
