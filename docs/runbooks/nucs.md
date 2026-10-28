# Nucs: devices that think for themselves

A Nuc is a Swarm device that runs its own Warden and its own model server. Because everything it
needs to keep working is on the device, it can carry on when its link to the Hive Stand drops, and
it takes load off the shared model seats while it is connected. This page covers making one, what it
does offline, and taking it back down. The design is ADR-0052 and ADR-0053, and enrolling the
device in the first place is `enrolling-a-device.md`.

## The ladder

A device moves one rung at a time, and the Warden always moves before the models do:

| Rung | On the device | The Warden runs |
|---|---|---|
| `GATEWAY` | the Pollen Packet only | on the Hive Stand |
| `COLONIZED` | the packet, the Warden and its Workers | on the device |
| `NUC` | all of that plus a model server | on the device |

`GATEWAY` straight to `NUC` is refused.

## Set the device up for it

A Nuc needs three things before the Queen will promote it.

1. It is colonized. At `pollen install`, tell the packet where the Hive's runtime comes from with
   `--runtime-wheelhouse DIR` or `--runtime-index URL`, and give the device's own manifest with
   `--warden-manifest FILE`. The packet installs the pinned runtime from there, never from anything
   sent over the wire.
2. The device manifest declares the server. `docs/manifests/device.toml` is the annotated example:
   an `[llm.servers.<id>]` table with the server's `argv`, `port`, `health_path` and
   `load_on_start = false`, and a `[forage.map]` row for each model it may load, copied from the
   Hive's manifest. A model that needs a GPU gives its `vram_bytes_required`.
3. The Hive's own manifest lists the same server id in its `[llm.servers]`, also with
   `load_on_start = false` so the Hive Stand never starts a copy, and names the models it may load.
   A model outside that list is refused at `SLOT_BINDING` and recorded as `guard.denied`.

## Promote it

```
hive swarm promote kitchen-pc
```

Run it once to colonize a gateway device and again to make it a Nuc. It records the wish, and the
running Hive moves the device a rung when it can. When a model fits the device's free VRAM, the
Queen sets the device's model ceilings, sends `NucPromote` and writes a hosting plan with the
device's own models first and the shared ones behind them. The device's Warden starts the server,
loads the models within those ceilings and reports each one as a new source. The Guard checks the
promotion, and the trail records `swarm.nuc_promote`, `swarm.nuc_promoted` and `swarm.promoted`.

The Queen also promotes a colonized device on her own, when the shared seats are busier than
`[swarm.nuc] seat_pressure` (0.75 by default) or a task has to survive a lost link. She lowers one
she promoted herself after `lower_after_s` with every reason gone and the device idle. She never
demotes a Nuc you promoted.

## What happens when the link drops

On the device:

- The Warden goes `OFFLINE` and keeps its Workers running on its own models. Shared grants are
  frozen, neither renewed nor revoked from its side.
- It cannot grow. A new Cell is refused, a request for shared Forage waits in the outbox, and a
  question for you is held until the Queen is back.
- Results, Alarms and Nectar queue in the outbox, and it writes its own trail segment.
- It spawns one short-lived Drone on its own models to find out why the link dropped and mend it if
  it can, inside `[supervision.reconnect_budget]` from the device manifest. The Drone works under
  the Cell's access level and the Capping gate, and records `warden.diagnosed` as fixed, diagnosed
  or no cause found.
- It re-dials with backoff from half a second up to thirty. Past `[supervision] max_offline_s` it
  clusters fully.

On the Hive Stand, the Queen marks the device unreachable and holds its tasks for `[swarm]
unreachable_grace_s` without failing them. A Drone on the Hive Stand checks reachability from her
end. Then she decides by rung. A Nuc's tasks keep waiting up to `max_offline_s`, since the device
is still working on them. A gateway or colonized device's tasks are placed afresh from their newest
Handoff.

When the link comes back, the Warden replays its outbox, ships its signed trail segment and records
`warden.reconnected`. The Queen re-sends its grants, ceilings and hosting plan, and refuses a
segment whose signature does not verify against the device's key.

## Take it down

```
hive swarm demote kitchen-pc
```

That stops the model server and drops the device one rung, back to colonized. Demote again to bring
the Warden back to the Hive Stand. `demote` pins the device at the rung it lands on, so the Queen
does not climb it back on her own, and a later `promote` lets the pin go.

## When it does not work

- The Queen never promotes it: check the device's capacity report names the server, the model fits
  its free VRAM, and both manifests list the same server id. `hive swarm list` shows the rung.
- A Nuc comes back without its server after a restart: the Queen promotes it again or lowers it
  cleanly, so give it a beat.
- An Alarm or a question shows up twice after a Queen restart: frames resent from a stretch the drop
  went unnoticed can repeat, because her replay window starts empty. Results and Nectar do not.
