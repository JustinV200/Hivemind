# Observation Hive design

This is the layout and colour document every Observation Hive view follows (roadmap 12.2). The
stack is in ADR-0049; the rules are codingrules 8.11 and 8.15. The words are the README's: Queen,
Warden, Cell, Forage, Honey and the rest are used literally in code, in the UI and here.

## 1. Principles

- **Everything is live.** A view subscribes to its streams and redraws from its slice. It never
  polls and has no refresh button. A dot in the top bar says whether the streams are connected.
- **Everything is readable.** Any bee's thoughts, any Cell, any grant, and the Honey the viewer's
  scopes allow. Nothing important sits behind a hover.
- **Read-only, except the chat.** The chat is the one write path (8.11). Every other control is
  navigation, a filter, a sort or "follow", and the chat's send button is styled unlike them all.
- **Tier and mask are never hidden.** `CombShieldLevel` and Pheromone Mask state are persistent
  badges on every Cell row and Cell page header, with a fixed legend in the fleet view.
- **Phone width up.** The design starts at 360 px and adds columns as room appears. The page never
  scrolls sideways.
- **Deterministic.** The same slice draws the same pixels. No random layout.

## 2. Shell

From top to bottom, on every screen after login:

1. **Top bar** (48 px, one row at every width, drawn on the chrome tokens of 3.1). Left: the
   brand mark (3.4) and the Hive's name. Right: the connection dot (section 4.4) with its word
   (`live`, `connecting`, `reconnecting`, `offline`; the dot pulses while connecting or
   reconnecting); the theme toggle (a sun or moon with the word `Light` or `Dark`, which stores
   `data-theme` on `<html>`); from 720 px the device name and the listener in words (`on the
   Hive Stand` for the loopback one, `remote` otherwise, with a `title` saying what it decides)
   and a log out button, which on a phone move into the nav's More panel. A session
   that `needs_step_up` shows a banner under the bar until the step-up prompt is done.
2. **Summary strip** (68 px from 720 px, 116 px on a phone; never scrolls away, never hidden by
   navigation). Four groups, each drawn as a shallow comb cell (3.4) with its label above and
   its number below in bold, 20 px on a phone and 24 px from 720 px, where a cell is wide and one
   row deep, sharing the band equally up to where the content stops (the rail plus the 1440 px
   cap), with
   the stale note at the band's right edge; a number that just moved carries a 2 px accent
   underline for five seconds:
   - `Bees` active bees (total).
   - `Cells` active / inactive.
   - `Cells` real / virtual.
   - `Thinking` by model / by rule (bees in awake episodes versus autopilot handling).

   The hint beside each label is one 12 px line on a phone, so the sentence that explains it
   (`bees deciding with a model vs by rule (autopilot)`) rides on the cell as its `title`.

   It is a slice seeded by `/v1/summary` and kept live by the cells, telemetry and episodes
   streams (ADR-0049). If a stream is down, the strip says `stale since hh:mm:ss` and sets its
   numbers in `--text-muted` (7.4:1, section 3.1) rather than showing old numbers as if they
   were current; it is never dimmed, for the reason item 4 gives. On a phone the note is a third
   line of the band and the four cells give up 2 px of padding each, so the band keeps its
   116 px and the head does not grow. Under it a **notice bar** appears while the
   push stream (`/v1/push/stream`) says something is waiting (a question, an Alarm, a reply, a
   finished goal, a security event): one line per kind with a count and a link to the view that
   holds the item; opening the chat clears the chat kinds, Dismiss clears the rest.
3. **Nav.** Fleet, Forage, Thoughts, Attendant, Chat, Honey, Cost, Capping, Devices, Help. `Enrol`
   is added, before Help, only when the session's `listener` is `loopback`. Each item carries its
   view's one-line gloss (section 8) as its `title`, so a newcomer can tell Fleet (machines) from
   Forage (capacity) from Capping (the change gate) before opening them. The nav is drawn on the chrome; the
   current item is bold in the chrome accent with a 3 px rule beside it and `aria-current="page"`,
   never colour alone. As a rail it starts where the head ends, whatever the head holds: the shell
   measures the head (bar, banner, strip and notice bar) and the rail, the Cell page's sticky
   header and the Honey reading pane follow that height (`--head-h`), so a waiting question is
   never hidden under the rail.
4. **Main area.** One view. A view has a title row (title, then its filters, then under them its
   one-line gloss, the same sentence the nav item carries, from `components/words`), then its
   body. While any stream the screen could be drawing from has dropped, the title row adds
   `stale since hh:mm:ss, reconnecting` in the warn tone, so rows drawn from the last frames are
   never taken for current ones. Neither the main area nor the strip is dimmed: at any opacity
   that still reads as dimmed, `--text-muted`, `--accent` and the status tones fall under the
   4.5:1 of section 7 on the page and the comb surface (0.9 leaves `--warn` at 4.1 on
   `--surface-2`), and the note already carries the state in words.

| Width | Shell |
|---|---|
| 360 to 719 | One column. Nav is a bottom bar of five icons plus a "More" item (which reads "Close" while open) for the rest, the device line and log out; the summary strip stays under the top bar and wraps to two rows of two cells, each two lines (the label with its hint on one 12 px line, the number under it) in a 46 px cell, so the band holds the 116 px `--strip-h` budget, stale note included, and the pinned head (bar and strip) stays under a quarter of a phone's screen. Tables become cards (one row, one card) and a sortable table gets one "Sort by" select in place of its header row; the Capping view's two filter groups become one select each. A link in a row, a card, a line of links or a breadcrumb has the buttons' 44 px touch area, as padding around its line. |
| 720 to 1079 | Nav becomes a left rail of icons with labels. A table with few columns (the Cost tables, the Forage's Wardens, requests and map tables) is a table; the rest stay cards, two across where there is room (the Fleet). Diagrams fill the width. |
| 1080 and up | Left rail with labels beside the icons, content up to 1440 px wide, two panes where a view has them (diagram beside its tables). Every table shows all its columns (the Grants table, with ten, from here), except the three whose columns need more room: the Fleet, the Capping queue and the Devices list become tables from 1280 px and keep their cards below it, so no table ever scrolls sideways. |

On a phone in landscape the nav hides on scroll down and returns on scroll up. The strip never hides.

## 3. Design tokens

All colour, spacing and type live as CSS custom properties on `:root` in `src/styles/tokens.css`.
The palette is the hive's: wax and comb for the surfaces, honey amber for the accent, propolis
(the dark resin bees seal the hive with) for the chrome, so the top bar and the nav read as dark
wood around cream content. Light is the default. Dark, the night hive, applies under
`@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme="light"])`, and again
under `:root[data-theme="dark"]`, so a stored choice (`data-theme` set on `<html>` by the theme
toggle) always wins over the system; there the chrome is the comb surface (`--surface-2`), one
step lighter than the page, so the rail and the bar still frame the content at night. A view
uses tokens only; a hex value in a component is a bug.

### 3.1 Surfaces, text, borders, accent

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#FBF7EE` | `#15120C` | page (wax) |
| `--surface` | `#FFFFFF` | `#1E1A12` | cards, tables, the strip's cells |
| `--surface-2` | `#F4ECDA` | `#29231A` | table header, strip, hover row, folder tiles (comb) |
| `--border` | `#8A7F66` | `#8C8268` | outlines of controls (3:1 or better on `--surface`) |
| `--border-soft` | `#D9D2C3` | `#3B3526` | card outlines and row dividers |
| `--text` | `#1E1A12` | `#F3EEE2` | body text |
| `--text-muted` | `#5C5546` | `#B5AD9A` | captions, units, ids |
| `--accent` | `#9A4A00` | `#F2B544` | honey amber: links, current filter, focus ring, the send button |
| `--on-accent` | `#FFFFFF` | `#1A1400` | text on a filled accent |
| `--accent-soft` | `#F6DFA8` | `#4A3500` | a fill only: the notice bar, a hovered folder tile; never a text colour |
| `--chrome` | `#3A2A14` | `#29231A` | the top bar and the nav (propolis in light; the comb surface, one step above the page, in dark) |
| `--chrome-text` | `#F3EEE2` | `#F3EEE2` | text on the chrome |
| `--chrome-muted` | `#CDBFA6` | `#B5AD9A` | the nav's resting items and the device line |
| `--chrome-accent` | `#F2B544` | `#F2B544` | the current nav item, with its rule and bold weight |

Contrast on `--surface`, light and dark: `--text` 17.3 and 15.0, `--text-muted` 7.4 and 7.8,
`--accent` 6.3 and 9.5, `--on-accent` on `--accent` 6.3 and 10.0; all pass 4.5:1 on `--bg` and
`--surface-2` too. On the chrome, `--chrome-text` is 11.9 and 13.4, `--chrome-muted` 7.6 and 7.0
and `--chrome-accent` 7.5 and 8.5. Shapes and motion: `--radius-card` is 12 px for every card and
panel, `--motion` is the one duration (150 ms ease) for hovers, inserts and the gauge's fill.

### 3.2 Status colours

Status uses four tokens, each used as text or a dot on `--surface` and always next to a word.

| Token | Light | Dark | Means |
|---|---|---|---|
| `--ok` | `#1B6B3A` | `#5FD38A` | working, connected, within budget |
| `--warn` | `#8A5A00` | `#F0B429` | near a limit, caution, waiting |
| `--alarm` | `#B3261E` | `#FF8A80` | failed, over, Alarm, Cell Wax `block` |
| `--idle` | `#5C6370` | `#9AA3B2` | idle, watch mode, nothing to do |

Contrast on `--surface` is 6.5, 5.9, 6.5 and 6.1 in light and 9.2, 9.3, 7.6 and 6.8 in dark; all
pass 4.5:1, on `--surface-2` too.

### 3.3 Badge tokens

Each badge has a fill (`-bg`) and a text colour (`-fg`), and is drawn with a 2 px border in its
`-fg` colour. The border is what separates a pale badge from a pale surface. Contrast is `-fg` on
`-bg`, all above 4.5:1.

| Badge | Light bg / fg | Ratio | Dark bg / fg | Ratio |
|---|---|---|---|---|
| `MEADOW` | `#D5EEC8` / `#17400C` | 9.5 | `#245A1B` / `#E4F8DC` | 7.4 |
| `PROPOLIS` | `#F4D675` / `#3F2A00` | 9.5 | `#6B4F00` / `#FFEBB0` | 6.5 |
| `NIGHT_VEIL` | `#26194F` / `#FFFFFF` | 15.7 | `#DCD3FF` / `#1A1040` | 12.4 |
| mask `OFF` | `#E6E3DB` / `#35312A` | 10.1 | `#3A372F` / `#E8E4D8` | 9.4 |
| mask `WARDEN` | `#BFE8E4` / `#06403D` | 8.8 | `#0F5A56` / `#D2F5F2` | 6.9 |
| mask `QUEEN_FORCED` | `#F2C4DB` / `#5E0B34` | 8.7 | `#8A2056` / `#FFE3F0` | 7.2 |

Token names are `--tier-meadow-bg`, `--tier-meadow-fg`, `--tier-propolis-*`, `--tier-night-veil-*`,
`--mask-off-*`, `--mask-warden-*` and `--mask-queen-forced-*`.

The three tier colours run green, amber, deep indigo, from open to closed; Night Veil is the only
one that inverts lightness, so it is the heaviest badge in either theme. The mask colours (grey,
teal, magenta) share no hue with them.

**Two rules keep the badges readable by anyone.** Colour is never the only signal: every badge
carries its text label (`MEADOW`, `PROPOLIS`, `NIGHT VEIL`, `OFF`, `WARDEN`, `QUEEN FORCED`), and
the two kinds have fixed shapes. **Tier is a rounded pill** (fully rounded ends, 12 px type, bold).
**Mask is a square tag** (2 px corner radius, 12 px type, regular weight, a small square glyph at
its left). A reader who cannot tell the colours still tells a tier from a mask by the outline, and
the words tell which. The same shapes are used in the legend, the table, the page header and the
diagrams.

### 3.4 The honeycomb

The hive shows itself through one shape, the comb cell, in seven fixed places and nowhere else.
Nothing is cartoon: no mascot, no stripes.

1. **The mark.** A hexagon outline holding a smaller filled hexagon (`Mark`), in the top bar before
   the name and on the login card; the same two hexagons, drawn on the propolis chrome, are the
   PWA icon (`public/icons/`).
2. **The comb pattern.** An SVG hex grid of 28 px cells, stroked in `--border` at 12 percent
   (`HexPattern`), behind the login card and behind an empty state (`EmptyState`), never behind a
   table or running text.
3. **The diagrams.** Each bee in the supervision tree and each task in the Attendant graph is a
   wide flat-top hexagon, the Cell frame a hex-cornered rectangle, the role glyph inside a small
   hexagon (`components/comb/shapes.ts`).
4. **The summary strip.** Its four groups are shallow hex-cornered cells on `--surface-2`, set
   4 px apart so they read as one row of comb rather than four boxes.
5. **The Honey browser's folders** and the strip share the one hex-cornered clip (`--hex-cut`, a
   10 px chamfer on all four corners) with no stroke, since they sit on the comb surface.
6. **The chrome's comb.** The same hex grid, stroked in `--chrome-muted` at 12 percent, fills the
   rail below its items and the top bar's empty middle from the rail width, so the comb is felt
   on every screen; it never runs behind an item or a word.
7. **Panels and cards.** Every panel and card of the working views (a Cell page panel and its
   diagram, a thought, a rate card, a cost figure, the Forage flow and host cards, the Attendant's
   panels, the Honey reading pane, the login card, the device, enrolment and spoken-goal forms) is
   a comb cell (`.comb-cell`): the same four-corner 10 px cut as the strip's groups, with a 1 px
   stroke that follows the cut. The stroke is a ring drawn under the clip (`--hex-ring`), never a
   CSS border, which would stop at each chamfer and leave the corners open; a panel is stroked in
   `--border-soft`, a form in `--border` like its fields (`comb-cell--strong`). The Fleet's table
   box (from 1280 px, where its rows are a table) and the empty state (`EmptyState`, the words on
   the faint comb) are panel-sized containers, so they are comb cells too. A row card (a Fleet
   card, a chat line, a Honey item, a table row folded into a card) keeps the 12 px radius, so a
   comb cell always means a panel and a rounded card a row; nothing mixes a chamfer with a
   radius. The Fleet's legend is a control-sized strip and keeps the 6 px control radius, like
   the chips and buttons beside it.

Every view title is led by a 10 px hexagon in `--accent`, the one recurring glyph; everywhere else
the comb is implied by the warm palette and the 2 px radius on tags.

## 4. Components

Each is one file under `src/components/`, takes typed props from the generated types, and holds no
state beyond focus, open/closed, a shared clock tick (`TimeStamp`) or a caught failure
(`ViewBoundary`, which wraps every view so a view that throws shows the error notice in its place
while the shell stays).

### 4.1 Badges

`TierBadge` and `MaskBadge` take the `CombShieldLevel` or mask state and, for a mask, its source.
A mask shows `WARDEN` or `QUEEN FORCED`; `OFF` shows as `OFF` in the grey tag and is still
drawn, because "no mask" is information. A Queen-forced mask also shows its expiry beside the tag
(`until hh:mm`) in `--text-muted`. A badge is never a button and has no hover-only content.
`Legend` renders the three tier pills and the three mask tags with one line each of what they mean.

**The tag** is the one short label beside a name in every view, and it is a class, not a
component: `.tag` (`components.css`) is the Fleet's quiet look, the comb surface (`--surface-2`)
with a soft outline (`--border-soft`) and the 2 px radius, for a kind (`Message`, `Awake`, `C1`,
`trigger tick`), a place (`this device`, `Hive Stand`) or a request (`Goal request`), so a static
word never looks pressable beside a real button (which is outlined in `--border` on `--surface`).
A status word (a proposal's state, a verdict, a severity, a taint, a waiting `Question` or
`Alarm`) is `.tag--status`: bold, outlined in its tone (`.tag--ok`, `--warn`, `--alarm`, `--idle`),
with the word carrying the meaning and the colour only underlining it. No view defines a tag of
its own.

### 4.2 Gauges

`ContextGauge` is a horizontal bar, 8 px tall, with the track in `--border-soft` and the fill in
`--ok` up to 70%, `--warn` from 70% to 90%, `--alarm` above; the fill's right end is cut like a
comb cell. Beside it, in text: `used / budget` and the percentage (`41,200 / 128,000 (32%)`). The
bar has `role="meter"` with `aria-valuenow`, `aria-valuemin` and `aria-valuemax`. The same
component draws grant used against issued and a cost against its cap. A share that is not a limit
(the Cost view's hosted calls out of all calls) asks for the `neutral` tone and fills in `--idle`
at every level, so a slot served wholly by a provider never reads as a fault. A word beside a
gauge (the Cell page's `Tokens` and `Spend (USD)`) sits level with the bar it names, not with the
bar and its caption together, where it would read as part of the numbers.

### 4.3 Tables

Dense: 32 px rows (40 px where a row holds two short lines, as in the Fleet, and 44 px where the
second line holds the sub-bees' numbered comb cells, which are 20 px so their digit draws at the
12 px floor of 4.8), 13 px type, tabular figures. The header is sticky and uses `--surface-2`.
A column sorts when its header is clicked or activated by Enter or Space; the sorted column shows
an arrow and has `aria-sort`. The sort is stable and ties break on the row's id, so the order is
deterministic. Rows are keyed by id and update in place; while focus or the pointer is inside the table, new
rows wait behind a `3 new rows, re-sort` button instead of moving the list. Ids are monospace and
cut to their kind and the last five characters of their ULID (`cell_…2C3D4`), the part that
differs between ids minted the same day (a ULID starts with its timestamp, so the head reads
`cell_01H…` on every row); an id of twelve characters or fewer is shown whole, and the full id is
always in `title` and the accessible name. Times are relative with the ISO time in `<time>`. In a
table an enum, an id, a badge, a name or a platform never splits mid-word: cells are `nowrap`
except the few that hold free text (a reason, a capability list, the links) or a row of
buttons (the Devices list's door controls), which wrap between words or buttons so most rows
stay one line. Only a card may break inside a word. Below the width at which a table is a table, the one `SortSelect` control
stands in for its header row on every sortable table (the Fleet, the Forage map, the Cost tables,
the Capping queue): a select naming the column and a button saying the direction in words, both
driving the same sort state as the headers. A table whose columns would not fit the pane (the
Fleet) folds a second value under a first (the kind, source and lease age under the Cell's name,
the mode under the status, the role under the task, the wax under the Alarm count, the thoughts
under the Attendant link) rather than scrolling sideways.

### 4.4 Status dot

A 10 px circle in a status token followed by a word; the dot is `aria-hidden`, the word is the
content. Fixed words: `live`, `connecting` (first attempt, not yet confirmed), `reconnecting` (after a drop), `offline` (connection); `working`, `idle`,
`waiting`, `failed` (a bee); `active`, `watch` (a Cell's mode).

### 4.5 Supervision tree

`SupervisionTree` draws one Cell's Warden and its sub-bees as SVG.

- The Warden is a wide node centred at the top (200 by 72). Its second line says where it runs:
  `Hive Stand` or `on the Cell`.
- Sub-bees are nodes (160 by 72) in rows below, sorted by role then id, wrapped to as many per
  row as the width allows (at least 1, at most 6). Each node is joined to the Warden by a straight
  vertical-then-horizontal line.
- A node shows: a role glyph (a fixed set of nine single-path glyphs: Worker, Forager, Scout,
  Guard Bee, Undertaker, Drone, House Bee, Ripener, other), the id, what it is doing now (one
  line, cut at 28 characters, full text in `<title>`), a status dot with its word, and a
  `ContextGauge` along the bottom edge with its percent drawn beside it (`79%`), never left to
  the fill or the `<title>` alone. A node in an awake episode has a solid 2 px outline in
  `--accent` and the word `awake` in its top right corner; one on autopilot has neither.
- A bee in an Alarm state has its outline in `--alarm` and the word `Alarm`.
- Clicking or activating a node opens that bee's thoughts (`#/thoughts/<bee>`).

### 4.6 Cell diagram

`CellDiagram` (12.4) draws the Cell as a frame: the `SupervisionTree` inside it; to its right
(wide) or below it (narrow) a column of small boxes: the session (`CellSession` kind and state), the
Exoskeleton (`attached` or `not attached`), the lease (age and expiry for a Real Cell) or the
Virtual Cell's backend and state, and the model hosting for its bees (`local`, `Hive Stand`,
`hosted` or `hybrid`, per slot). Lines join each box to the tree node it concerns. Where the
Warden runs is drawn as the frame's own label, so a Warden on the Hive Stand is shown outside the
Cell frame and one on the Cell inside it.

### 4.7 Forage flow

`ForageFlow` (12.5) is three columns of horizontal bars, drawn from the ledger:

- **Pool.** One bar for the Queen's shared pool: the Royal Reserve segment (hatched, `--idle`), the
  granted segment, and the free segment. The bar's width is the total in the chosen unit
  (model seats, spend per hour, cores); a unit switch changes the whole diagram at once.
- **Grants.** One bar per Warden, sorted by Cell id, whose length is proportional to its grant,
  filled up to what it has used.
- **Spend.** One bar per sub-bee, grouped under its Warden, proportional to what it spent.
- Ribbons join each segment to the next column, with width proportional to the amount. A grant
  near expiry gets a `--warn` ribbon edge, its expiry as text and the word `soon` on a line under
  its name, on either canvas, so the name keeps its tail.

The flow's canvas is 480 units wide, the pool's labels left of its boxes and the others right, in
the gaps; a Warden's or a grant's label is its id as the tables show it (kind and ULID tail, 4.3),
or the tail alone where the line is too short for both. A pane narrower than that (a phone) gets
a canvas exactly as wide as the pane: every label moves to the left of its box, right-aligned,
the pool's amount goes on a line under its word rather than being cut away (a box is never
shorter than its label's lines), each line keeps the characters the width leaves (ten at 334 px,
eight at 304 px; the full words stay in `<title>`), the ribbons pass under the Warden and grant
labels (which have a halo of the surface), and nothing scrolls sideways above 240 px.

Below the flow, **per-host capacity** is a stacked bar per host (Hive Stand, each Nuc, each
Virtual Cell backend): in use, reserved, available, with the numbers beside it, and one legend
for the four kinds under the first capacity figure on the page. A Nuc's local pool sits to the
right of the shared flow, with its own reserve and the seats it exports.

### 4.8 Diagram rules

- A diagram is a pure function of its slice: no timers, no random numbers.
- Layout is computed from sorted ids and fixed sizes, so two renders of the same slice are
  byte-identical, and a test renders each diagram twice and compares.
- Every node, bar and ribbon has a `<title>` giving its full text and numbers. The `<svg>` has
  `role="img"` and an `aria-labelledby` pointing at a `<title>` that states what it shows. A
  hidden table with the same numbers follows each diagram for screen readers.
- Colours come from tokens (`fill="var(--ok)"`), never literals, so both themes work.
- A diagram measures its pane (`usePaneWidth`, a `ResizeObserver` on the box it is drawn in)
  and sizes its narrow canvas from it, one viewBox unit per pixel, so text in SVG, which is real
  `<text>` at 12 units, is at least 12 px on any phone; a wide canvas (the Cell diagram's 744
  units, the Attendant graph's natural size, the flow's 480) is drawn only in a pane that holds
  it, at exactly 1x (the `<svg>` carries its canvas's width) and centred, so diagram type stays on
  the page's 12 to 15 px scale on a wide screen rather than stretching with the pane. Before the
  pane is measured, and in a test, the canvas is the design's 360. If a label does not fit, it is
  cut and the full text stays in `<title>`.
- A line never runs through a node. The Attendant graph's lines take the plain elbow (out of the
  task's far edge, along the gap before the waiting task's layer, in) where that is clear, and
  otherwise the gaps between rows and columns, which every layer shares and no node sits in.
  Lines into one task share no segment: each takes a bus and an entry point of its own, and in
  the gaps a lane of its own, a few units apart (the gap widens by a lane for each line a task
  can receive), so two dependencies never draw as one line with one arrowhead.

## 5. Views

Each route below is a hash route. Each view names its read route, which seeds its slice once, and
its streams, which keep it live. Routes marked "added in 12.1" are being added to the Landing
Board by step 12.1 and appear in the generated types when they land. A view that is missing a
capability shows a one-line `forbidden` notice in place, never an empty screen.

### 5.1 Fleet (`#/fleet`)

All Cells in one table with a Real, Virtual or All filter (a three-way segmented control, default
All: one choice at a time, which is what makes it a segmented control and not the Capping view's
chips). Columns: Cell id, kind, source, status and mode, tier badge, mask badge, access level, two
badges for where the Warden runs and where its models come from, current task and role, open
Alarms, the highest live Cell Wax severity, lease age, and links to its page, its Attendant view
and its bees' thoughts. The **legend** (`Legend`) is a full-width strip under the view's title
row, above the table, at every width (its three sentences are longer than the row's free space);
it is never collapsed or scrolled out.
Read: `/v1/cells`, `/v1/fleet` (added in 12.1). Streams: cells, tasks, telemetry.

### 5.2 Cell page (`#/cells/<id>`)

The header leads with the Cell's name (`CellView.name`, the word the Fleet rows and the Forage's
host cards lead with too), the full id beside it as the subject, then kind, source and status,
then the tier badge and the mask badge, large and always visible (the header sticks under the
summary strip). Until the page is read the title is the plain word `Cell` over the id. Below it: the `CellDiagram`; then the Cell's
tasks and goals; the Forage its Warden holds (grant used against issued, pending requests); the
Honey the Warden can see, as a link into `#/honey/cells/<id>`; access level and mode (`ACTIVE` or
`WATCH`, with the last Patrol's report); Cell Wax with severity, proposer and expiry; open
Alarms; and Capping activity (proposals in flight, verdicts, rollbacks) with flight-recorder
playback for Exoskeleton actions (a step list with a frame viewer, play and pause, no write).
Read: `/v1/cells/{id}`, `/v1/cells/{id}/page` (added in 12.1), `/v1/wardens`. Streams: cells,
telemetry, tasks, forage, trail (Capping events for this Cell).

### 5.3 Forage (`#/forage`)

The `ForageFlow` and per-host capacity from 4.7; hosted spend against cost caps; the Forage map as
a table (source, grade, measured grade, distance from each Cell, abundance, cost); the Fanner's
queues per binding with waiting requests and their tempo; pending `ForageRequest`s with reasons
and the Queen's decision when it lands; grants nearing expiry. The host cards are named from the
fleet, which the view reads once itself when no view has seeded it yet. The Wardens, requests and
map tables are tables from 720 px; the Grants table, with ten columns, from 1080 px. Read:
`/v1/forage`, `/v1/llm`, `/v1/fleet` (added in 12.1). Streams: forage, cells.

### 5.4 Thoughts (`#/thoughts/<bee>`, `#/thoughts/queen`)

The bee's episodes, newest first: trigger, assembled context by section (each section collapsed,
expandable), the reasoning summary when the provider exposes one, the decision and the action
(its code as plain words, `retry task`, the code on `title`). For the Queen it also shows the
Attendant's current ordering of her inbox with scores, and each autopilot decision as a slim row
in words: what she decided (`the Queen handled an Alarm`), the action, the reason through the
Alarm kinds' table (`a Worker crashed`), and what it concerned. A **Follow** toggle appends new
episodes at the top
as they arrive and keeps the scroll position when off. Secrets and screenshot bytes are redacted
at the source; the UI shows `[redacted]` where the record says so and never tries to fetch more.
Needs `observe:thoughts`. A `Bee` select in the title row, built from the Fleet slice (the Queen, then each Cell's Warden
and the bees whose thoughts it lists, each named with its Cell), reaches any bee from here, and a
links row under the title leads to the bee's Cell page and its Warden's Attendant view.
Read: `/v1/thoughts` (added in 12.1), `/v1/episodes`. Streams: episodes,
telemetry.

### 5.5 Attendant (`#/attendant/<principal>`)

`<principal>` is `queen` or a Warden's id. Shows the task graph the principal is concerned with
(a layered left-to-right graph, nodes by task id order within a layer), what recently finished,
what comes next, and the ordered inbox behind it, with each task's placement and Alarms. A task
node shows its id as the tables do (4.3), its status word beside its dot and its Cell; one with
open Alarms has its outline in `--alarm` and the count with its word (`2 Alarms`) at the right
of its status line, as the bee node of 4.5 writes `Alarm`, never a bare number. Reached
from the fleet row and the Cell diagram. Read: `/v1/attendant/{principal}` (added in 12.1),
`/v1/tasks`, `/v1/wardens`. Streams: tasks (for the Queen: every task; for a Warden: its tasks).

### 5.6 Chat (`#/chat`)

One column: the conversation, oldest first, and below it the composer. Lines are `message`,
`reply`, `question`, `alarm` or `notice`, the Queen's on the left and the human's on the right.
A question shows its options as buttons (or a text field for a free answer); an Alarm shows
`Acknowledge`; once answered or acknowledged on this device the line keeps what the viewer said
(`You answered: spring`, `Acknowledged by you.`), and one settled elsewhere says `No longer
waiting.`. Each line links to the tasks and episodes it produced (`See task … in the Attendant`,
with the message it answers as the link's `title`, since a task has no title of its own). The
composer says under its switch that a message only talks to the Queen while a goal asks her to
plan and run work. A push-to-talk button holds
to record, and a spoken goal shows its transcript with `Confirm` and `Decline` before anything
runs. The page opens on the newest line (the conversation is scrolled to its end once the seed
lands) and follows a new line there unless the reader has scrolled up to read; a question's
answer buttons sit under its own line, above the composer: a question or Alarm line names its
inbox item in `ref` (the Entrance writes the question's id there), which joins the line to its
form, and a `Waiting for you` section below the conversation lists only the inbox items whose
line is not on the page (a question older than the newest lines read), so nothing is drawn
twice. The composer's send button is the
only filled accent button in the app. Break-glass Absconding needs the
password again, a typed phrase and a scope review; there is no one-click path. Read: `/v1/chat`, `/v1/inbox`. Streams: chat, push (to show a waiting
question while another view is open). Writes: `POST /v1/chat`, `/v1/chat/audio`, the answer and
acknowledge routes under `/v1/inbox`.

### 5.7 Honey (`#/honey/<path>`)

A folder tree on the left (a single list on phone) and the folder's items on the right: `/hive`,
`/cells/<id>`, `/bees/<id>`, `/tasks/<id>`, `/bee-bread`. Every item shows its `HoneyClearance`
(`C0`, `C1`, `C2`) and provenance. An item's title is a link to `#/honey/<path>?item=<id>`, which
opens it in the reading pane, so an open item can be shared, comes back on reload and closes with
Back; the pane's Close is a link back to the folder's own address. Only the Honey route reads the
hash's query. A search box searches the open folder; its hits stand under a heading that names
the words and the count (`Results for "passkey" (1)`), in place of the folder's own items while
the search is open. A legend under the title keys the three clearance labels with the README's
one-liners (public, internal, personal), which each badge also carries as its `title`; the
breadcrumbs are hidden at the root, where they would only repeat the title. "Propose a note"
would queue a note for the House Bee (Cell Wax when opened from a Cell's folder) and say so,
never "saved"; until the Landing Board carries it the browser draws no control and says nothing
of one. What shows follows the viewer's scopes. Read: `/v1/honey/tree`, `/v1/honey/items/{id}` and `/v1/honey/search`. There is no Honey stream: the
view reads on navigation and re-reads a folder when the trail stream shows a Honey event for it.

### 5.8 Cost (`#/cost`)

Per-goal spend as a stacked bar by slot and provider, a table under it, and for each slot what
moving it local would save. Money is two decimals with thousands separators (`$3.10`,
`$1,234.50`), and four only below one cent (`$0.0050`), so a column of figures lines up. Read:
`/v1/cost` (added in 12.1), `/v1/llm`. Streams: trail (normalised `Usage` events), forage.

### 5.9 Capping (`#/capping`)

The Hive-wide queue of proposals by tier and state, judge verdicts with reasons, rollbacks,
sampled-audit findings and rates per tier. The tier and state filters are chips (several of a
group can be on at once; `All` clears it) from 720 px, and one select per group below, since the
chips would take six rows of a phone before any content; a group of several chips collapses to
its first member when the select takes over, so the control always names what the table shows.
The Fleet's filter is a segmented control because it is one choice at a time. A proposal's state
and the gate's verdict are the words of `enums.ts` (`Approved` for `CAPPED`, `Refused` for
`REJECTED`, `Rolled back`), the same words the rates cards count by, on the queue's tags, the
state chips and the Cell page's Capping panel alike, with the gate's code on each tag's and chip's
`title`; the judge's finding on a sampled audit is drawn the same way (`Approved`, `Wants
changes`, `Refused`), and a tier's `title` is its code (`OUTSIDE_SCRATCH_WRITE`), like every
other `title` on the page. Every item links to the bee's thoughts and, for an
Exoskeleton action, to its recording. Read: `/v1/capping` (added in 12.1). Streams: trail.

### 5.10 Devices and enrolment (`#/devices`, `#/enrol`)

`#/devices` is on every device: the device list with where each last connected from (a table
from 1280 px, since its capability list, platform and door controls need the room; cards below),
the passkey step-up prompt, and the join screen a new device lands on when it opens the Entrance
with an invite. Read: `/v1/devices`, `/v1/devices/me`. Stream: entrance (security events).
`#/enrol` renders only when the session's `listener` is `loopback`: the invite (code and QR, with
one line on what to do with them; the button says `Create invite`), the
pending list, approve (name, capabilities as labelled boxes from the known set, spend cap,
expiry), deny, unlock, revoke, and the
Entrance Reducer with its reopen flow. The Reducer's panel is headed `Remote access` and says the
mode in words (`on (serving)`, `reduced, the Hive Stand only`) over one line on what reducing
does; its button reads `Reduce to the Hive Stand only`, behind a second tap. A remote session that opens `#/enrol` sees a plain
"not found" page. This is a convenience; the remote listener 404s the routes (8.15). Read:
`/v1/entrance/pending`, `/v1/entrance/invites`, `/v1/entrance/mode`, `/v1/entrance/watch`. Below
the pending list, "Waiting to use your screen or sound" lists the tasks held for the owner's yes to
drive the running screen or audio (ADR-0069), each with what it asks for, the machine, the task and
the bee, and when it is refused unanswered; Allow and Refuse post the loopback-only, stand-key
`/v1/entrance/watch/{request_id}/approve` and `/deny`.

The join screen is the one place outside the chat where the app writes beyond the Entrance's own device and door routes: it posts `POST /v1/enrol/passkey-options` and `POST /v1/enrol/passkey` to redeem an invite, so the client's write guard allows `/v1/enrol/passkey*` and nothing else under `/v1/enrol/`.

### 5.11 Metrics

`/v1/metrics` (added in 12.1) is the Prometheus endpoint for scrapers. The UI does not read it;
the strip uses `/v1/summary` (added in 12.1) and the streams.

### 5.12 Help (`#/help`, `#/help/<page>`)

The in-app help and the guided tour, reachable from the nav on every listener. The pages are
markdown files bundled with the app (`src/views/help/content/`), parsed by a small grammar of
their own (`##` and `###` headings, paragraphs, lists, bold, code and links): start here, the
words (every README term with its plain meaning, in alphabetical order), bees and Cells, Forage
and cost, memory and Honey, Capping and guard rails (the gate, the judge, the Nurse Bee, the Guard
Bee, approvals), devices and the Hive Stand (the Entrance, enrolment, the Pollen Packet, the stand
key, step-up), and the screens, one section per view. The page list stands beside the text from
1080 px, each page with a one-line summary, and above it as one wrapping row of links below; the
open page is bold with a rule beside or under it. A search box in the title
row searches every page as the person types, since the text is bundled: a hit is a section whose
words hold every word of the query, headings first, drawn in place of the page with a link to its
section. A section's address is `#/help/<page>?s=<heading id>`, which lands focus on the heading
(kept clear of the sticky head by a scroll margin); every view's gloss carries a `More in Help`
link to its section of the screens page, and the Fleet's and the Honey's legends link to the
glossary entries on the Comb Shield levels and the clearances. A test proves every nav label, strip
word, badge word and clearance name has a glossary entry, and every nav item a screens section.

**The tour** starts from the Help screen's card and never on its own. It is a small dialog fixed
at the bottom of the page (above the nav on a phone, at the lower right from 720 px), never modal
and dimming nothing, with the step's title and words, `Step N of M` in words, and Back, Next
(Finish on the last step) and Skip tour; the arrow keys move and Escape skips, and focus moves to
the panel on each step, and back to the card's button when the tour ends with the Help screen
open. The element a step is about carries a `data-tour` attribute (the strip, the connection
word, the nav, the theme toggle, every view's title row as `view-title-<its screens section>`,
so a step about one screen is never satisfied by another's row, the chat's composer) and
is outlined by a ring drawn at its measured box, kept inside the viewport, and scrolled into
view, without motion under `prefers-reduced-motion`; the panel names it in words, so the ring is
never the only signal, and moves to the top, under the head, while the element sits in the lower
half of the viewport, so it never covers what it is talking about. A
step on another screen opens its route first; a step whose element is not on the page is skipped
the way the tour was moving. Finishing is remembered per browser in `localStorage` (every read and
write guarded); skipping is not, and a log out or a lost session mid-tour closes it, so the next
session never opens on an old step. Reads nothing from the Hive.

**Ask** (`#/help/ask`, ADR-0069) is the one entry of the page list that is not a page: a panel in
place of the text with a question box, an Ask button, a row of example questions, and one note,
always shown, that an AI helper answers from these pages and nothing else, lists the sections it
read, can be wrong, and cannot see or change the Hive. A question goes to `GET /v1/help/ask`, the
screen's one read; while it is out the panel says `Asking the help` (`aria-busy`), and the answers
stack under the box, newest last, each with the question above it, the words or the reason there
are none (no model on this Hive, the model did not answer, nothing matched: a sentence,
never a code), and its sources as links into the pages (`Words: Night Veil`, with `(cited)` after
the ones the answer rests on, words being the only mark). A refusal is the `ErrorNotice`. The
build copies the pages beside the app as `help/<page>.md`, which is what the Entrance answers from,
so the Ask and the pages never disagree.

## 6. Typography and density

- **Sans:** `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial,
  sans-serif`: the device's own face (Segoe UI on Windows, Roboto on Android, San Francisco on
  Apple) is the design, so the app fetches nothing and every device of a kind shows the same
  page. A named face was dropped because one that is not shipped is never seen.
- **Mono (ids, code, numbers in logs):** `ui-monospace, "SF Mono", Menlo, Consolas,
  "DejaVu Sans Mono", monospace`.
- **Headings:** weight 600 with 0.01 em tracking. One rhythm in every view: a panel's heading
  and every top-level section heading (the Forage's, the Cost's and the Capping's sections, the
  Devices' Entrance panel, the Enrol's three sections, the Queen's inbox on the Thoughts view, the
  Honey lists, the chat's waiting section) is `.section-heading`, 18 px with a hairline under it;
  a sub-heading inside a panel (an `h3`) carries none.
- **Sizes (token, px):** `--fs-xs` 12 (badges, captions), `--fs-sm` 13 (tables), `--fs-md` 15
  (body), `--fs-lg` 18 (section titles), `--fs-xl` 22 (view titles). Line height 1.4 for text, 1.2
  for headings.
- **Spacing:** `--sp-1` to `--sp-8` are 4, 8, 12, 16, 24, 32 px. Page gutter 16 px under 720 px,
  24 px above. `--radius` is 6 px for controls and `--radius-card` 12 px for cards and panels;
  tier pills are fully round and mask tags 2 px.
- **Density:** table rows 32 px; buttons 36 px with a 44 px touch area under 720 px, and under
  720 px a link in a row, a card, a line of links or a breadcrumb has the same 44 px area as
  padding around its line (a flex row that sets one beside plain text aligns them by baseline).
  A card has one rhythm: where its rows are label and value pairs (the Capping queue) every row
  takes that 44 px pitch, and in a Cell page panel a row's link takes a line of its own under
  the row's facts; numbers use `font-variant-numeric: tabular-nums`.

## 7. Accessibility

- **Contrast.** Every text and badge pairing in section 3 is at least 4.5:1; borders of controls
  are at least 3:1 on `--surface`. A token change must re-run the contrast test, which reads the
  tokens and fails below those numbers.
- **Colour is never alone.** Status has a word, tier and mask have text and a fixed shape, a chart
  series has a label or a pattern.
- **Keyboard.** Every control and every diagram node is reachable by Tab in reading order, with a
  visible 2 px `--accent` focus ring offset by 2 px. On the chrome (the bar and the nav) the ring
  is `--chrome-accent`, since `--accent` is about 2:1 on the propolis in the light theme; a nav
  item fills its rail or bar edge to edge, so there the ring is inset 2 px, where the rail's
  scroll box cannot clip its sides. The ring is a non-text indicator and holds 3:1 on what it
  is drawn on in both themes; the contrast test checks both pairings. Tables sort and expand by
  Enter and Space.
  The nav is a landmark (`<nav>`), the main area a `<main>`, with a skip link to it as the first
  focusable element.
- **Motion.** One duration, `--motion` (150 ms ease), on hover, on a row's insert and on a new
  Thoughts card, which slides in 8 px; the connection dot pulses only while `connecting` or
  `reconnecting`. Nothing else moves.
- **Reduced motion.** Under `prefers-reduced-motion: reduce` there are no transitions or
  animated updates; a changed value is marked with a static 2 px underline in `--accent` for five
  seconds (the strip's numbers carry it in either mode).
- **Live regions.** The summary strip is `role="status"` with `aria-live="off"` (it changes
  constantly); an arriving question or Alarm in the chat is announced once through a polite live
  region. The connection word changes are announced.
- **Labels.** Every control has a visible label or an `aria-label`. Tables have captions.

## 8. Writing in the UI

- Use the README's words and no others: Queen, Warden, Worker, Forager, Cell, Real Cell, Virtual
  Cell, Forage, grant, Honey, Nectar, Alarm, Cell Wax, Patrol, Capping, Comb Shield level (shown as
  `MEADOW`, `PROPOLIS`, `NIGHT VEIL`), Pheromone Mask. If the README does not define a word, the
  UI does not use it; a term the reader may not know gets a one-line gloss in its legend or empty
  state.
- **Every view opens with one sentence** saying what it shows (`VIEW_HINTS` in
  `src/components/words/`), plain words first and the bee term in the same breath (`Capacity:
  cores, memory, model seats and spend, and who holds it`). The nav item carries the same
  sentence as its `title`.
- **A code is shown as a phrase.** The schema's enums (`CONTEXT_PRESSURE`, `SCRATCH_WRITE`,
  `entrance:submit`, an inbox kind, a clearance, a proposal's state, an OS family) are drawn
  through one words table per enum in `src/components/words/enums.ts` (`context nearly full`,
  `writes inside its scratch folder`, `may send goals and messages (entrance:submit)`, `Approved`
  for `CAPPED`, `macOS` for `MACOS`), with the code kept on the element's `title` for the
  operator; a code the table does not know is shown as sent, in lower case with spaces
  (`plainCode`), which is also how a code with no table of its own (an episode's action, an
  autopilot decision's action and reason, an access level, a grant's state, a Forage request's
  kind, a provider's health, an effort, a rollback's method, a lease state, a Virtual Cell's
  status, a task's status, a wax note's or an Alarm's severity, a wax note's state, a held
  request's action, a thought's taint label) is drawn, with the code on `title` where the
  element is a table cell, a tag or a line of text (the taint tag keeps its one-line meaning
  there instead, since its code is already the word). A
  diagram's node or box keeps its whole sentence in its `<title>` (4.8), so the Cell diagram's
  lease and Virtual Cell boxes carry the words there, not the code. The Entrance sends every
  such code in upper case (`FULL`, `HEALTHY`, `SNAPSHOT`), so a page writes it the same way
  wherever it stands: the Fleet row and the Cell page header both say `read only`, the Grants
  table and the Cell's Forage panel both say `active`, the Cell page's Cell Wax panel and the
  Fleet's wax mark both say `block`. No view keeps a formatter of its own for this; a fixture
  carries the wire's upper case so a test proves the rule where the data arrives. An id the Landing Board carries without a title (a task, a wax note, an Alarm on the
  Cell page) links to where its words are (the chat, the Honey item), and never invents a title.
  A model slot arrives as its `ModelSlot` code (`QUEEN`, `JUDGE`) and is written in lower case
  wherever it appears (the Cost tables, the Forage map, the Cell diagram's hosting box, an
  episode's usage), the code on `title` where there is an element to carry it.
- **A pressed toggle** (`aria-pressed="true"`) is filled in the accent in every view, under the
  pointer too, by one rule in `base.css`; no view carries a pressed rule of its own.
- Labels are nouns (`Context`, `Grant`); buttons are verbs (`Answer`, `Acknowledge`, `Follow`).
  Sentences are short and plain.
- **Errors** show one human sentence, then what to do. Example: `hivemind.entrance.step_up_required`
  shows "This needs you to confirm again. Enter your password and use your device key." When the
  next step names a place to go, it is a link there (`Go back to the Fleet and pick another.`
  to `#/fleet`). The raw
  code is kept off screen, on the alert's `data-code` for devtools. The sentence comes from a
  fixed table in the client keyed by code; an unknown code shows `ErrorBody.detail`, which the
  Entrance writes as one sentence and never fills with a credential. A `401` takes the human to
  login with "Your session ended. Log in again." (the Entrance never says why, and neither does
  the UI).
- **Empty states** say what would fill the view (`No Cells yet. A Cell appears here when the Queen
  provisions or borrows one.`), always as `EmptyState` (the sentence on the faint comb, in a comb
  cell) wherever a view or a section has nothing to list; a sentence inside a panel (`No open
  Alarms on this Cell.`) stays a plain line, since a panel is a comb cell already. A slice not
  yet seeded shows a labelled skeleton, and a stream that has dropped says `stale since hh:mm:ss,
  reconnecting` in the view's title row (section 2) until it is back.
  A legend's sentences never depend on an answer: the Fleet's Comb Shield legend keeps the
  Entrance's three fixed sentences as its fallback, so a refused read leaves no bare badge.
