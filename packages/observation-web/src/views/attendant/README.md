# observation-web/src/views/attendant

The Attendant view, `#/attendant/<principal>`: the inbox triage every supervisor runs, shown for
the Queen (`queen`, every task she is concerned with) or one Warden (its sub-bees' tasks and its
own inbox). It is reached from the fleet list and the Cell diagram, which link a Warden here.

The heading names the principal, with a link to its thoughts and, for a Warden, to its Cell page
(taken from the Cell one of its own tasks is placed on, since the schema does not name it). The
**task graph** is a live SVG in dependency layers: left to right from 720 px of room, top to bottom
below it. Each node shows the task id, a status word beside a status colour, the placement Cell and
a badge counting open Alarms, and links to `#/thoughts/<warden>` when it names a bee, else to the
task's Cell page. Lines run from a task to the task that waits for it. A hidden table says the same
in words. Beside it: the **inbox** in the Attendant's order (rank, score, kind, severity, id, task,
reasons; a note when the Entrance says it was reconstructed from durable state), **recently
finished** and **up next**.

Seeded by `GET /v1/attendant/{principal}`. Kept live by `/v1/tasks/stream`: a frame for this
principal moves the node's status and placement at once, and the first frame of a burst makes the
view read once more, a second later, for the inbox order and the two lists (no frame, no read).

`slice.ts` is the reducer; `use_attendant.ts` the seed, the stream and the re-read; `attendant.tsx`
the screen (`AttendantScreen`); `header.tsx` the title row; `attendant.css` the styles. `graph/` is
the SVG: `layout.ts` (layers and geometry, pure), `task_graph.tsx` and `task_node.tsx` (drawing),
`status.ts` (tones, words, links), `graph_table.tsx` (the table for a screen reader) and
`use_graph_mode.ts` (wide or narrow by the container's width). `lists/` holds the inbox and the two
short task lists.

Read-only, and it renders only what the generated types carry. The schema has no subject line for
an inbox entry and no title for a task (`TaskNode` carries ids, role, status and placement), so the
view shows ids; it names no Cell for a Warden that has no placed task.
