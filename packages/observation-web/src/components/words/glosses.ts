/**
 * One plain sentence per screen: what it shows, for the title row and the nav.
 *
 * A newcomer cannot tell Fleet (machines) from Forage (capacity) from Capping (the change gate)
 * by the bee words alone, so every view opens with one line saying what it holds, and the nav
 * item that leads to it carries the same line as its `title`. The sentences live here, once, so
 * the nav and the title row never drift apart; they use the README's words and gloss them in the
 * same breath.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/words/`. Read by `src/shell/nav_items.tsx` for the
 *   nav and by each view in `src/views/` for its `ViewTitle` hint.
 *
 * Key invariants:
 *   - One sentence per route the nav lists, short enough for one line at 720 px.
 *   - Plain words first, the bee term in the same sentence, so neither is left unexplained.
 *
 * See Also:
 *   - docs/observation/design.md section 8 (a term the reader may not know gets a gloss).
 */

export const VIEW_HINTS = {
  fleet: 'Every machine (Cell) the Hive works on: its shield level, mask, status and task.',
  cell: 'One Cell: the bees on it, their tasks, its Forage, Cell Wax and open Alarms.',
  forage: 'Capacity: cores, memory, model seats and spend, and who holds it.',
  thoughts: 'What a bee decided and why, one episode at a time, newest first.',
  attendant: 'What is waiting on the Queen or a Warden, in priority order, and the task graph.',
  chat: 'Talk to the Queen: ask for work, answer her questions, acknowledge Alarms.',
  honey: 'What the Hive has learned, as folders you can read and search.',
  cost: 'What the Hive has spent on models, by goal, slot and provider.',
  capping: 'The change gate: what bees asked to do outside scratch and whether it was allowed.',
  devices: 'The devices enrolled on the Hive, what each may do, and remote access.',
  enrol: 'Let a new device in: create an invite, then approve the device once it joins.',
  help: 'What the words mean, how the Hive works and how to read each screen, and the tour.',
} as const;

export type GlossedView = keyof typeof VIEW_HINTS;
