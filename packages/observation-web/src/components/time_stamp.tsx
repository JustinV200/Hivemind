/**
 * `TimeStamp`: a moment as a `<time>` element, local and readable, with the ISO time kept.
 *
 * Every record the views list has an `at`. It is shown as the reader's local date and time down to
 * the second, in tabular figures, and the exact ISO string stays in `dateTime` and `title`, so the
 * text never has to be parsed back out of the page.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by the Thoughts view and the views that
 *   follow it.
 *
 * Key invariants:
 *   - A string that is not a date is shown as given, never as "Invalid Date".
 *   - Formatting is by hand from the local fields, so it does not change with the browser's locale.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 6.
 */
import type { ReactElement } from 'react';

const pad = (n: number): string => String(n).padStart(2, '0');

function readable(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function TimeStamp(props: { at: string }): ReactElement {
  return (
    <time className="time-stamp" dateTime={props.at} title={props.at}>
      {readable(props.at)}
    </time>
  );
}
