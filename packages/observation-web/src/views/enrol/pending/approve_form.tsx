/**
 * `ApproveForm`: approve one waiting device, binding its name, capabilities, spend cap and expiry.
 *
 * Approval is the decision that lets a device in (landing-board.md section 4). The form starts from
 * what the device asked for: the name it proposed, the capabilities proposed for its role (ticked
 * in the known list, `CapabilityPicker`) and the cap on record. Nothing ticked asks the Entrance
 * for the device role's own proposed set,
 * and the Entrance refuses anything wider than that role's ceiling. The spend cap is required, in
 * USD per day. A program key (Ed25519) can also be marked as one a person types at, so it may step
 * up; a browser's passkey device always can.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/pending/`. Drawn by `pending_list.tsx` on the Hive
 *   Stand only; it calls `actions.approve` and closes when that works.
 *
 * Key invariants:
 *   - The body is exactly `ApprovalBody`; `interactive` is true only for an Ed25519 key marked so.
 *   - Nothing is sent without a name or with a negative or missing spend cap.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `ApprovalBody`.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { CapabilityPicker } from '../../devices';
import type { PendingDevice } from '../slice';
import type { ApprovalBody, EnrolActions } from '../use_enrol_actions';
import { EXPIRY_CHOICES, expiryFrom } from './expiry';

export function ApproveForm(props: {
  device: PendingDevice;
  actions: EnrolActions;
  onClose: () => void;
}): ReactElement {
  const { device, actions } = props;
  const [name, setName] = useState(device.description?.name || device.name);
  const [capabilities, setCapabilities] = useState<string[]>([...device.capabilities]);
  const [cap, setCap] = useState(String(device.spend_cap_usd_per_day ?? 0));
  const [expiry, setExpiry] = useState('30');
  const [interactive, setInteractive] = useState(false);
  const spend = Number(cap);
  const invalid = name.trim() === '' || cap.trim() === '' || !(spend >= 0);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (invalid) return;
    const chosen = EXPIRY_CHOICES.find((one) => one.key === expiry) ?? EXPIRY_CHOICES[0]!;
    const body: ApprovalBody = {
      name: name.trim(),
      spend_cap_usd_per_day: spend,
      // Nothing ticked asks for the device role's proposed set.
      capabilities: capabilities.length > 0 ? capabilities : null,
      expires_at: expiryFrom(chosen, Date.now()) ?? null,
      // Only a program key can be one a person types at; a passkey always is, whatever this says.
      interactive: device.key_kind === 'ed25519' && interactive,
    };
    if (await actions.approve(device.id, body)) props.onClose();
  }

  return (
    <form
      className="enrol-form comb-cell comb-cell--strong"
      aria-label={`Approve ${device.name}`}
      onSubmit={(event) => void submit(event)}
    >
      <label>
        Name
        <input
          maxLength={64}
          required
          autoComplete="off"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <CapabilityPicker
        legend="Capabilities"
        hint="Untick everything to give the usual set for this kind of device."
        chosen={capabilities}
        onChange={setCapabilities}
      />
      <label>
        Spend cap per day in USD
        <input
          type="number"
          min="0"
          step="0.01"
          inputMode="decimal"
          required
          value={cap}
          onChange={(event) => setCap(event.target.value)}
        />
      </label>
      <label>
        Expires
        <select value={expiry} onChange={(event) => setExpiry(event.target.value)}>
          {EXPIRY_CHOICES.map((choice) => (
            <option key={choice.key} value={choice.key}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>
      {device.key_kind === 'ed25519' ? (
        <label className="enrol-check">
          <input
            type="checkbox"
            checked={interactive}
            onChange={(event) => setInteractive(event.target.checked)}
          />
          A person types the password at this device
        </label>
      ) : null}
      <div className="enrol-row">
        <button type="submit" className="button" disabled={actions.busy || invalid}>
          Approve device
        </button>
        <button type="button" className="button" onClick={props.onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}
