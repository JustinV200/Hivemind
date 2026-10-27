/**
 * `CapabilitiesForm`: replace what one approved device may do, and optionally its daily spend cap.
 *
 * The Entrance takes the device's whole new capability set (inside the device role's ceiling, and
 * only after step-up), not a change to the old one, so the form starts from the current set and the
 * human edits the text. A blank spend cap keeps the cap the device has. The set must name at least
 * one capability; a device that may do nothing is a revoke, which has its own confirmation.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/list/`. Drawn by `devices_screen.tsx` under the
 *   list, on the Hive Stand only; it calls `actions.widen` (guarded, so step-up is asked for).
 *
 * Key invariants:
 *   - The body is exactly `WidenBody`: `capabilities` and `spend_cap_usd_per_day`.
 *   - Nothing is sent for an empty set or a negative cap; the form says why instead.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `WidenBody`.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import type { DeviceActions } from '../actions/use_device_actions';
import type { DeviceRecord } from '../slice';
import { parseCapabilities } from './format';

export function CapabilitiesForm(props: {
  device: DeviceRecord;
  actions: DeviceActions;
  onClose: () => void;
}): ReactElement {
  const { device, actions } = props;
  const [text, setText] = useState(device.capabilities.join('\n'));
  const [cap, setCap] = useState(
    device.spend_cap_usd_per_day === null ? '' : String(device.spend_cap_usd_per_day),
  );
  const capabilities = parseCapabilities(text);
  const spend = cap.trim() === '' ? null : Number(cap);
  const invalid = capabilities.length === 0 || (spend !== null && !(spend >= 0));

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (invalid) return;
    if (await actions.widen(device, capabilities, spend)) props.onClose();
  }

  return (
    <form
      className="devices-form comb-cell comb-cell--strong"
      aria-label={`Capabilities of ${device.name}`}
      onSubmit={(event) => void submit(event)}
    >
      <h2>What {device.name} may do</h2>
      <label>
        Capabilities, one per line
        <textarea rows={4} value={text} onChange={(event) => setText(event.target.value)} />
      </label>
      <label>
        Spend cap per day in USD, blank to keep it
        <input
          type="number"
          min="0"
          step="0.01"
          inputMode="decimal"
          value={cap}
          onChange={(event) => setCap(event.target.value)}
        />
      </label>
      {capabilities.length === 0 ? (
        <p className="devices-note">Name at least one capability, or revoke the device instead.</p>
      ) : null}
      <div className="devices-row">
        <button type="submit" className="button" disabled={actions.busy || invalid}>
          Save capabilities
        </button>
        <button type="button" className="button" onClick={props.onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}
