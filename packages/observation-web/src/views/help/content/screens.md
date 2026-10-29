How to read each screen. Every screen opens with one sentence saying what it shows; that sentence
is also the nav item's hover text, and it links here.

## The shell

The top bar holds the Hive's name, the connection word (`live`, `connecting`, `reconnecting`,
`offline`), the theme toggle (light or dark, remembered on this browser) and, from tablet width,
the device's name, which listener it came in through, and Log out. On a phone the device line and
Log out sit in the nav's More panel.

The summary strip under it never scrolls away: active bees; Cells active and inactive; Real and
Virtual Cells; and bees thinking by model against by rule. A number that just moved carries a short
underline. If a stream drops, the strip says `stale since` a time and mutes its numbers.

The notice bar appears under the strip while something is waiting for you: a question, an Alarm, a
reply, a finished goal or a security event, each with a link to where it is.

The nav is a bottom bar of five items on a phone, with More for the rest, and a rail from tablet
width. The current item is bold with a rule beside it.

## Fleet

Every Cell in one table (cards on a phone, a table from a wide screen): name and id, kind and
source, status and mode, the Comb Shield pill and the Pheromone Mask tag, access level, where its
Warden runs and where its models come from, current task and role, open Alarms, the highest live
Cell Wax severity, lease age, and links to its page, its Warden's Attendant and its bees' thoughts.

The Real / Virtual / All filter is remembered per browser. Click a column header (or use Sort by on
a phone) to sort. While your pointer or focus is in the table, new rows wait behind a "re-sort"
button instead of moving the list. The legend above the table keys the three Comb Shield levels and
is never collapsed.

## Cell page

One Cell, reached from its Fleet row. The header leads with the Cell's name, then its id, kind,
source, status and both badges, and stays under the strip as you scroll. Below it the live diagram
draws the Warden and each sub-bee as a comb cell: role, what it is doing now, a status word and a
context gauge; beside the tree, the session, the Exoskeleton, the lease or the Virtual Cell's
backend, and where each model slot is hosted. Click a bee to open its thoughts.

Then the Cell's tasks and goals; the Forage its Warden holds (a gauge per grant, used against
issued, and pending requests); a link into the Cell's Honey folder; access level and mode with the
last Patrol; Cell Wax with severity, proposer and expiry; open Alarms; and Capping activity for this
Cell.

## Forage

The flow at the top draws the Queen's shared pool (the Royal Reserve hatched, then granted, then
free), one bar per Warden's grant filled to what it used, and one bar per sub-bee's spend, joined
by ribbons; the unit switch (model seats, spend per hour, cores) changes the whole diagram. A grant
near expiry says `soon` under its name. Below it, per-host capacity: in use, reserved and
available on the Hive Stand, each Nuc and each Virtual Cell backend, with one legend for the four
kinds.

Then hosted spend against the cost caps, the Wardens' grants, pending requests with their reasons
and the Queen's decision when it lands, and the Forage map as a table: source, grade, measured
grade, distance from each Cell, abundance and cost.

## Thoughts

A bee's episodes, newest first. Each card shows its trigger, the assembled context by section (each
collapsed, expandable), the reasoning summary when the provider gives one, the decision and the
action in words. For the Queen the screen also shows her Attendant's current ordering of her inbox
with scores, and each autopilot decision as a slim row: what she decided, the action, the reason
and what it concerned.

The Bee select in the title row reaches any bee: the Queen, then each Cell's Warden and its bees.
Follow appends new episodes at the top as they arrive; off, the scroll position is kept. Secrets
and screenshot bytes are redacted at the source and show as `[redacted]`.

## Attendant

What is waiting on the Queen (`#/attendant/queen`) or on one Warden, in priority order, and the
task graph it is working through: a layered, left-to-right graph where a task node shows its id,
its status word and its Cell, and a task with open Alarms says so in words. Beside it, what
recently finished, what comes next, and the ordered inbox with each item's score and placement.
Reached from a Fleet row or a Cell diagram.

## Chat

The conversation with the Queen, oldest first, with the composer under it. A message only talks
to the Queen; switch on "Request as a goal" to ask her to plan and run work, and the button reads
"Request goal". Ctrl or Cmd with Enter sends; the hold-to-talk button sends a clip instead, and a
spoken goal shows its transcript with Confirm and Decline before anything runs.

A question from the Queen shows its options as buttons (or a text field); an Alarm shows
Acknowledge. Once answered on this device the line keeps what you said; one settled elsewhere says
"No longer waiting." Each line links to the task and the episodes it produced. The page opens on
the newest line and follows new ones unless you have scrolled up to read.

## Honey

The Hive's knowledge as folders: `/hive` (the shared store), `/cells/<id>` (what that Cell's Warden
can see, with its live Cell Wax under `/wax`), `/bees/<id>`, `/tasks/<id>` and `/bee-bread`. Every
item shows its clearance tag and its provenance. An item's title opens it in the reading pane
(beside the list on a wide screen, in place of it on a phone), at an address you can share. A
searchable folder has a search box; a search is explicit, since it can cost an embedding call.

What you see follows your device's capabilities: a folder you may not see is absent from its
parent, and opening it by address shows the refusal.

## Cost

A strip of totals (total spend, hosted spend, the local saving), then three sortable tables: spend
per goal split by slot and provider, spend per slot with what moving it local would save and a
gauge of its hosted share, and spend per provider. Money is two decimals, four only below a cent.
A call that named no goal sits in the unattributed row, last.

## Capping

Rates per tier at the top, then the Hive-wide queue of proposals: tier, state in words (Proposed,
Checking, Approved, Applied, Verified, Refused, Rolled back), the bee, the Cell, the failing check
and the judge's verdict with its reasons. Filter by tier and state with the chips (a select each on
a phone); sort by header. Every item links to the bee's thoughts. Sampled-audit findings have
their own table.

## Devices

Every device enrolled on the Hive: name, platform, capabilities in words, spend cap, expiry, last
seen and its standing. On the Hive Stand the row's controls edit capabilities, revoke or unlock the
device; elsewhere they are absent. The Remote access panel says the Entrance's mode in words and,
on the Hive Stand, offers to reduce it to the Hive Stand only, behind a second tap. The step-up
prompt appears here when an action needs it.

## Enrol

Only on the Hive Stand. Create invite mints a code and a QR with one line on what to do with them;
the pending list shows each device that redeemed an invite, with Approve (name, capabilities as
labelled boxes, spend cap, expiry) and Deny. A remote session that opens this address sees "Not
found".

## Help

This screen. The pages on the left (above the text on a phone): start here, the words, how the
Hive works by theme, and the screens. The search box finds any word or phrase across every page as
you type, headings first. Take the tour walks the shell and the main screens one step at a time;
Next, Back and Skip tour move through it, the arrow keys and Escape do the same, and it remembers
on this browser that you finished it.
