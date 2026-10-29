/**
 * `EntrancePanel`: remote access (the Entrance's mode), the Reducer, the reopen flow and the held
 * requests.
 *
 * The Entrance is either OPEN (it serves its remote listener) or REDUCED (loopback only, every
 * remote session ended). The panel says this as "Remote access: on" or "reduced, the Hive Stand
 * only", with one line on what reducing does, since this is a safety control and its schema words
 * (mode, listener, exposed) are the densest jargon in the app. Reducing narrows access, so any
 * device may do it, after a second tap that
 * says what it ends (including this device's own session when it is remote). Reopening widens it,
 * so only the Hive Stand offers it, and the Entrance asks for a fresh step-up first: the guarded
 * write opens the prompt. A remote device looking at a reduced Entrance is told where to reopen it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/entrance/`. Drawn by `devices_screen.tsx` under
 *   the list; it calls `actions.reduce`, `actions.reopen` and, through the held list, the rest.
 *
 * Key invariants:
 *   - The reopen control exists only when `loopback` is true.
 *   - Reduce needs two deliberate taps; reopen is a single guarded tap (step-up is its guard).
 *   - The mode is a word, never a colour alone.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `ModeView` and `ChangedView`.
 *   - .claude/codingrules.md section 8.15 (the Entrance Reducer).
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import type { DeviceActions } from '../actions/use_device_actions';
import type { DevicesState } from '../slice';
import { HeldList } from './held_list';

function Reducer(props: { actions: DeviceActions }): ReactElement {
  const [arming, setArming] = useState(false);
  const { actions } = props;
  if (!arming) {
    return (
      <button type="button" className="button button--danger" onClick={() => setArming(true)}>
        Reduce to the Hive Stand only
      </button>
    );
  }
  return (
    <div
      className="devices-arming comb-cell comb-cell--strong"
      role="group"
      aria-label="Reduce to the Hive Stand only"
    >
      <p>
        This ends every remote session, including this one if it is remote, and the Entrance answers
        on the Hive Stand only until it is reopened there.
      </p>
      <div className="devices-row">
        <button
          type="button"
          className="button button--danger"
          disabled={actions.busy}
          onClick={() => void actions.reduce().then(() => setArming(false))}
        >
          Reduce now
        </button>
        <button type="button" className="button" onClick={() => setArming(false)}>
          Keep it open
        </button>
      </div>
    </div>
  );
}

// The mode in one line: on (and whether its door is serving right now), or reduced.
function accessWords(mode: NonNullable<DevicesState['mode']>): string {
  if (mode.mode !== 'OPEN') return 'reduced, the Hive Stand only';
  return mode.remote_listening ? 'on (serving)' : 'on (the remote door is not serving)';
}

function ModeLines(props: { mode: NonNullable<DevicesState['mode']> }): ReactElement {
  const { mode } = props;
  return (
    <dl className="devices-facts">
      <div>
        <dt>Remote access</dt>
        <dd>{accessWords(mode)}</dd>
      </div>
      <div>
        <dt>Configured</dt>
        <dd>
          {mode.exposed ? 'the manifest asks for a remote door' : 'no remote door in the manifest'}
        </dd>
      </div>
    </dl>
  );
}

export function EntrancePanel(props: {
  state: DevicesState;
  loopback: boolean;
  actions: DeviceActions;
}): ReactElement {
  const { state, loopback, actions } = props;
  const { mode } = state;
  return (
    <section className="devices-section" aria-label="Entrance">
      <h2 className="section-heading">Remote access</h2>
      <p className="devices-note">
        Devices away from the Hive Stand come in through the Entrance&apos;s remote door. Reducing
        closes it and ends every remote session; only the Hive Stand can open it again.
      </p>
      {state.errors.mode ? <ErrorNotice error={state.errors.mode} /> : null}
      {mode ? <ModeLines mode={mode} /> : <p aria-busy="true">Reading the Entrance</p>}
      {mode?.mode === 'OPEN' ? <Reducer actions={actions} /> : null}
      {mode?.mode === 'REDUCED' && loopback ? (
        <button
          type="button"
          className="button"
          disabled={actions.busy}
          onClick={() => void actions.reopen()}
        >
          Reopen the Entrance
        </button>
      ) : null}
      {mode?.mode === 'REDUCED' && !loopback ? (
        <p className="devices-note">Only the Hive Stand can reopen the Entrance.</p>
      ) : null}
      <HeldList
        held={state.held}
        error={state.errors.confirmations}
        devices={state.devices}
        actions={actions}
      />
    </section>
  );
}
