# ADR-0032: GUI actions are typed proposals the gate applies through an injected surface, recorded frame by frame, and rolled back by checkpoint

- Status: Accepted
- Date: 2026-09-24

## Context

Codingrules 8.12 says nothing lands uncapped and the proposer never verifies its own work, and it
singles GUI work out: "recorded, not trusted", structural assertions over pixels, and rehearsal
before a browser procedure is reused. The gate (ADR-0018) applies DIFF, COMMAND and COPY actions
itself through the Cell's session; `ACTION_SEQUENCE` existed as prose steps with no applier, so
every GUI proposal was rejected. Capping lives at Layer 2 and the Exoskeleton at Layer 3, so the
gate cannot import it.

Rollback is the hard part. A Docker snapshot rollback recreates the container, which kills the
in-Cell Warden that asked for it (ADR-0027); per-click snapshots would also cost seconds each. A
click cannot be un-clicked, but most of what a click changes in a browser (the page, its cookies,
its local storage) can be put back. And the existing rule of three rollbacks before an Alarm
(`ROLLBACKS_BEFORE_ALARM`) fits file writes a bee retries, not a GUI whose state no longer matches
what the bee believes.

The Honey Store, where Nectar lives, is phase 7. Phase 4 set the precedent that until then Nectar
is a Bee Bread entry, but Bee Bread payloads are bounded text and a recording is frames.

## Decision

**GUI actions are a new `ActionKind.GUI` carrying typed `GuiStep`s (waggle 1.6).** A step is one
of: pointer move, click, double click, type text, press keys, scroll (desktop); navigate, click an
element, fill an element, press keys (browser); say a clip (audio). Browser elements are named by
role and accessible name, label, visible text or CSS selector, in that order of preference. Typed
text carries a `secret` flag. `ACTION_SEQUENCE` stays the prose form for devices.

**Three more postcondition kinds, checked by the gate.** `URL_MATCHES` (exact, or a prefix ending
in `*`), `ELEMENT_TEXT` (now implemented: the element's text contains the expected text) and
`REGION_CHANGED` (a screen region's pixels differ from before the action). Each is an eventual
assertion polled for a bounded settle time, because a page reacts after the click returns.

**The gate applies GUI actions through an injected `GuiSurface`** (a Layer 2 protocol in
`supervision/capping/gui.py`, implemented by the Exoskeleton handle, injected by the Warden like
the `Snapshotter`). With no surface a GUI proposal is rejected before any check runs. The
allowlist rung requires `exoskeleton:display` for desktop steps, `exoskeleton:browser` for
browser steps, `exoskeleton:audio` for speech and `net:<host>` for navigation. After any snapshot
the tier asks for, the gate takes a surface checkpoint (restorable browser state, the before frame,
the region digests the postconditions name), applies, verifies, and on failure rolls back: by
snapshot when one was taken, else by restoring the checkpoint (`RollbackMethod.GUI_STATE`: the
page URL, cookies and local storage), else not at all. The gate then tells the surface the
terminal outcome so the recorder can close the entry.

**The tool declares the tier from what the action reaches.** Desktop input on a display the lease
started is `scratch_write`; any input on a display that was already running is `device_command`;
navigation, and browser actions on a page that is not loopback, are `network_egress`; a step the
bee says is irreversible is `irreversible`. Checks may raise a tier and never lower one.

**A failed declared postcondition on a GUI action raises an Alarm at once**
(`POSTCONDITION_FAILED`), not after three: every later step would act on a screen the bee has
misread. The Alarm names the recording, never a frame.

**Acceptance can be structural.** A subtask that needs the Exoskeleton may state `URL_MATCHES` and
`ELEMENT_TEXT` acceptance criteria; the planner refuses them on any other subtask. The Warden runs
them through the attached handle before it detaches.

**The flight recorder keeps evidence, not a story.** One `FlightRecording` per attach; every GUI
proposal adds a `RecordedAction`: its steps (secrets redacted), the before and after frames, the
accessibility snapshot and URL before and after when a browser is attached, the declared
postconditions with their outcomes, the terminal state and the rollback method. Frames are PNG
bytes in a `RecordingStore` (two tables in the Hive's SQLite file), never in a log, a trail
payload or a tool result's text. Until phase 7's Nectar intake exists, the store is the Nectar body
and a Bee Bread entry of kind `RECORDING` references it with the recording's clearance, which is
how an episode record reaches it. A Night Veil Cell's recordings are purged with the Cell.

**Redaction happens where the evidence is made.** Secret text is recorded as a length; step text
and snapshots are scrubbed for credential patterns; credential-looking URL query parameters are
masked. Pixels are not scrubbed: browsers mask password fields, and a model never types a secret
the plan did not hand it.

**Judges read recordings.** An `irreversible` GUI proposal is reviewed on `ModelSlot.JUDGE` after
it is applied and before the tool returns, so before the bee's next step: a vision-capable judge
sees the before and after frames, any other judge sees the structural evidence. A rejection raises
an Alarm and ends the attempt. Every other tier is sampled after the fact at its audit rate with
the same evidence (ADR-0018's audit path).

**Reusable browser work is rehearsed.** A verified recording can be exported as a
`BrowserProcedure` (its typed steps and postconditions) and replayed against a fixture site; the
`RehearsalReport` is what the Royal Jelly Lab (9.3) requires before promoting the procedure as a
tool, so production runs execute a capped procedure rather than an improvised one.

## Consequences

Positive: every click, keystroke and navigation is proposed, checked, applied by the Warden's gate,
verified by it and recorded; a wrong click is caught by the bee's own declared expectation, undone
where undoing is possible, and escalated. Browser rollback is cheap and never kills the Warden.
Weak models can drive browsers with structural assertions alone.

Negative: a checkpoint restores client-side state only; a form that reached a server stays
submitted, which is exactly why a step the bee knows is irreversible is judged. Desktop actions
outside a browser have no checkpoint, so they roll back only by snapshot. Recordings cost disk (a
1280x800 PNG is tens of kilobytes); retention is a manifest setting. Waggle grows by one action
kind, one step model, three postcondition kinds and one rollback method.

## Alternatives considered

Letting tools act and only record: the bee would verify its own work, which 8.12 forbids. A
snapshot per GUI proposal: seconds per click, and on Docker it recreates the container out from
under the in-Cell Warden. Steps as JSON inside the prose `steps` field: no wire change, but every
reader would parse strings the protocol cannot validate. Frames as base64 Bee Bread payloads:
bounded text was never meant for images, and phase 7 would have to migrate them anyway.
