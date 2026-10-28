# hivemind.queen.supersedure

Supersedure moves the Hive Stand (the machine the Queen runs on) to another colonized machine
(roadmap 13.2a, ADR-0065). The old Queen freezes, her stores travel as one checksummed backup over
the candidate's session, and the candidate's Queen comes up held in `REQUEENING` until the old one
has stood down and acknowledged her. Only then does the signed `QueenMoved` go out. A candidate not
ready inside the grace window is rolled back. Each node's own trail says which side of a
Supersedure it was on, and `gate` makes every restart obey it, so two Queens never run.

- `preflight.py`: the candidate test (`hive doctor --supersedure`).
- `copy.py`, `handover.py`, `rollback.py`: the copy and the two endings.
- `run.py`: the driver, `begin` then `finish`.
- `record.py`: the four trail events and how to read them.
- `gate.py`: the start rules and the candidate's hold.
- `ports.py`: the candidate, the devices and the Queen's levers, as protocols.
- `door.py`: the human's lever on the running Queen.
