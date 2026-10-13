/**
 * Test builders and a harness for the Devices and Enrol views: records, a store, a signed-in client.
 *
 * The generated types have many required members that a test about one lock does not care about.
 * These builders fill them with plain defaults and let a test override only what it is about, so
 * each test reads as its point and still type-checks against the generated schema. `openHive` makes
 * the store (the app's table plus the Devices and Enrol slices, which `src/app.tsx` does not hold
 * until the orchestrator registers them) and a client on the fake browser whose stored session
 * names the listener the test wants, then answers the reads both views make.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by every test under tests/views/devices/ and enrol/.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *   - Defaults describe one approved device, `dev_phone`, called "Pixel 9"; the session's own
 *     device is `dev_test`, which the harness stores.
 *
 * See Also:
 *   - src/landing_board/types.ts, generated.
 *   - tests/support/fake_browser.ts for the fake the harness uses.
 */
import { APP_SLICES } from '../../../src/app';
import { createLandingBoard } from '../../../src/landing_board/client';
import type { components } from '../../../src/landing_board/types';
import { createHiveStore } from '../../../src/store';
import type { Hive } from '../../../src/store';
import { devicesSlice } from '../../../src/views/devices/slice';
import { enrolSlice } from '../../../src/views/enrol/slice';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser, RecordedRequest } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];
export type Device = Schemas['DeviceView'];

export function deviceView(over: Partial<Device> = {}): Device {
  return {
    approved_at: '2026-01-01T00:00:00Z',
    backup_eligible: true,
    backup_state: false,
    capabilities: ['observe', 'entrance:submit'],
    certificate_requested: false,
    created_at: '2026-01-01T00:00:00Z',
    description: { name: 'Pixel 9', platform: 'Android 16', user_agent: 'Chrome/150' },
    expires_at: null,
    fingerprint: 'AB:CD:EF',
    id: 'dev_phone',
    interactive: true,
    key_kind: 'passkey',
    last_seen_at: '2026-01-02T03:04:05Z',
    loopback_bound: false,
    name: 'Pixel 9',
    spend_cap_usd_per_day: 5,
    status: 'APPROVED',
    ...over,
  };
}

export function modeView(over: Partial<Schemas['ModeView']> = {}): Schemas['ModeView'] {
  return { exposed: true, mode: 'OPEN', remote_listening: true, ...over };
}

export function heldView(
  over: Partial<Schemas['ConfirmationView']> = {},
): Schemas['ConfirmationView'] {
  return {
    action: 'goal',
    created_at: '2026-01-02T00:00:00Z',
    device_id: 'dev_bot',
    expires_at: '2026-01-02T00:15:00Z',
    id: 'pend_1',
    payload: { text: 'rebuild the garden', budget_usd: 50 },
    status: 'PENDING',
    ...over,
  };
}

// One Entrance security event, as `/v1/entrance/stream` carries it.
export function securityFrame(kind = 'guard.entrance_locked'): Schemas['SecurityFrame'] {
  return {
    type: 'security_event',
    event: {
      actor: 'human',
      at: '2026-01-02T00:00:00Z',
      id: `evt_${kind}`,
      kind,
      payload: {},
      subject_id: 'dev_phone',
    },
  };
}

export interface Setup {
  listener?: 'loopback' | 'remote';
  needsStepUp?: boolean;
  devices?: Device[];
  mode?: Schemas['ModeView'];
  held?: Schemas['ConfirmationView'][];
  pending?: Device[];
}

export interface OpenedHive {
  fake: FakeBrowser;
  hive: Hive;
  // `METHOD /path` of each request made so far, query dropped.
  paths: () => string[];
  // The requests made so far to `METHOD /path`.
  sent: (key: string) => RecordedRequest[];
}

export const bodyOf = (request: RecordedRequest): unknown =>
  JSON.parse(new TextDecoder().decode(request.body!));

export async function openHive(setup: Setup = {}): Promise<OpenedHive> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser, {
    listener: setup.listener ?? 'remote',
    needsStepUp: setup.needsStepUp ?? false,
  });
  fake.respond('GET /v1/devices', 200, { devices: setup.devices ?? [deviceView()] });
  fake.respond('GET /v1/entrance/mode', 200, setup.mode ?? modeView());
  fake.respond('GET /v1/entrance/confirmations', 200, { confirmations: setup.held ?? [] });
  fake.respond('GET /v1/entrance/pending', 200, { devices: setup.pending ?? [] });
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  const session = await client.session.restore();
  const store = createHiveStore({ ...APP_SLICES, devices: devicesSlice, enrol: enrolSlice });
  store.dispatch({ slice: 'session', event: { type: 'restored', session } });
  const paths = (): string[] => fake.requests.map((r) => `${r.method} ${r.target.split('?')[0]}`);
  const sent = (key: string): RecordedRequest[] =>
    fake.requests.filter((r) => `${r.method} ${r.target.split('?')[0]}` === key);
  return { fake, hive: { store, client }, paths, sent };
}
