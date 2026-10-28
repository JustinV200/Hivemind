# observation-web/src/views/chat

The Chat view, `#/chat`: the human's side of the Queen's (the central orchestrator's) inbox, and
the one write path of the Observation Hive. It shows the conversation in order (the human's
messages, and the Queen's replies, questions and Alarms), each line with its time, its kind as a
word and links to the task it concerns (`#/attendant/queen`; a human message also links to
`#/thoughts/queen`, where she reads it). A waiting question has its answer form under its line
(buttons for a closed question, a text field for a free one), and an Alarm that reached the human has
Acknowledge; both post to the inbox routes and disappear on success. The composer is docked to the
bottom, above the nav on a phone: a text box and Send (the one filled accent button), and a
"Request as a goal" switch (off by default; on, the button reads "Request goal" and the words go
to `POST /v1/goals` instead of the chat, with the Confirm and Decline controls a held goal has, and a
line linking to `#/attendant/queen`), and a hold-to-talk button that records with the browser's recorder, posts the clip to `/v1/chat/audio`
and shows the transcript, with Confirm and Decline while a spoken goal is held. A `403
step_up_required` shows a password prompt and retries the write once. A device without
`entrance:submit` is told so and gets no composer. There is no break-glass control: none is built
in this step.

`index.ts` is the face (`ChatScreen` and `chatSlice`); `chat.tsx` keeps the old `ChatView` name as
an alias for the outlet. `chat_screen.tsx` is the route component.

- `slice/`: state and events (`state.ts`), the reducer (`reduce.ts`: log order, bounded to 500
  lines, pending messages settled by line id), selectors joining a line to its inbox item.
- `reads/`: `use_chat.ts` seeds from `GET /v1/chat` and `GET /v1/inbox` once and re-reads the inbox
  once per live question or Alarm line (never on a timer); `chat_stream.ts` is the stream wiring,
  resuming with `?after=<seq>`.
- `writes/`: `use_actions.ts` is every chat, inbox and voice write; `use_goal.ts` is the typed goal
  request; `use_step_up.ts` and
  `step_up_prompt.tsx` are the step-up flow.
- `conversation/`: the list, a line, a pending message, a requested goal (`goal_row.tsx`), the question and Alarm forms, the links,
  the "Waiting for you" block for inbox items whose line has not arrived, and `use_follow_tail.ts`,
  which opens the page on the newest line and follows a new one there unless the reader has
  scrolled up to read.
- `composer/`: the text box and Send. `voice/`: push-to-talk, its state machine, the transcript card
  and the goal controls.

Known limits: the stream opens without a cursor on the first connection (the Entrance then sends
only lines written after it accepted), so a line written between the seed read and that moment
reaches the view only if it is a question or Alarm (the inbox shows it under "Waiting for you");
older history than the newest 100 lines is not paged in; the `max_clip_seconds` cap is the Hive's
default of 60 seconds because the document does not publish it; and the withdrawn-everywhere push
is not consumed, because the shell exposes no push notice yet.
