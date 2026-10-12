/**
 * `StatusDot`: a coloured dot followed by its word.
 *
 * Status is always a word first and a colour second (design.md 3.2): the dot is decoration and
 * hidden from assistive technology, and the word is the content. The words are a fixed set, one
 * per state, so the same state reads the same in the top bar, the fleet table and the diagrams.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by the top bar for the connection word
 *   now, and by the fleet, Cell page and diagrams later.
 *
 * Key invariants:
 *   - Each word maps to exactly one status token: ok, warn, alarm or idle.
 *   - The dot is `aria-hidden`; only the word is announced.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.2 and 4.4.
 */
import type { ReactElement } from 'react';

export type StatusWord =
  | 'live'
  | 'reconnecting'
  | 'offline'
  | 'working'
  | 'idle'
  | 'waiting'
  | 'failed'
  | 'active'
  | 'watch';

type Tone = 'ok' | 'warn' | 'alarm' | 'idle';

const TONE: Record<StatusWord, Tone> = {
  live: 'ok',
  reconnecting: 'warn',
  offline: 'alarm',
  working: 'ok',
  idle: 'idle',
  waiting: 'warn',
  failed: 'alarm',
  active: 'ok',
  watch: 'idle',
};

export function StatusDot(props: { word: StatusWord }): ReactElement {
  return (
    <span className="status">
      <span className={`status-dot status-dot--${TONE[props.word]}`} aria-hidden="true" />
      <span>{props.word}</span>
    </span>
  );
}
