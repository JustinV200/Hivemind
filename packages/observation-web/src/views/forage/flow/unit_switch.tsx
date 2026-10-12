/**
 * `UnitSwitch`: the control that picks what the Forage flow measures.
 *
 * Seats, sub-bees, spend and tokens are different views of the same grants; the switch changes the
 * whole diagram at once. It is a group of toggle buttons (one pressed), so a keyboard user reaches
 * each with Tab and sets it with Enter or Space, and the pressed one is announced, not only drawn.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/flow/`. Drawn by `forage_screen.tsx` beside the
 *   flow; holds no state of its own (the screen keeps the chosen unit).
 *
 * Key invariants:
 *   - Exactly one unit is pressed at any time.
 *   - A control only changes what is drawn; it never writes to the Hive.
 *
 * See Also:
 *   - docs/observation/design.md section 4.7 (the unit switch).
 */
import type { ReactElement } from 'react';
import { FLOW_UNITS } from './model';
import type { FlowUnit } from './model';

export function UnitSwitch(props: {
  unit: FlowUnit;
  onChange: (unit: FlowUnit) => void;
}): ReactElement {
  return (
    <div className="forage-units" role="group" aria-label="Flow unit">
      {FLOW_UNITS.map((choice) => (
        <button
          key={choice.unit}
          type="button"
          className="button"
          aria-pressed={props.unit === choice.unit}
          onClick={() => props.onChange(choice.unit)}
        >
          {choice.label}
        </button>
      ))}
    </div>
  );
}
