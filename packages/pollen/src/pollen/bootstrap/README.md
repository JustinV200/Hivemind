# pollen.bootstrap

The bootstrap package is how a Level 0 device climbs to Level 1 (ADR-0052). The Queen orders it in
three `swarm.colonize` steps and the packet answers each with `swarm.colonized`: PREPARE builds a
virtual environment under the data directory and installs the pinned `hivemind` version into it,
only from the wheelhouse or index the operator recorded at `pollen install`; START launches
`hivemind-in-cell` from it as a long-lived Warden in a process group of its own, outside every
lease, and records it together with the new level so the dead-man switch stands down while it
lives; WITHDRAW stops it (TERM, a long grace, then the group is killed) and puts the level back.
A packet that is restarted finds the recorded Warden and adopts it, and one that finds its Warden
dead says so and returns to Level 0.

- `runtime.py`: the environment: find an interpreter, make the venv, run pip, check the entry point.
- `warden.py`: the Warden's process: the environment it is started with, `WardenKeeper`, stopping.
- `service.py`: `ColonizeService`, the executor: it checks the order, runs the step as a task
  bounded by the order's deadline, one at a time, and answers durably.

Nothing here imports `hivemind`: the variables the Warden is started with are a contract with
`hivemind.manifest.env`, checked by a test on the hivemind side. The runtime source never comes from
the wire, and the Warden gets its key as a file, never as a value.
