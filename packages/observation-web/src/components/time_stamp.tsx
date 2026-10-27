/**
 * `TimeStamp`: a moment as a `<time>` element, relative and readable, with the ISO time kept.
 *
 * Every record the views list has an `at`. It is shown relative to now ("just now", "5 min ago",
 * "in 2 h", "3 d ago"), and past a week as the local date, so a reader sees at a glance how fresh
 * a record is. The exact ISO string stays in `dateTime` and `title`, so nothing has to be parsed
 * back out of the page and a hover gives the full moment.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by every view that lists records; the
 *   clock it reads is `clock.ts`, shared by every stamp on the page.
 *
 * Key invariants:
 *   - A string that is not a date is shown as given, never as "Invalid Date".
 *   - `relativeText` is pure (it takes `nowMs`), so the words are testable without a clock.
 *   - Formatting is by hand from the local fields, so it does not change with the browser's locale.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 6.
 */
import type { ReactElement } from 'react';
import { useNowMs } from './clock';

const pad = (n: number): string => String(n).padStart(2, '0');

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const JUST_NOW = 45_000; // Under this either way the stamp says "just now": a tick is 30 s.
const WEEK = 7 * DAY; // Past a week the date says more than a count of days.

// The local date and time to the second; the date part is the text once a week has passed.
export function readable(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

// The gap in words: minutes under an hour, hours under a day, days under a week.
function span(ms: number): string {
  if (ms < HOUR) return `${Math.max(1, Math.round(ms / MINUTE))} min`;
  if (ms < DAY) {
    const hours = Math.floor(ms / HOUR);
    const minutes = Math.round((ms % HOUR) / MINUTE);
    return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
  }
  return `${Math.floor(ms / DAY)} d`;
}

// "just now", "5 min ago", "in 2 h", or the local date once the moment is more than a week off.
export function relativeText(iso: string, nowMs: number): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const gap = nowMs - at;
  if (Math.abs(gap) < JUST_NOW) return 'just now';
  if (Math.abs(gap) >= WEEK) return readable(iso).slice(0, 10);
  return gap > 0 ? `${span(gap)} ago` : `in ${span(-gap)}`;
}

export function TimeStamp(props: { at: string }): ReactElement {
  const nowMs = useNowMs();
  return (
    <time className="time-stamp" dateTime={props.at} title={props.at}>
      {relativeText(props.at, nowMs)}
    </time>
  );
}
