# observation-web/src/views/enrol

The Enrol view, `#/enrol`, on the Hive Stand only (the session's `listener` is `loopback`; any other
session is shown "Not found" and the route makes no request): let a new device in. The operator
names a device and creates an invite (the Entrance's word is "mint"; the button says "Create"),
which is shown as a code, a link with a Copy button, a QR code, the time it lapses and one line on
what to do with it, and can be withdrawn. When the device has redeemed the invite and made its
passkey it appears under "Waiting to join" with the fingerprint of its key, live through the
Entrance's security stream; Approve opens a form (name, capabilities, daily spend cap, expiry) and
Deny refuses it. When the Hive runs its own certificate authority an approval can answer with a
sealed certificate bundle, shown once with its passphrase. Below that, "Waiting to use your screen
or sound" lists the tasks the Queen holds until the owner allows them to drive the running screen or
audio (ADR-0069): each card says what it asks for, on which machine, for which task and bee, and
when it is refused unanswered; Allow lets it start, Refuse cancels it.

`slice.ts` is the view's slice (the waiting devices, the invite just minted, the read's error and a
count of security frames); `use_enrol.ts` seeds it from `GET /v1/entrance/pending` and reads again
once, a second after the first frame of a burst, never without a frame (it reuses `devices/live/`);
`use_enrol_actions.ts` holds the writes (mint, withdraw, approve, deny, and `answerWatch`), each
under the shared
step-up of `devices/step_up/`; `enrol_screen.tsx` is the route component (`index.ts` exports it as
`EnrolScreen`, and `enrol.tsx` keeps the old `EnrolView` name for the shell's outlet); `invite/` has
the mint form, the card and the QR image (an `img` over a data URI, never injected markup);
`pending/` the waiting list, the approval form with its expiry choices and the bundle notice;
`watch/` the watch list and its read of `GET /v1/entrance/watch` (on mount, after an answer, and on
"Check again"; nothing polls).

`join/` is not part of the screen above: it is the join screen a new device lands on when it opens
the Entrance with an invite, on any listener and before any session exists. The login screen shows
it (`src/views/login/`). It reads the invite from the page's address, makes the device's passkey,
redeems the invite, remembers the device id, and then waits for the operator; the human taps
"Check again" to ask whether it has been approved (nothing polls), and on approval goes on to log
in.

Not in the schema, so not shown: the invite's remaining time as a countdown (only the expiry
moment is given), and anything about a bundle past the moment it is answered.
