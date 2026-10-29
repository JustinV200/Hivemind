/**
 * The Devices view: who is enrolled, what each may do, the Entrance's mode and the held requests.
 *
 * At `#/devices`, on every device. The list shows each enrolled device with its standing, what it
 * may do, its spend cap, when it last authenticated and what it said its platform is, and marks the
 * device in your hand. On the Hive Stand (the session's listener is `loopback`) each row also
 * offers lock, unlock, revoke (after typing the device's name) and a capability editor. Below the
 * list sits the Entrance panel: the mode, the Entrance Reducer, its reopen flow on the Hive Stand,
 * and the requests held for a person. Anything the Entrance answers `403 step_up_required` opens
 * the shared step-up prompt and is retried once. It seeds from three reads and stays live through
 * the Entrance's security stream.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/`. Chosen by the outlet in `src/shell/` when the
 *   route is `devices`. Built to docs/observation/design.md section 5.10.
 *
 * Key invariants:
 *   - Every control that calls a loopback-only route is drawn only when the listener is `loopback`;
 *     the Entrance 404s those routes on the remote listener regardless.
 *   - The device in your hand offers no lock or revoke.
 *   - A refusal is the error table's sentence through `ErrorNotice`, never an empty screen.
 *
 * See Also:
 *   - src/views/devices/README.md, src/views/devices/use_devices.ts for how it stays live.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice, VIEW_HINTS, ViewTitle } from '../../components';
import { useSlice } from '../../store';
import { useDeviceActions } from './actions/use_device_actions';
import './devices.css';
import { EntrancePanel } from './entrance/entrance_panel';
import { CapabilitiesForm } from './list/capabilities_form';
import { DeviceList } from './list/device_list';
import type { EditKind } from './list/device_row';
import { RevokeForm } from './list/revoke_form';
import { StepUpPanel, StepUpPrompt, useStepUp } from './step_up';
import { useDevices } from './use_devices';

interface Editing {
  id: string;
  kind: EditKind;
}

export function DevicesScreen(): ReactElement {
  const state = useDevices();
  const { listener, deviceId } = useSlice('session');
  const loopback = listener === 'loopback';
  const step = useStepUp();
  const actions = useDeviceActions(step);
  const [editing, setEditing] = useState<Editing | null>(null);
  const target = state.devices.find((device) => device.id === editing?.id);
  const close = (): void => setEditing(null);

  return (
    <section className="devices">
      <ViewTitle title="Devices" hint={VIEW_HINTS.devices} />
      <StepUpPanel />
      <StepUpPrompt step={step} />
      {actions.notice ? (
        <p className="devices-notice" role="status">
          {actions.notice}
        </p>
      ) : null}
      {actions.error ? <ErrorNotice error={actions.error} /> : null}
      {state.errors.devices ? <ErrorNotice error={state.errors.devices} /> : null}
      {state.seeded ? (
        <DeviceList
          devices={state.devices}
          selfId={deviceId}
          loopback={loopback}
          actions={actions}
          onEdit={(device, kind) => {
            actions.clear();
            setEditing({ id: device.id, kind });
          }}
        />
      ) : state.errors.devices ? null : (
        <p aria-busy="true">Reading the devices</p>
      )}
      {loopback && target && editing?.kind === 'revoke' ? (
        <RevokeForm key={target.id} device={target} actions={actions} onClose={close} />
      ) : null}
      {loopback && target && editing?.kind === 'capabilities' ? (
        <CapabilitiesForm key={target.id} device={target} actions={actions} onClose={close} />
      ) : null}
      <EntrancePanel state={state} loopback={loopback} actions={actions} />
    </section>
  );
}
