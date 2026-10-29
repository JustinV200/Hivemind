/**
 * `CapabilityPicker`: choose what a device may do from the known set, as labelled checkboxes.
 *
 * The approve and capability forms used to ask for raw codes, one per line, with no list of the
 * valid values. The picker lists every capability the Hive Stand console holds, each as its plain
 * phrase with the code beside it, and keeps any capability the device already holds that the list
 * does not know (an unknown code is still the device's; dropping it silently would narrow the
 * device). The chosen set is handed back in the list's order, so the body the Entrance gets is
 * the same whichever box was ticked last.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/list/`. Drawn by `capabilities_form.tsx` and
 *   by the Enrol view's `pending/approve_form.tsx`.
 *
 * Key invariants:
 *   - Holds no state: the form owns the chosen set and hears every change.
 *   - Every box has a visible label: the phrase and the code, never the code alone.
 *
 * See Also:
 *   - src/components/words/enums.ts for the known set and the phrases.
 */
import type { ReactElement } from 'react';
import { capabilityWords, KNOWN_CAPABILITIES } from '../../../components';

export function CapabilityPicker(props: {
  legend: string;
  // One line under the legend saying what an empty choice means, when it means something.
  hint?: string;
  chosen: readonly string[];
  onChange: (chosen: string[]) => void;
}): ReactElement {
  const { chosen } = props;
  // The known set first, then what the device holds beyond it, so nothing it has is hidden.
  const offered = [...KNOWN_CAPABILITIES, ...chosen.filter((c) => !KNOWN_CAPABILITIES.includes(c))];
  const toggle = (capability: string, on: boolean): void =>
    props.onChange(offered.filter((c) => (c === capability ? on : chosen.includes(c))));
  return (
    <fieldset className="devices-picker">
      <legend>{props.legend}</legend>
      {props.hint ? <p className="devices-note">{props.hint}</p> : null}
      {offered.map((capability) => (
        <label key={capability} className="devices-check">
          <input
            type="checkbox"
            checked={chosen.includes(capability)}
            onChange={(event) => toggle(capability, event.target.checked)}
          />
          {capabilityWords(capability)}
        </label>
      ))}
    </fieldset>
  );
}
