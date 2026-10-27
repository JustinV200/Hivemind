/**
 * `DeviceRow`: one enrolled device as a table row (a card on a phone), with its door controls.
 *
 * The row shows the device's name (marked when it is the device in your hand, and when it is the
 * Hive Stand's own console), its standing as a word, the kind of key, what it may do, its daily
 * spend cap, when its standing lapses, when it last authenticated and what it said its platform
 * is. The Landing Board does not record the network address a device connected from, so the row
 * does not claim one. On the Hive Stand an approved device offers Lock, Edit capabilities and
 * Revoke, and a locked one Unlock and Revoke; the device in your hand offers none, so no one
 * locks themselves out by accident.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/list/`. Drawn by `device_list.tsx`.
 *
 * Key invariants:
 *   - The door controls exist only when `loopback` is true; the router and the Entrance also
 *     refuse them elsewhere, this is the third layer.
 *   - Status is a word as well as a colour; display text from a device is escaped on draw.
 *
 * See Also:
 *   - docs/observation/design.md section 5.10.
 */
import type { ReactElement } from 'react';
import { TimeStamp } from '../../../components';
import type { DeviceActions } from '../actions/use_device_actions';
import type { DeviceRecord } from '../slice';
import { capabilityWords, platformWords, spendCapWords, statusWord } from './format';

export type EditKind = 'revoke' | 'capabilities';

function Controls(props: {
  device: DeviceRecord;
  actions: DeviceActions;
  onEdit: (kind: EditKind) => void;
}): ReactElement | null {
  const { device, actions } = props;
  const named = (verb: string): string => `${verb} ${device.name}`;
  const button = (label: string, aria: string, run: () => void, danger = false): ReactElement => (
    <button
      type="button"
      className={danger ? 'button button--danger' : 'button'}
      aria-label={aria}
      disabled={actions.busy}
      onClick={run}
    >
      {label}
    </button>
  );
  // Revoke ends the device for good, so it is outlined in the alarm token; its form confirms.
  const revoke = button('Revoke', named('Revoke'), () => props.onEdit('revoke'), true);
  if (device.status === 'APPROVED') {
    return (
      <>
        {button('Lock', named('Lock'), () => void actions.lock(device))}
        {button('Edit capabilities', `Edit capabilities of ${device.name}`, () =>
          props.onEdit('capabilities'),
        )}
        {revoke}
      </>
    );
  }
  if (device.status === 'LOCKED') {
    return (
      <>
        {button('Unlock', named('Unlock'), () => void actions.unlock(device))}
        {revoke}
      </>
    );
  }
  return null;
}

export function DeviceRow(props: {
  device: DeviceRecord;
  isSelf: boolean;
  loopback: boolean;
  actions: DeviceActions;
  onEdit: (kind: EditKind) => void;
}): ReactElement {
  const { device } = props;
  return (
    <tr>
      <td data-label="Device">
        <strong>{device.name}</strong>
        {props.isSelf ? <span className="devices-tag">this device</span> : null}
        {device.loopback_bound ? <span className="devices-tag">Hive Stand</span> : null}
      </td>
      <td data-label="Standing">
        <span className={`devices-status devices-status--${device.status.toLowerCase()}`}>
          {statusWord(device.status)}
        </span>
      </td>
      <td data-label="Key">{device.key_kind ?? 'not yet'}</td>
      <td data-label="May do">{capabilityWords(device.capabilities)}</td>
      <td data-label="Spend cap">{spendCapWords(device.spend_cap_usd_per_day)}</td>
      <td data-label="Expires">
        {device.expires_at ? <TimeStamp at={device.expires_at} /> : 'does not expire'}
      </td>
      <td data-label="Last seen">
        {device.last_seen_at ? <TimeStamp at={device.last_seen_at} /> : 'never'}
      </td>
      <td data-label="Platform" title={device.description?.user_agent || undefined}>
        {platformWords(device)}
      </td>
      {props.loopback ? (
        <td data-label="Controls" className="devices-controls">
          {props.isSelf ? null : (
            <Controls device={device} actions={props.actions} onEdit={props.onEdit} />
          )}
        </td>
      ) : null}
    </tr>
  );
}
