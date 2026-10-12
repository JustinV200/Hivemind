/**
 * Test support for the Fleet view: row and answer builders, and a mounted view on a fake browser.
 *
 * The generated `FleetRow` has many required members that a test about sorting does not care
 * about. `fleetRow` fills them with plain defaults and lets a test override only what it is about.
 * `openFleet` builds the store and client, scripts `GET /v1/fleet`, mounts `FleetView` and opens
 * its Cells socket, so a test starts from a seeded view and a live stream.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the Fleet view's tests; the store is built with the
 *   Fleet slice added explicitly, so these tests hold whether or not `src/app.tsx` lists it yet.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders at
 *     compile time.
 *   - An unrouted request answers 404; the view's reads are the only ones scripted.
 *
 * See Also:
 *   - tests/views/fleet/fleet_view.test.tsx and slice.test.ts.
 */
import { act, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { vi } from 'vitest';
import { APP_SLICES } from '../../../src/app';
import { createLandingBoard } from '../../../src/landing_board/client';
import type { components } from '../../../src/landing_board/types';
import { createHiveStore, HiveProvider } from '../../../src/store';
import type { AppSlices, HiveStore } from '../../../src/store';
import { FleetView, fleetSlice } from '../../../src/views/fleet';
import { createFakeBrowser, flush } from '../../support/fake_browser';
import type { FakeBrowser, FakeSocket } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];

export function fleetRow(row: Partial<Schemas['FleetRow']> = {}): Schemas['FleetRow'] {
  const id = row.id ?? 'cell_1';
  return {
    access_level: 'SCRATCH',
    comb_shield: 'MEADOW',
    current_task_id: null,
    current_task_role: null,
    highest_wax_severity: null,
    id,
    kind: 'VIRTUAL',
    lease_age_s: null,
    links: { attendant_principal: null, cell_id: id, thought_principals: [] },
    mask_state: null,
    mode: null,
    models_from: 'unknown',
    name: 'cell',
    open_alarms: 0,
    source: 'local',
    status: 'READY',
    warden_id: null,
    warden_runs: null,
    ...row,
  };
}

export const LEGEND: Schemas['TierLegendEntry'][] = [
  { tier: 'MEADOW', meaning: 'Open ground: a throwaway Cell with nothing private on it.' },
  { tier: 'PROPOLIS', meaning: 'Sealed against the outside: private work, no secrets.' },
  { tier: 'NIGHT_VEIL', meaning: 'Sealed and dark: secrets live here, nothing leaves.' },
];

export function fleetAnswer(
  rows: Schemas['FleetRow'][],
  legend: Schemas['TierLegendEntry'][] = LEGEND,
): Schemas['FleetView'] {
  return { as_of: '2026-01-01T00:00:00Z', legend, rows };
}

export interface OpenedFleet {
  fake: FakeBrowser;
  socket: FakeSocket;
  store: HiveStore<AppSlices>;
  // How many times the view has asked for the aggregate so far (counted at the call, so a read
  // that has been asked for but not yet signed and sent still counts).
  reads: () => number;
  // How many of those requests have actually reached the network.
  sent: () => number;
}

interface Options {
  rows?: Schemas['FleetRow'][];
  status?: number;
  body?: unknown;
  rereadMs?: number;
}

// Mounts the view, waits for its Cells socket and opens it; the seed read has been answered.
export async function openFleet(options: Options = {}): Promise<OpenedFleet> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  fake.respond(
    'GET /v1/fleet',
    options.status ?? 200,
    options.body ?? fleetAnswer(options.rows ?? []),
  );
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const asked = vi.spyOn(client.http, 'call');
  const table: AppSlices = { ...APP_SLICES, fleet: fleetSlice };
  const store = createHiveStore(table);
  render(
    createElement(HiveProvider, {
      hive: { store, client },
      children: createElement(FleetView, { rereadMs: options.rereadMs }),
    }),
  );
  await act(async () => {
    await flush();
  });
  const socket = fake.sockets[0]!;
  await act(async () => {
    socket.open();
    await flush();
  });
  const reads = () => asked.mock.calls.filter((call) => call[1] === '/v1/fleet').length;
  const sent = () => fake.requests.filter((r) => r.target === '/v1/fleet').length;
  return { fake, socket, store, reads, sent };
}

// The Cell ids of the table's body rows, in drawn order (the header row excluded).
export function drawnIds(): string[] {
  const body = screen.getByRole('table').querySelector('tbody')!;
  return [...body.querySelectorAll('tr > th code')].map((code) => code.getAttribute('aria-label')!);
}

// The view's stylesheet as text. Vitest blanks every stylesheet outside `src/styles/` (even with
// `?raw`) and the project has no Node typings, so the file is read through the runtime's own `fs`
// by a name the bundler cannot see; the one cast gives it the single signature used here.
export async function fleetCss(): Promise<string> {
  const module = 'node:fs';
  const fs = (await import(/* @vite-ignore */ module)) as {
    readFileSync(path: string, encoding: 'utf8'): string;
  };
  // The test runner's working directory is the package root.
  return fs.readFileSync('src/views/fleet/fleet.css', 'utf8');
}
