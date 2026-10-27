/**
 * `RevokeForm`: revoke one device, after typing its name, with the option to cancel its goals.
 *
 * Revoking is the destructive door action: it ends every session of the device at once and it
 * cannot be undone (the device would have to enrol again). So the button stays disabled until the
 * human has typed the device's name exactly, the same guard a break-glass action has. The toggle
 * also cancels the device's open goals in the same step, as the roadmap's revoke screen offers
 * (10.5d); without it a goal already placed on a Warden keeps running.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/list/`. Drawn by `devices_screen.tsx` under the
 *   list, on the Hive Stand only; it calls `actions.revoke` and closes when that works.
 *
 * Key invariants:
 *   - Nothing is sent until the typed text equals the device's name.
 *   - The cancel-goals toggle starts off: cancelling placed work is a second decision.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `RevokeBody` and `RevocationView`.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import type { DeviceActions } from '../actions/use_device_actions';
import type { DeviceRecord } from '../slice';

export function RevokeForm(props: {
  device: DeviceRecord;
  actions: DeviceActions;
  onClose: () => void;
}): ReactElement {
  const { device, actions } = props;
  const [typed, setTyped] = useState('');
  const [cancelGoals, setCancelGoals] = useState(false);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (typed !== device.name) return;
    if (await actions.revoke(device, cancelGoals)) props.onClose();
  }

  return (
    <form
      className="devices-form comb-cell comb-cell--strong"
      aria-label={`Revoke device ${device.name}`}
      onSubmit={(event) => void submit(event)}
    >
      <h2>Revoke {device.name}</h2>
      <p>
        This ends every session of the device now and cannot be undone: it would have to enrol
        again. Type its name to confirm.
      </p>
      <label>
        Type {device.name} to confirm
        <input
          autoComplete="off"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
      </label>
      <label className="devices-check">
        <input
          type="checkbox"
          checked={cancelGoals}
          onChange={(event) => setCancelGoals(event.target.checked)}
        />
        Also cancel its open goals
      </label>
      <div className="devices-row">
        <button
          type="submit"
          className="button button--danger"
          disabled={actions.busy || typed !== device.name}
        >
          Revoke device
        </button>
        <button type="button" className="button" onClick={props.onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}
