/**
 * `InvitePanel`: mint an invite for a new device, and show the one just minted.
 *
 * The operator names the device ("phone") and mints; the Entrance answers with a single-use code, a
 * link and a QR code that stop working after the invite's time-to-live. Until the minted invite is
 * redeemed or withdrawn the card stays on screen and the mint form is hidden, so one invite is
 * shown at a time and the operator is never unsure which code belongs to which device.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/invite/`. Drawn by `enrol_screen.tsx` on the Hive
 *   Stand only; it calls `actions.mint` (loopback-only on the Entrance too).
 *
 * Key invariants:
 *   - The label is required and at most 64 characters, as the document asks.
 *   - One invite is shown at a time.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `InviteRequest` and `InviteView`.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import type { InviteRecord } from '../slice';
import type { EnrolActions } from '../use_enrol_actions';
import { InviteCard } from './invite_card';

export function InvitePanel(props: {
  invite: InviteRecord | null;
  actions: EnrolActions;
}): ReactElement {
  const { invite, actions } = props;
  const [label, setLabel] = useState('');

  async function mint(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (label.trim() === '') return;
    if (await actions.mint(label.trim())) setLabel('');
  }

  return (
    <section className="enrol-section" aria-label="Invite a device">
      <h2>Invite a device</h2>
      {invite ? (
        <InviteCard invite={invite} actions={actions} />
      ) : (
        <form
          className="enrol-form comb-cell comb-cell--strong"
          onSubmit={(event) => void mint(event)}
        >
          <label>
            What do you call the device
            <input
              maxLength={64}
              required
              placeholder="phone"
              autoComplete="off"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </label>
          <button type="submit" className="button" disabled={actions.busy || label.trim() === ''}>
            Mint invite
          </button>
        </form>
      )}
    </section>
  );
}
