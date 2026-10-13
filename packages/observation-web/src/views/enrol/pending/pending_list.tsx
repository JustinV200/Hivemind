/**
 * `PendingList`: the devices that redeemed an invite and wait for the operator, each with Approve and Deny.
 *
 * A card shows what the device said about itself (name, platform, user agent: display text, escaped
 * on draw) and the fingerprint of its key, which is the one fact the operator can check against the
 * device's own screen before letting it in. Approve opens the approval form under the card; Deny
 * refuses it at once, with an optional reason that is recorded as a category on the trail. A
 * device that is not asking (an invite not yet redeemed) is not listed here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/pending/`. Drawn by `enrol_screen.tsx` on the Hive
 *   Stand only; the calls are `actions.approve` and `actions.deny`.
 *
 * Key invariants:
 *   - Nothing a device sent is drawn as markup; the fingerprint is shown in full.
 *   - One approval form is open at a time.
 *   - Deny never needs a reason; the Entrance supplies its own when none is given.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4, step 5.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { TimeStamp } from '../../../components';
import type { PendingDevice } from '../slice';
import type { EnrolActions } from '../use_enrol_actions';
import { ApproveForm } from './approve_form';

function Card(props: {
  device: PendingDevice;
  open: boolean;
  actions: EnrolActions;
  onToggle: () => void;
}): ReactElement {
  const { device, actions } = props;
  const said = device.description;
  return (
    <li className="enrol-pending" aria-label={`Waiting: ${device.name}`}>
      <p>
        <strong>{said?.name || device.name}</strong> asks to join
      </p>
      <dl className="enrol-facts">
        <div>
          <dt>Key fingerprint</dt>
          <dd className="enrol-fingerprint">{device.fingerprint ?? 'not reported'}</dd>
        </div>
        <div>
          <dt>Key</dt>
          <dd>{device.key_kind ?? 'not reported'}</dd>
        </div>
        <div>
          <dt>Platform</dt>
          <dd>{said?.platform || 'not reported'}</dd>
        </div>
        <div>
          <dt>Browser</dt>
          <dd>{said?.user_agent || 'not reported'}</dd>
        </div>
        <div>
          <dt>Asked</dt>
          <dd>
            <TimeStamp at={device.created_at} />
          </dd>
        </div>
      </dl>
      <div className="enrol-row">
        <button
          type="button"
          className="button"
          aria-expanded={props.open}
          aria-label={`Approve ${device.name}`}
          onClick={props.onToggle}
        >
          Approve
        </button>
        <button
          type="button"
          className="button"
          aria-label={`Deny ${device.name}`}
          disabled={actions.busy}
          onClick={() => void actions.deny(device.id, '')}
        >
          Deny
        </button>
      </div>
      {props.open ? (
        <ApproveForm device={device} actions={actions} onClose={props.onToggle} />
      ) : null}
    </li>
  );
}

export function PendingList(props: {
  pending: readonly PendingDevice[];
  actions: EnrolActions;
}): ReactElement {
  const [open, setOpen] = useState<string | null>(null);
  if (props.pending.length === 0) {
    return (
      <p className="enrol-note">
        Nobody is waiting. A device appears here once it has used an invite and made its passkey.
      </p>
    );
  }
  return (
    <ul className="enrol-pending-list">
      {props.pending.map((device) => (
        <Card
          key={device.id}
          device={device}
          open={open === device.id}
          actions={props.actions}
          onToggle={() => setOpen(open === device.id ? null : device.id)}
        />
      ))}
    </ul>
  );
}
