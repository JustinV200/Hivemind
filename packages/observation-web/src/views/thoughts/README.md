# observation-web/src/views/thoughts

The Thoughts view, `#/thoughts/queen` and `#/thoughts/<bee>`: one bee's episode records, newest
first, kept live by the episodes stream, with a Follow switch (on by default; off, new episodes wait
behind an "N new" button). Each episode shows its trigger, decision, action and reasoning summary,
and the rest of its record (the prompt it refers to, the trigger's detail, the usage) in sections
that open in place. For the Queen the view also shows the Attendant's current ordering of her inbox
with scores, and her autopilot decisions in the same time order, drawn apart from episodes.

`slice.ts` is the view's slice and reducer (bounded to 500 episodes, a principal filter, the Follow
queue, page merge by episode id); `use_thoughts.ts` reads the first page and "Older";
`thoughts_view.tsx` is the route component; `episode_card.tsx`, `decision_row.tsx`,
`inbox_panel.tsx` and `follow_toggle.tsx` draw the pieces; `timeline.ts` merges episodes and
decisions. The view is read-only and renders only what the generated types carry. Secrets and
screenshot bytes are redacted by the Entrance before they reach it.
