/**
 * The Attendant slice: one principal's task graph and ordered inbox, kept live by task frames.
 *
 * The view seeds the slice from `GET /v1/attendant/{principal}` (`queen` or a Warden's id). Then
 * each `TaskGraphFrame` is folded in at once: a frame for a task already in the graph replaces
 * that node's status, placement Cell and Warden, so the graph moves the moment the task does. The
 * frame carries neither the order of the inbox, nor the finished and up-next lists, nor a new
 * task's role, so a frame only bumps a counter and the hook re-reads the view once for those.
 * Nothing in this file reads, waits or looks at a clock.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/`. Registered in `src/app.tsx`'s table, filled
 *   by `use_attendant.ts` (the reads and the stream) and read by `attendant.tsx`.
 *
 * Key invariants:
 *   - Pure and bounded: a frame never adds a node (only the Entrance's own answer does), so the
 *     slice is never larger than the last read.
 *   - A frame for another principal's graph, or one that arrives before the seed, returns the
 *     state unchanged (same identity), so the store stays quiet.
 *   - A frame counts toward a re-read even when its task is not yet in the graph: that is how a
 *     task created after the seed reaches the screen.
 *   - An answer for a principal the reader has already left is dropped.
 *   - Stored records are the generated types as received; only a node's live fields change.
 *
 * See Also:
 *   - docs/observation/design.md section 5.5.
 *   - src/store/registry.ts, merged into below so `useSlice('attendant')` is typed.
 */
import type { components } from '../../landing_board/types';
import type { Slice } from '../../store';

type Schemas = components['schemas'];
// Named for what it is read as, so it never clashes with the view component in `attendant.tsx`.
export type AttendantRead = Schemas['AttendantView'];
export type TaskNode = Schemas['TaskNode'];
export type TaskGraphFrame = Schemas['TaskGraphFrame'];

export interface AttendantState {
  // Whose view this is; null until a screen opens one.
  principal: string | null;
  view: AttendantRead | null;
  // The last refusal or failure of a read, shown through `ErrorNotice`; null when well.
  error: unknown;
  // How many frames touched this principal's graph since it was opened; the hook re-reads the
  // view when this moves, so the reducer stays free of timers and requests.
  frameTicks: number;
}

export type AttendantEvent =
  | { type: 'open'; principal: string }
  | { type: 'seeded'; principal: string; view: AttendantRead }
  | { type: 'failed'; principal: string; error: unknown }
  | { type: 'task'; frame: TaskGraphFrame };

export const initialAttendant: AttendantState = {
  principal: null,
  view: null,
  error: null,
  frameTicks: 0,
};

// The node's live fields from the task as it stands now; role and Alarm count are not in a frame.
function patchNode(node: TaskNode, task: TaskGraphFrame['task']): TaskNode {
  return { ...node, status: task.status, cell_id: task.cell_id, warden_id: task.warden_id };
}

function foldTask(state: AttendantState, frame: TaskGraphFrame): AttendantState {
  const { view } = state;
  // Another principal's graph, or nothing seeded to fold into: not this screen's business.
  if (!view || frame.principal !== state.principal) return state;
  const frameTicks = state.frameTicks + 1;
  const known = view.nodes.some((node) => node.task_id === frame.task.id);
  if (!known) return { ...state, frameTicks };
  const nodes = view.nodes.map((node) =>
    node.task_id === frame.task.id ? patchNode(node, frame.task) : node,
  );
  return { ...state, frameTicks, view: { ...view, nodes } };
}

export function reduceAttendant(state: AttendantState, event: AttendantEvent): AttendantState {
  switch (event.type) {
    case 'open':
      // Every visit starts clean, so a principal revisited never shows what it was when last seen.
      return { ...initialAttendant, principal: event.principal };
    case 'seeded':
      // The Entrance's answer is authoritative: it replaces whatever frames folded before it.
      return event.principal === state.principal
        ? { ...state, view: event.view, error: null }
        : state;
    case 'failed':
      return event.principal === state.principal ? { ...state, error: event.error } : state;
    case 'task':
      return foldTask(state, event.frame);
  }
}

export const attendantSlice: Slice<AttendantState, AttendantEvent> = {
  initial: initialAttendant,
  reduce: reduceAttendant,
};

// Tells the store's hooks about this slice, so `useSlice('attendant')` and `dispatch` are typed.
declare module '../../store/registry' {
  interface SliceRegistry {
    attendant: Slice<AttendantState, AttendantEvent>;
  }
}
