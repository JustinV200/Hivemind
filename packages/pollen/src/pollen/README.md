# pollen

The Pollen Packet is the small connector that runs on a device the Hive borrows: a Raspberry Pi, a
spare laptop, a desktop. It dials out to the Queen, proves who it is with a key it made itself, and
then does exactly what the Queen or a Warden asks, inside a lease, after checking every request
against the capabilities the device was granted. It holds no model, writes no trail, supervises
nothing and never listens. It depends on `waggle`, pydantic and psutil only, so it installs on
hardware with no Docker, no SQLite extensions and no LLM SDK (ADR-0051).

```text
  invite (pollen1....)                         the Queen's swarm listener
        │                                                ▲
        ▼                                                │ wss://, pinned by the invite
  pollen enrol ──► key + state ──► pollen run ──► agent ─┘
                                                   │
                     verified, addressed, fresh ───┤
                                                   ▼
                          capability check ──► executors (sessions, files, background,
                                                   │        observation, probes)
                                                   ▼
                                     lease (scratch, quota, restore record, dead-man switch)
                                                   │
                         platform shims (Linux, macOS, Windows) under all of it
```

## Public API

- `PollenPacket` and `build_packet`: the packet's loop, and the composition root that builds it.
- `PacketState` and `DataLayout`: what the packet persists, and where its data directory is.
- `PollenError`: the root of every error pollen raises on purpose.
- The `pollen` command (`cli.py`): `enrol`, `run`, `install`, `uninstall`, `status` and `version`.
- `teardown.py`: what `pollen uninstall` ends that the service left running (the packet under
  the Windows task's console host, and the lease programs it started).
  `--data-dir`, else `POLLEN_DATA_DIR`, else the platform's default, names the data directory.

## Packages

| Package | What it holds |
|---|---|
| `agent/` | the gateway loop: dial out, the signed first frame, heartbeats, admitting only frames a trusted node signed for this device |
| `enrol/` | first-run registration from a `pollen1.` invite |
| `executors/` | what the device can do once enrolled, one executor per request type |
| `lease/` | the device side of a Real Cell lease, and the dead-man switch |
| `bootstrap/` | climbing from Level 0 to Level 1 (ADR-0052): the pinned runtime and the on-device Warden |
| `platform/` | every OS difference, behind one `PlatformShim` |

Each one has its own README. `docs/runbooks/enrolling-a-device.md` is the operator's side.

## How to test this

```bash
uv run --frozen pytest packages/pollen/tests
```

The end-to-end tests run the real packet against a scripted Queen on a loopback WebSocket
(`tests/support/queen_double.py`), which signs what it sends and can put tampered or replayed
frames on the wire, so the signing, freshness and capability checks meet what a device would.
