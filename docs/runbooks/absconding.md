# Absconding: tearing the whole Hive down

Absconding is the human's break-glass teardown of the whole Hive (roadmap step 13.4, codingrules
15). Use it when you are leaving a machine for good, when something has gone badly wrong and you
want every borrowed device given back at once, or when the Hive's own database may be all that
survived. It works with no Queen running and with a corrupt Brood Chamber, because it reads only
backend labels, the trail and the small tables named below.

Only you can do it. No route, autopilot rule, Queen, Warden, Worker or tool can call it: the
`absconding` enforcement point refuses every principal but the operator, whatever a role
override grants (`guard.state_floor.human_only`).

## Run it

On the Hive Stand:

```
hive abscond --reason "leaving this laptop" --manifest hive.toml
```

1. **Password.** Type the operator password. It must open the Hive Stand console's key, the same
   proof a console login starts from. Nothing of the Hive is opened until it does.
2. **Scope review.** You see everything that will go: the Virtual Cells (and how many are
   dormant), the open leases on the Hive Stand and on devices, the live grants, the Workers still
   running, the temp dirs (scratch no lease owns; none while `[hive_stand] keep_scratch` is set or
   a Queen looks to be running) and the resident Basket blobs.
3. **Leavings.** Every path a Cell left outside scratch on purpose is numbered. Type the numbers of
   those to remove, comma- or space-separated. A blank line keeps them all. They were often left
   at your own word, so nothing is removed unless you tick it.
4. **Confirm the review**, then **type `abscond my hive`**.

Anything refused or declined exits 1 having changed nothing.

With `--password-stdin` the password is the first line of stdin and each answer is the next one,
in the order above.

## What it does

- Every Virtual Cell this Hive's id labels is destroyed, dormant ones included. A Night Veil Cell is
  purged with it.
- Every open Hive Stand lease is released (its scratch removed, its processes killed) when no Queen
  looks to be running. Otherwise it gets a RELEASE order her tick carries out through its Warden. A
  device's lease always gets a RELEASE order, which the next Queen sends to that device's Warden
  when it attaches.
- Every grant on a destroyed Cell is revoked.
- Temp dirs are removed, resident Basket rows evicted, and the Leavings you ticked are put back as
  they were before the bee wrote them.
- `guard.absconded` is recorded, as `human`, with your reason, how you confirmed (`reauth`,
  `phrase`, `scope_review`, `leavings_ticked`), what the scope held and what each step did.

Persistent stores are kept: the trail, the Brood Chamber, Honey, the ledger and the Entrance
tables. Delete the Hive's directory yourself if you want those gone too.

## Afterwards

The receipt ends with `left as found`. It is `True` when a fresh read finds no Virtual Cell and
no open lease. When it is `False`, a lease was deferred to a Queen or its scratch was already gone.
Start the Hive once (`hive run`) to let the Queen carry out the RELEASE orders, then run `hive
abscond` again.
