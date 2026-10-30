No bee's work is trusted on its own say-so. Beekeepers cap a honey cell only once the honey is
ripe; in the Hive, work stays provisional in scratch until it is capped. The
[Capping screen](#/capping) is the gate's queue.

## Propose, then commit

Every action with a side effect outside a lease's scratch folder is a **proposal**: what will be
done, its risk tier, and the outcome the bee expects to hold afterwards. The gate runs the checks
that tier needs, applies the change, verifies the outcome, and rolls the change back if the outcome
does not hold. Each proposal moves from proposed through checking to approved, applied and
verified, or to refused or rolled back; the queue shows those words on every row.

## Risk tiers

A tier says what the action would do, from the least to the most dangerous: only reads, writes
inside its scratch folder, writes outside it, talks to the network, spends money, commands a
device, cannot be undone. A table maps each tier to its checks, and adding a tier or a check is a
table edit, not code.

## Layered checks, cheapest first

Deterministic checks run with no model at all: schemas, lints, allowlists, size caps. Then tests in
a sandboxed Cell. Then, for riskier tiers, an independent **judge**: a model on its own slot with
no shared context with the proposer, which the manifest may pin to a different provider so blind
spots do not correlate. Then, for the irreversible, a human. A task's tempo can shorten the ladder
for low-risk tiers and lengthen it for critical ones, but never below a tier's floor.

## Someone else checks: the Nurse Bee

Outcomes and acceptance criteria are verified by the bee's Warden or a judge, never by the bee that
did the work. After the Warden's own deterministic acceptance checks pass, the **Nurse Bee** reviews
the attempt in a fresh context: the objective, the criteria and their outcomes, the actions of the
attempt's verified proposals, and bounded reads of the files they touched, assembled by the Warden
and never from the Worker's own account of itself. It runs on the judge slot, on a model at least as
strong as the Worker's, preferring a different provider.

A pass accepts the attempt. "Wants changes" fails it, and the Queen retries on a fresh Worker on
the same Cell with the findings; after the manifest's number of rounds she escalates to you with
the Alarm "the Nurse Bee still wanted changes after the last fix round". When no model strong
enough is available the attempt is accepted and marked as not nursed, on the trail.

## Snapshots and rollback

On a Virtual Cell the gate snapshots the whole machine before an irreversible or device-command
proposal and rolls the Cell back when the outcome fails. Real Cells get no snapshot; what a lease
started is undone when the lease is released.

## What cannot be gated is sampled

Low-risk work is audited after the fact at a sampling rate per tier by the judge; findings become
Honey and Alarms, and the Guard Bee raises a tier's rate when its failure rate climbs. The Capping
screen's audits table lists those findings and the rates per tier.

## The Guard Bee and isolation

The Guard Bee runs in the Queen's process and never touches a Cell. Its rules are data: denial
rates, writes outside scratch, over-grant spend, unexpected network attempts, rejection and
rollback rates, failed audits, suspected prompt injection, and trouble at the Entrance. It files a
request; only the Queen decides to isolate a Cell or quarantine a bee, and only you lift an
isolation, with step-up. Memory an isolated Cell produced is marked tainted and never reaches a
prompt again until a judge clears it.

## Guard rails in this app

- This app has one write path: the chat into the Queen's inbox (a message, a goal, an answer, an
  acknowledgement). No screen calls a store, a Warden or a Cell directly.
- Sensitive actions need **step-up**: the password and the device key again, within a short
  window. Spend above a cap, device and capability changes, Supersedure and reopening a reduced
  Entrance all ask for it.
- Approving, widening, unlocking or revoking a device exists only on the Hive Stand's own listener,
  with the stand key. A remote session never sees those screens.
- A task that would see and use your own screen, or hear and play sound, waits for your yes on
  the same listener (a [watch request](#/help/glossary?s=watch-request) on the Enrol screen);
  unanswered for fifteen minutes it is refused, and no goal can grant either scope itself.
- Break-glass actions (Absconding, Sting Cut, Supersedure) are never one click and are pulled
  from the command line, not from a page.
