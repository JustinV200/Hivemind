/**
 * The connection slice: the state of each live stream, for the top bar's dot and the strip.
 *
 * The shell opens three streams (cells, telemetry, episodes). The top bar shows one word for them
 * all (`live`, `reconnecting`, `offline`), and the summary strip dims and says `stale since` when
 * any stream that could be live is not, rather than showing old numbers as current. A stream the
 * Entrance refused (4401, 4403) is not "down": it will not come back, so it is kept apart and the
 * strip says which groups it leaves without data.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Folded by `HiveStore`; fed by `feed.ts` from each
 *   stream's state callback; read by the top bar and the summary strip.
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
import type { StreamState } from '../landing_board/client';

export type StreamName = 'cells' | 'telemetry' | 'episodes';
export type ConnectionWord = 'live' | 'reconnecting' | 'offline';

export const STREAM_NAMES: readonly StreamName[] = ['cells', 'telemetry', 'episodes'];

export interface StreamStatus {
  state: StreamState;
  since: number;
}

export interface ConnectionSlice {
  streams: Record<StreamName, StreamStatus>;
}

export type ConnectionEvent =
  | { type: 'stream'; name: StreamName; state: StreamState; at: number }
  | { type: 'reset'; at: number };

function allIn(state: StreamState, at: number): Record<StreamName, StreamStatus> {
  return {
    cells: { state, since: at },
    telemetry: { state, since: at },
    episodes: { state, since: at },
  };
}

export const initialConnection: ConnectionSlice = { streams: allIn('closed', 0) };

export function reduceConnection(slice: ConnectionSlice, event: ConnectionEvent): ConnectionSlice {
  if (event.type === 'reset') return { streams: allIn('connecting', event.at) };
  const current = slice.streams[event.name];
  // A repeat of the same state keeps its original time, so `since` means "since it became this".
  if (current.state === event.state) return slice;
  return { streams: { ...slice.streams, [event.name]: { state: event.state, since: event.at } } };
}

function usable(slice: ConnectionSlice): StreamStatus[] {
  return STREAM_NAMES.map((name) => slice.streams[name]).filter((s) => s.state !== 'refused');
}

export function connectionWord(slice: ConnectionSlice): ConnectionWord {
  const needed = usable(slice);
  if (needed.length > 0 && needed.every((s) => s.state === 'live')) return 'live';
  if (needed.some((s) => s.state === 'connecting' || s.state === 'reconnecting')) {
    return 'reconnecting';
  }
  return 'offline';
}

// When the strip last had every usable stream live; null when it is current.
export function staleSince(slice: ConnectionSlice): number | null {
  const down = usable(slice).filter((s) => s.state !== 'live');
  return down.length === 0 ? null : Math.min(...down.map((s) => s.since));
}

export function refusedStreams(slice: ConnectionSlice): StreamName[] {
  return STREAM_NAMES.filter((name) => slice.streams[name].state === 'refused');
}
