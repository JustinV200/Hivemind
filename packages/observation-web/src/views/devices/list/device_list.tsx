/**
 * `DeviceList`: every enrolled device, oldest first, with where each last connected and what it may do.
 *
 * Every device sees the list (it needs only `observe`). The Controls column, and every control in
 * it, is drawn only when the session's listener is `loopback`: lock, unlock, revoke and the
 * capability editor are Hive Stand decisions. Under 1080 px the table becomes stacked cards by
 * stylesheet alone, each cell keeping its label. An empty list says what would fill it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/list/`. Drawn by `devices_screen.tsx`.
 *
 * Key invariants:
 *   - Rows are keyed by the device id, so a security frame updates a row in place.
 *   - The Controls header and cells appear together or not at all.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 5.10.
 */
import type { ReactElement } from 'react';
import type { DeviceActions } from '../actions/use_device_actions';
import type { DeviceRecord } from '../slice';
import { DeviceRow } from './device_row';
import type { EditKind } from './device_row';

const HEADERS = [
  'Device',
  'Standing',
  'Key',
  'May do',
  'Spend cap',
  'Expires',
  'Last seen',
  'Platform',
] as const;

export function DeviceList(props: {
  devices: readonly DeviceRecord[];
  selfId: string | null;
  loopback: boolean;
  actions: DeviceActions;
  onEdit: (device: DeviceRecord, kind: EditKind) => void;
}): ReactElement {
  if (props.devices.length === 0) {
    return (
      <p className="devices-note">
        No devices yet. A device appears here when the operator mints an invite for it at the Hive
        Stand.
      </p>
    );
  }
  return (
    <table className="devices-table">
      <caption>Enrolled devices and when each last authenticated</caption>
      <thead>
        <tr>
          {HEADERS.map((label) => (
            <th key={label} scope="col">
              {label}
            </th>
          ))}
          {props.loopback ? (
            <th scope="col" className="devices-controls">
              Controls
            </th>
          ) : null}
        </tr>
      </thead>
      <tbody>
        {props.devices.map((device) => (
          <DeviceRow
            key={device.id}
            device={device}
            isSelf={device.id === props.selfId}
            loopback={props.loopback}
            actions={props.actions}
            onEdit={(kind) => props.onEdit(device, kind)}
          />
        ))}
      </tbody>
    </table>
  );
}
