# observation-web/src/views/devices

The Devices view, `#/devices`, on every device: who is enrolled and what each may do, live. The
list shows each device's name (marked when it is the one in your hand and when it is the Hive
Stand's console), its standing as a word, the kind of key, what it may do, its daily spend cap,
when its standing lapses, when it last authenticated and the platform it reported. Under the list
sits the Entrance panel: the mode, the Entrance Reducer, and the requests held for a person with
Confirm and Decline. Reduce is open to any device (narrowing access is always safe); the held
requests too. On the Hive Stand only (the session's `listener` is `loopback`) each row also offers
Lock, Unlock, Revoke (after typing the device's name, with an option to cancel its open goals) and a
capability editor, and a reduced Entrance offers Reopen. Anything the Entrance answers
`403 step_up_required` opens the shared step-up prompt and is retried once.

`slice.ts` is the view's slice (devices, mode, held requests, one error per read, and a count of
security frames); `use_devices.ts` seeds it from `GET /v1/devices`, `/v1/entrance/mode` and
`/v1/entrance/confirmations` and reads again once, a second after the first frame of a burst on
`/v1/entrance/stream`, never without a frame (`live/`); `devices_screen.tsx` is the route component
(`index.ts` exports it as `DevicesScreen`, and `devices.tsx` keeps the old `DevicesView` name for
the shell's outlet); `list/` holds the table, its rows, the revoke form and the capability editor;
`entrance/` the Entrance panel and the held list; `actions/` every write the view makes, as one
hook; `step_up/` the step-up the Enrol view shares: a hook that asks again and retries once, the
in-place prompt and the standing panel the shell's banner links to.

Not in the schema, so not shown: the network address a device last connected from (the Landing Board
records `last_seen_at` and the self-description only), so the list says when and what platform, and
does not claim where from.
