/**
 * `Gauge`: a horizontal bar for used against a budget, with the numbers beside it in text.
 *
 * The same bar draws a bee's context window, a grant used against issued and a cost against its
 * cap. The fill is `--ok` up to 70 percent, `--warn` to 90 percent and `--alarm` above, but the
 * numbers (`41,200 / 128,000 (32%)`) carry the meaning too, so colour is never the only signal.
 * The bar is a `role="meter"` so a screen reader gets the value, minimum and maximum.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by the supervision tree, the Forage view
 *   and the Cost view in later steps; styled by `src/styles/components.css`.
 *
 * Key invariants:
 *   - A budget of zero or less draws an empty bar and 0 percent, never NaN.
 *   - The fill width is clamped to the track, though the text shows the true percentage, so an
 *     overrun reads as over 100 percent.
 *
 * See Also:
 *   - docs/observation/design.md section 4.2.
 */
import type { ReactElement } from 'react';

const WARN_AT = 70;
const ALARM_AT = 90;

function tone(percent: number): 'ok' | 'warn' | 'alarm' {
  if (percent > ALARM_AT) return 'alarm';
  return percent >= WARN_AT ? 'warn' : 'ok';
}

const number = (n: number): string => n.toLocaleString('en-US');

export function Gauge(props: { used: number; budget: number; label: string }): ReactElement {
  const { used, budget, label } = props;
  const percent = budget > 0 ? Math.round((used / budget) * 100) : 0;
  return (
    <div className="gauge">
      <div
        className="gauge-track"
        role="meter"
        aria-label={label}
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={budget}
      >
        <div
          className={`gauge-fill gauge-fill--${tone(percent)}`}
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
      <span className="gauge-text">{`${number(used)} / ${number(budget)} (${percent}%)`}</span>
    </div>
  );
}
