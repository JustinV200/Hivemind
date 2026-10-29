/**
 * The Enrol view: let a new device in. Invite it, then approve or deny it, on the Hive Stand.
 *
 * At `#/enrol`, on the loopback listener only. The operator names a device and mints an invite
 * (a code, a link and a QR code, good once and for a short time), hands it to the device, and the
 * device appears in the waiting list as soon as it has redeemed the invite, live through the
 * Entrance's security stream. Approving binds the device's name, capabilities, daily spend cap and
 * expiry; denying refuses it. Below them, the tasks waiting for the owner's yes to use the running
 * screen or audio (ADR-0069), each allowed or refused here, on the Hive Stand, and nowhere else.
 * Anything the Entrance answers `403 step_up_required` opens the shared
 * step-up prompt and is retried once. A session on any other listener sees "Not found" and makes no
 * request, as a second layer behind the router and the Entrance's own 404.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/`. Chosen by the outlet in `src/shell/` when the
 *   route is `enrol`. Built to docs/observation/design.md section 5.10.
 *
 * Key invariants:
 *   - Nothing here is mounted unless the session's listener is `loopback`, so no hook of the body
 *     runs, and no loopback-only route is called, anywhere else.
 *   - Approve, deny, mint, withdraw and a watch answer are the only writes; a test lists them.
 *   - A refusal is the error table's sentence through `ErrorNotice`, never an empty screen.
 *
 * See Also:
 *   - src/views/enrol/README.md, src/views/enrol/use_enrol.ts for how it stays live.
 */
import type { ReactElement } from 'react';
import { ErrorNotice, ViewTitle } from '../../components';
import { useSlice } from '../../store';
import { StepUpPanel, StepUpPrompt, useStepUp } from '../devices';
import './enrol.css';
import { InvitePanel } from './invite/invite_panel';
import { BundleNotice } from './pending/bundle_notice';
import { PendingList } from './pending/pending_list';
import { useEnrol } from './use_enrol';
import { useEnrolActions } from './use_enrol_actions';
import { useWatch } from './watch/use_watch';
import { WatchList } from './watch/watch_list';

function Body(): ReactElement {
  const state = useEnrol();
  const step = useStepUp();
  const actions = useEnrolActions(step);
  const watch = useWatch();
  return (
    <section className="enrol">
      <ViewTitle title="Enrol" />
      <StepUpPanel />
      <StepUpPrompt step={step} />
      {actions.notice ? (
        <p className="enrol-notice" role="status">
          {actions.notice}
        </p>
      ) : null}
      {actions.error ? <ErrorNotice error={actions.error} /> : null}
      {actions.bundle ? (
        <BundleNotice bundle={actions.bundle} onDismiss={actions.dismissBundle} />
      ) : null}
      <InvitePanel invite={state.invite} actions={actions} />
      <section className="enrol-section" aria-label="Waiting to join">
        <h2 className="section-heading">Waiting to join</h2>
        {state.error ? <ErrorNotice error={state.error} /> : null}
        {state.seeded ? (
          <PendingList pending={state.pending} actions={actions} />
        ) : state.error ? null : (
          <p aria-busy="true">Reading the waiting devices</p>
        )}
      </section>
      <section className="enrol-section" aria-label="Waiting to use your screen or sound">
        <h2 className="section-heading">Waiting to use your screen or sound</h2>
        {watch.error ? <ErrorNotice error={watch.error} /> : null}
        {watch.seeded ? (
          <WatchList requests={watch.requests} actions={actions} onAnswered={watch.again} />
        ) : watch.error ? null : (
          <p aria-busy="true">Reading the waiting tasks</p>
        )}
        <div className="enrol-row">
          <button type="button" className="button" onClick={watch.again}>
            Check again
          </button>
        </div>
      </section>
    </section>
  );
}

export function EnrolScreen(): ReactElement {
  const { listener } = useSlice('session');
  // The router already hides this route off the Hive Stand; this is the same rule one layer in.
  return listener === 'loopback' ? <Body /> : <ViewTitle title="Not found" />;
}
