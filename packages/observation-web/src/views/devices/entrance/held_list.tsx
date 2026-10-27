/**
 * `HeldList`: requests held for a person's confirmation, each with Confirm and Decline.
 *
 * A device no person types at (a program) cannot step up, so what it asks for that needs it waits
 * here (landing-board.md section 7). A person on an interactive device confirms inside the step-up
 * window, which carries the request out exactly once, or declines it. The payload is shown in full
 * because the human is deciding on it (it is C2: the read needs `entrance:submit`). Confirming is
 * guarded, so a request refused for want of a fresh step-up opens the prompt and is retried once.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/entrance/`. Drawn by `entrance_panel.tsx`.
 *
 * Key invariants:
 *   - Open to any listener, as the document says; nothing here is loopback-only.
 *   - The payload is drawn as escaped text in a `pre`, never as markup.
 *   - A read that was refused is not an empty list: the refusal is shown in its place.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `ConfirmationView`.
 */
import type { ReactElement } from 'react';
import { EmptyState, ErrorNotice, IdToken, TimeStamp } from '../../../components';
import type { DeviceActions } from '../actions/use_device_actions';
import type { DeviceRecord, HeldRecord } from '../slice';

const words = (code: string): string => code.replace(/_/g, ' ');

function HeldItem(props: {
  held: HeldRecord;
  asker: DeviceRecord | undefined;
  actions: DeviceActions;
}): ReactElement {
  const { held, actions } = props;
  return (
    <li className="devices-held comb-cell comb-cell--strong" aria-label={`Held request ${held.id}`}>
      <p>
        <strong>{words(held.action)}</strong> from {props.asker ? props.asker.name : 'a device'}{' '}
        <IdToken id={held.device_id} />
      </p>
      <p className="devices-note">
        Held <TimeStamp at={held.created_at} />, can be confirmed until{' '}
        <TimeStamp at={held.expires_at} />
      </p>
      <details>
        <summary>What it asked for</summary>
        <pre className="devices-payload">{JSON.stringify(held.payload, null, 2)}</pre>
      </details>
      <div className="devices-row">
        <button
          type="button"
          className="button"
          aria-label={`Confirm held request ${held.id}`}
          disabled={actions.busy}
          onClick={() => void actions.confirm(held)}
        >
          Confirm
        </button>
        <button
          type="button"
          className="button"
          aria-label={`Decline held request ${held.id}`}
          disabled={actions.busy}
          onClick={() => void actions.decline(held)}
        >
          Decline
        </button>
      </div>
    </li>
  );
}

export function HeldList(props: {
  held: readonly HeldRecord[] | null;
  error: unknown;
  devices: readonly DeviceRecord[];
  actions: DeviceActions;
}): ReactElement {
  const { held } = props;
  return (
    <section className="devices-section" aria-label="Held requests">
      <h3>Held requests</h3>
      {props.error ? <ErrorNotice error={props.error} /> : null}
      {held === null ? (
        props.error ? null : (
          <p aria-busy="true">Reading the held requests</p>
        )
      ) : held.length === 0 ? (
        <EmptyState>
          Nothing is held. A request waits here when a device that no person types at asks for
          something that needs you to confirm again.
        </EmptyState>
      ) : (
        <ul className="devices-held-list">
          {held.map((one) => (
            <HeldItem
              key={one.id}
              held={one}
              asker={props.devices.find((d) => d.id === one.device_id)}
              actions={props.actions}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
