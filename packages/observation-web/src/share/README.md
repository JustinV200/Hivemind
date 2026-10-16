# observation-web/src/share

The phone host: the parts of the Observation Hive that exist only inside the Android shell
(ADR-0050), each offered by an optional member of `Browser` and rendering nothing without it.

- `phone_host.tsx` mounts the pieces below, beside the login gate; `local_error.ts` makes a refusal
  made on the device draw like the Entrance's.
- `intake/`: the share sheet as an intake. `use_shared_items.ts` queues what other apps share (in
  memory only, so an item shared while logged out waits for login), `share_card.tsx` asks the human
  to confirm, and `send_item.ts` sends text through the chat's `send`, audio through its `speak` and
  an image through `POST /v1/nectar/images`. `media.ts` maps media types; `clip_length.ts` reads a
  clip's length from the WebView.
- `waiting/`: the ongoing "a question is waiting" notification. `use_waiting.ts` follows the push
  stream and re-reads the inbox each time the stream comes back (the stream keeps no replay);
  `waiting.ts` is its reducer.
- `setup/`: `push_card.tsx` offers push notices through the UnifiedPush distributor, and
  `invite_paste.tsx` takes the invite link by hand when the App Link did not open the app.
