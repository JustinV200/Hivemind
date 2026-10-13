/**
 * The connection slice: the state of each live stream, for the top bar's dot and the strip.
 *
 * The shell opens three streams (cells, telemetry, episodes). The top bar shows one word for them
 * all (`live`, `connecting`, `reconnecting`, `offline`), and the summary strip dims and says `stale
 * since` when any stream that was live and dropped is not back, rather than showing old numbers as
 * current. A stream on its
 * first attempt is `connecting`, not `reconnecting`: a quiet stream is only known to be accepted
 * when the hello deadline passes (socket.ts), so for those seconds nothing has dropped and nothing
 * is stale. A stream the Entrance refused (4401, 4403) is not "down": it will not come back, so it is kept apart and the
 * strip says which groups it leaves without data.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/slices/`. Folded by `HiveStore`; fed by `feed.ts` from each
 *   stream's state callback; read by the top bar and the summary strip.
 *
 * A view that opens a stream of its own (the Thoughts view's follow mode) reports it here too, keyed
 * by path, so the top bar's word covers it. A view stream is not one the strip needs, so it never
 * makes the strip stale, and it leaves the slice when the view closes it.
 *
 * Key invariants:
 *   - Pure: the time of a change comes in with the event, never from a clock here.
 *   - `since` is when the stream entered its current state, so `staleSince` is the earliest time
 *     any needed stream stopped being live.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4.4 for the words and the stale rule.
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for the reconnect policy.
 */
import type { StreamState } from '../../landing_board/client';

export type StreamName = 'cells' | 'telemetry' | 'episodes';
export type ConnectionWord = 'live' | 'connecting' | 'reconnecting' | 'offline';

export const STREAM_NAMES: readonly StreamName[] = ['cells', 'telemetry', 'episodes'];

export interface StreamStatus {
  state: StreamState;
  since: number;
}

export interface ConnectionSlice {
  streams: Record<StreamName, StreamStatus>;
  // Streams a view opened, by path; present only while the view holds them open.
  views: Readonly<Record<string, StreamStatus>>;
}

export type ConnectionEvent =
  | { type: 'stream'; name: StreamName; state: StreamState; at: number }
  | { type: 'view'; path: string; state: StreamState; at: number }
  | { type: 'reset'; at: number };

function allIn(state: StreamState, at: number): Record<StreamName, StreamStatus> {
  return {
    cells: { state, since: at },
    telemetry: { state, since: at },
    episodes: { state, since: at },
  };
}

export const initialConnection: ConnectionSlice = { streams: allIn('closed', 0), views: {} };

// A closed view stream is forgotten, so an unmounted view leaves nothing for the top bar to show.
function reduceView(
  slice: ConnectionSlice,
  event: Extract<ConnectionEvent, { type: 'view' }>,
): ConnectionSlice {
  const current = slice.views[event.path];
  if (event.state === 'closed') {
    if (current === undefined) return slice;
    const kept = Object.entries(slice.views).filter(([path]) => path !== event.path);
    return { ...slice, views: Object.fromEntries(kept) };
  }
  if (current?.state === event.state) return slice;
  return {
    ...slice,
    views: { ...slice.views, [event.path]: { state: event.state, since: event.at } },
  };
}

export function reduceConnection(slice: ConnectionSlice, event: ConnectionEvent): ConnectionSlice {
  if (event.type === 'reset') return { ...slice, streams: allIn('connecting', event.at) };
  if (event.type === 'view') return reduceView(slice, event);
  const current = slice.streams[event.name];
  // A repeat of the same state keeps its original time, so `since` means "since it became this".
  if (current.state === event.state) return slice;
  return {
    ...slice,
    streams: { ...slice.streams, [event.name]: { state: event.state, since: event.at } },
  };
}

function usable(slice: ConnectionSlice): StreamStatus[] {
  return STREAM_NAMES.map((name) => slice.streams[name]).filter((s) => s.state !== 'refused');
}

export function connectionWord(slice: ConnectionSlice): ConnectionWord {
  // The word covers a view's streams as well, so a Thoughts stream that drops shows in the bar.
  const needed = [...usable(slice), ...Object.values(slice.views)].filter(
    (s) => s.state !== 'refused',
  );
  if (needed.length > 0 && needed.every((s) => s.state === 'live')) return 'live';
  // A drop outranks a first attempt: the bar says the worse of the two.
  if (needed.some((s) => s.state === 'reconnecting')) return 'reconnecting';
  if (needed.some((s) => s.state === 'connecting')) return 'connecting';
  return 'offline';
}

// When the strip last had every usable stream live; null when it is current. A stream still on its
// first attempt has not dropped, and the strip is seeded from the summary read, so it is current.
export function staleSince(slice: ConnectionSlice): number | null {
  const down = usable(slice).filter((s) => s.state !== 'live' && s.state !== 'connecting');
  return down.length === 0 ? null : Math.min(...down.map((s) => s.since));
}

export function refusedStreams(slice: ConnectionSlice): StreamName[] {
  return STREAM_NAMES.filter((name) => slice.streams[name].state === 'refused');
}
