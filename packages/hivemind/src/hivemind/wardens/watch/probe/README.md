# hivemind.wardens.watch.probe

How a watching Warden looks at its Cell: read-only, through fixed commands, returning a
`waggle.messages.swarm.Observation`.

## Public API (roadmap step 11.10)

- `WatchProbe` (`protocol.py`): `observe(kinds, roots, since_at) -> Observation`. A Protocol, so
  `hivemind.swarm.PollenWatchProbe` (which asks a Pollen Packet) satisfies it by structure.
- `PROBE_FAILURES` (`protocol.py`): the errors a probe raises on purpose; the observer catches
  those and nothing broader.
- `SessionWatchProbe` (`session.py`): looks through the Warden's own `CellSession`. It is lent the
  session (`use_session`) and runs at most two commands per observation.

## Behind `SessionWatchProbe`

- `commands.py`: the closed list of what may be run. One POSIX shell script built from named
  fragments (`/proc`, `df`, `find -printf` with no action, all bounded by `head -c`), and one fixed
  `tail` script. Roots, the directory `df` describes and the files to tail are positional
  parameters, never script text. Every section is announced by a marker carrying a random mark
  chosen per run, so a file name or log line cannot forge a section.
- `system.py`: pure parsers for the process list (with `CpuBaseline`, which measures a CPU share
  over the interval between two samples) and for resource use.
- `files.py`: pure parsers for the file listing and the log tails, and `ChangeTracker`, which
  remembers each root's last listing to tell a created file from a modified one and to see
  deletions.

Linux is the one platform with collectors. On another platform, with no session or with a closed
one, the probe returns an Observation with nothing in it and says why in its `reason`; it never
opens a lease and never calls anything on the session but `exec`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/watch/probe -q
```
