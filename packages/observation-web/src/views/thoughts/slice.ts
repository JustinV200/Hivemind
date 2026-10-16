/**
 * The Thoughts slice: one bee's episode records and autopilot decisions, newest first.
 *
 * The view seeds the slice from `GET /v1/thoughts` once, then folds the episodes stream into it.
 * Frames from the stream carry every bee's episodes, so the reducer keeps only the open principal's.
 * The Queen's records carry her real principal (the Hive id), not the word `queen` the route
 * names her by, so the seed reports the resolved principal and a frame matches either. Her
 * autopilot decisions have no episodes: they arrive as `queen.decided` and `alarm.handled` trail
 * frames and are folded in as decision rows, queued behind Follow like episodes.
 * With Follow on, a new episode goes to the top; with Follow off it waits in a queue the reader
 * reveals, so the list under their eyes never moves. The slice is bounded: past `MAX_EPISODES` the
 * oldest fall off the end, and the page cursor is dropped with them, because a cursor can only
 * continue from the last record it handed out and the trimmed ones would otherwise be skipped.
 * Opening the same bee again after that cut starts clean, so the fresh seed brings a cursor and
 * the view's "open this view again to read further back" holds.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Registered in `src/app.tsx`'s table, filled
 *   by `use_thoughts.ts` (reads) and `useViewStream` (frames), read by `thoughts_view.tsx`.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_EPISODES` episodes, queued episodes and decisions each.
 *   - An episode id appears once in the list and the queue together; a repeat changes nothing.
 *   - An answer for a principal that is no longer open (the reader moved on) is ignored.
 *   - Stored records are the generated types as received; a trail frame is only reshaped into the
 *     decision row the seed would have sent for the same event.
 *
 * See Also:
 *   - docs/observation/design.md section 5.4.
 *   - src/store/registry.ts, merged into below so `useSlice('thoughts')` is typed.
 */
import type { components } from '../../landing_board/types';
import type { Slice } from '../../store';

type Schemas = components['schemas'];
export type EpisodeView = Schemas['EpisodeView'];
export type DecisionView = Schemas['AutopilotDecisionView'];
export type ThoughtsPage = Schemas['ThoughtsPage'];
export type EpisodeFrame = Schemas['EpisodeFrame'];
export type TrailFrame = Schemas['TrailFrame'];

// The Queen's route name, and the trail kinds that are her deterministic decisions.
export const QUEEN = 'queen';
export const DECISION_KINDS = ['queen.decided', 'alarm.handled'] as const;

export const MAX_EPISODES = 500;

export interface ThoughtsState {
  // Whose thoughts these are; null until a view opens one.
  principal: string | null;
  // The principal its records carry, once the seed says: the Queen's is her Hive id.
  resolved: string | null;
  seeded: boolean;
  episodes: readonly EpisodeView[];
  decisions: readonly DecisionView[];
  // Episodes that arrived while Follow was off, newest first.
  queued: readonly EpisodeView[];
  // Decisions that arrived while Follow was off, newest first.
  queuedDecisions: readonly DecisionView[];
  nextCursor: string | null;
  // True once the bound dropped the oldest records, so "Older" is no longer offered.
  truncated: boolean;
  follow: boolean;
  // The last refusal or failure of a read, shown through `ErrorNotice`; null when well.
  error: unknown;
}

export type ThoughtsEvent =
  | { type: 'open'; principal: string }
  | { type: 'seeded'; principal: string; page: ThoughtsPage }
  | { type: 'older'; principal: string; cursor: string; page: ThoughtsPage }
  | { type: 'failed'; principal: string; error: unknown }
  | { type: 'frame'; frame: EpisodeFrame }
  | { type: 'decision'; frame: TrailFrame }
  | { type: 'follow'; on: boolean }
  | { type: 'reveal' };

export const initialThoughts: ThoughtsState = {
  principal: null,
  resolved: null,
  seeded: false,
  episodes: [],
  decisions: [],
  queued: [],
  queuedDecisions: [],
  nextCursor: null,
  truncated: false,
  follow: true,
  error: null,
};

// `front` then `back`, each id once (the first sighting wins), cut to the bound.
function join<T extends { id: string }>(front: readonly T[], back: readonly T[]) {
  const seen = new Set<string>();
  const merged = [...front, ...back].filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
  return { list: merged.slice(0, MAX_EPISODES), cut: merged.length > MAX_EPISODES };
}

// Newest first by time; `sort` is stable, so equal times keep the order they were joined in.
function newestFirst<T extends { at: string }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

// Applies the bound's side effect: a cut list means the cursor can no longer be trusted.
function bounded(state: ThoughtsState, list: readonly EpisodeView[], cut: boolean): ThoughtsState {
  return {
    ...state,
    episodes: list,
    truncated: state.truncated || cut,
    nextCursor: cut ? null : state.nextCursor,
  };
}

function seeded(state: ThoughtsState, page: ThoughtsPage): ThoughtsState {
  // Frames that arrived before the answer are newer than it, but the sort settles any overlap.
  const episodes = join(newestFirst([...state.episodes, ...page.episodes]), []);
  const decisions = join(newestFirst([...state.decisions, ...page.decisions]), []);
  // Coming back to a principal already paged into keeps the deeper cursor.
  const nextCursor = state.seeded ? state.nextCursor : page.next_cursor;
  return {
    ...bounded({ ...state, nextCursor }, episodes.list, episodes.cut),
    resolved: page.principal ?? state.resolved,
    seeded: true,
    decisions: decisions.list,
    error: null,
  };
}

function older(state: ThoughtsState, page: ThoughtsPage): ThoughtsState {
  const episodes = join(state.episodes, page.episodes);
  const decisions = join(state.decisions, page.decisions);
  return {
    ...bounded({ ...state, nextCursor: page.next_cursor }, episodes.list, episodes.cut),
    decisions: decisions.list,
    error: null,
  };
}

function framed(state: ThoughtsState, episode: EpisodeView): ThoughtsState {
  // Another bee's episode, or one already held, is not this view's to show.
  if (state.principal === null) return state;
  if (episode.principal !== state.principal && episode.principal !== state.resolved) return state;
  if ([...state.episodes, ...state.queued].some((held) => held.id === episode.id)) return state;
  if (state.follow) {
    const { list, cut } = join([episode], state.episodes);
    return bounded(state, list, cut);
  }
  return { ...state, queued: join([episode], state.queued).list };
}

function revealed(state: ThoughtsState): ThoughtsState {
  if (state.queued.length === 0 && state.queuedDecisions.length === 0) return state;
  const { list, cut } = join(state.queued, state.episodes);
  const decisions = join(state.queuedDecisions, state.decisions).list;
  return { ...bounded(state, list, cut), decisions, queued: [], queuedDecisions: [] };
}

const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);

// Reshapes a trail event the way the server shapes the seed's decision rows (`_decisions`).
function decisionOf(event: TrailFrame['event']): DecisionView {
  const { payload } = event;
  return {
    id: event.id,
    at: event.at,
    kind: event.kind,
    subject_id: event.subject_id,
    action: text(payload.action),
    reason: text(payload.reason) ?? text(payload.cause),
    alarm_id: text(payload.alarm_id) ?? (event.kind === 'alarm.handled' ? event.subject_id : null),
  };
}

function decided(state: ThoughtsState, event: TrailFrame['event']): ThoughtsState {
  // Only the Queen has decisions, and only the two kinds are hers.
  if (state.principal !== QUEEN || !(DECISION_KINDS as readonly string[]).includes(event.kind))
    return state;
  const decision = decisionOf(event);
  if ([...state.decisions, ...state.queuedDecisions].some((held) => held.id === decision.id))
    return state;
  if (state.follow) return { ...state, decisions: join([decision], state.decisions).list };
  return { ...state, queuedDecisions: join([decision], state.queuedDecisions).list };
}

export function reduceThoughts(state: ThoughtsState, event: ThoughtsEvent): ThoughtsState {
  switch (event.type) {
    case 'open':
      // The same bee again keeps what is held (the reseed merges), unless the bound cut the list
      // and took the cursor with it: then only a clean start can offer Older again. Another bee
      // starts clean.
      if (event.principal === state.principal && !state.truncated)
        return state.error ? { ...state, error: null } : state;
      return { ...initialThoughts, principal: event.principal, follow: state.follow };
    case 'seeded':
      return event.principal === state.principal ? seeded(state, event.page) : state;
    case 'older':
      // A reply for a page already taken (a double click) or a bee already left is ignored.
      return event.principal === state.principal && event.cursor === state.nextCursor
        ? older(state, event.page)
        : state;
    case 'failed':
      return event.principal === state.principal ? { ...state, error: event.error } : state;
    case 'frame':
      return framed(state, event.frame.episode);
    case 'decision':
      return decided(state, event.frame.event);
    case 'follow':
      if (event.on === state.follow) return state;
      // Turning Follow on shows what waited, so the list is whole again.
      return event.on ? revealed({ ...state, follow: true }) : { ...state, follow: false };
    case 'reveal':
      return revealed(state);
  }
}

export const thoughtsSlice: Slice<ThoughtsState, ThoughtsEvent> = {
  initial: initialThoughts,
  reduce: reduceThoughts,
};

// Tells the store's hooks about this slice, so `useSlice('thoughts')` and `dispatch` are typed.
declare module '../../store/registry' {
  interface SliceRegistry {
    thoughts: Slice<ThoughtsState, ThoughtsEvent>;
  }
}
