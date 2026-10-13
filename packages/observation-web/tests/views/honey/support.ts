/**
 * Test builders and a harness for the Honey browser: folders, items, hits, frames, a mounted view.
 *
 * The generated types have many required members that a test about one badge does not care about.
 * These builders fill them with plain defaults and let a test override only what it is about, so
 * each test reads as its point and still type-checks against the generated schema. `openHive`
 * makes the store (the app's table plus the Honey slice, which `src/app.tsx` does not hold until
 * the orchestrator registers it) and a client on the fake browser; `scriptTree` answers the folder
 * route from a table keyed by path (and cursor), as the Entrance would for one viewer.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by every test under tests/views/honey/.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *   - Defaults describe one healthy folder: `/hive`, readable up to C1, holding one Honey row.
 *   - An unscripted folder answers 404, like a path the Entrance does not serve.
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
import { honeySlice } from '../../../src/views/honey';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeAnswer, FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];
export type Folder = Schemas['HoneyFolderView'];
export type Item = Schemas['HoneyItemSummary'];
export type ItemFull = Schemas['HoneyItemView'];
export type Hit = Schemas['HoneySearchHit'];
export type Provenance = Schemas['HoneyProvenanceView'];

export function provenance(over: Partial<Provenance> = {}): Provenance {
  return {
    bee: 'worker_1',
    cell_id: 'cell_1',
    observed_at: '2026-01-01T00:00:00Z',
    source: 'BEE',
    task_id: 'task_1',
    ...over,
  };
}

export function itemSummary(over: Partial<Item> = {}): Item {
  return {
    clearance: 'C1',
    detail: 'part 1',
    expires_at: null,
    id: 'honey_1',
    kind: 'honey',
    path: '/hive/honey_1',
    proposer: null,
    provenance: provenance(),
    scope: 'hive',
    severity: null,
    size_chars: 120,
    title: 'How the site logs in',
    ...over,
  };
}

export function waxSummary(over: Partial<Item> = {}): Item {
  return itemSummary({
    id: 'wax_1',
    kind: 'wax',
    path: '/cells/cell_1/wax/wax_1',
    title: 'Disk fills at night',
    detail: 'CAUTION',
    severity: 'CAUTION',
    proposer: 'warden_1',
    expires_at: '2026-02-01T00:00:00Z',
    scope: 'cell:cell_1',
    provenance: provenance({ source: 'PATROL' }),
    ...over,
  });
}

export function itemView(over: Partial<ItemFull> = {}): ItemFull {
  return {
    ...itemSummary(),
    content: 'Log in with the saved passkey.\n  Then open the board.',
    reason: null,
    sources: [],
    summary: null,
    ...over,
  };
}

export function folderView(over: Partial<Folder> = {}): Folder {
  return {
    can_search: true,
    ceiling: 'C1',
    folders: [],
    items: [itemSummary()],
    next_cursor: null,
    note: '',
    parent: '/',
    path: '/hive',
    scope: 'hive',
    ...over,
  };
}

export function hitView(over: Partial<Hit> = {}): Hit {
  return {
    clearance: 'C1',
    id: 'honey_2',
    path: '/hive/honey_2',
    provenance: provenance({ source: null }),
    scope: 'hive',
    score: 0.826,
    snippet: 'the passkey is kept in the vault',
    title: 'Where the passkey lives',
    ...over,
  };
}

export function searchPage(over: Partial<Schemas['HoneySearchPage']> = {}) {
  return { hits: [hitView()], next_cursor: null, note: '', path: '/hive', ...over };
}

export function honeyTrail(over: Partial<Schemas['TrailEventView']> = {}): Schemas['TrailFrame'] {
  return {
    type: 'trail',
    event: {
      actor: 'system',
      at: '2026-01-01T00:00:06Z',
      family: 'honey',
      id: 'evt_1',
      kind: 'honey.ripened',
      node_id: 'queen',
      payload: {},
      subject_id: 'nectar_1',
      ...over,
    },
  };
}

export const FORBIDDEN: FakeAnswer = {
  status: 403,
  body: { error: 'hivemind.entrance.capability_denied', detail: 'Needs observe:honey.' },
};

export const GONE: FakeAnswer = {
  status: 404,
  body: { error: 'hivemind.entrance.honey_item_not_found', detail: 'No Honey item x exists.' },
};

export interface OpenedHive {
  fake: FakeBrowser;
  hive: Hive;
}

// A signed-in client on the fake browser and a store holding the app's slices plus Honey's.
export async function openHive(): Promise<OpenedHive> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const store = createHiveStore({ ...APP_SLICES, honey: honeySlice });
  return { fake, hive: { store, client } };
}

// Answers the folder route from a table keyed `path` or `path|cursor`; the rest is a 404.
export function scriptTree(fake: FakeBrowser, table: Record<string, Folder | FakeAnswer>): void {
  fake.routes.set('GET /v1/honey/tree', (request) => {
    const query = new URL(request.target, 'https://hive.test').searchParams;
    const cursor = query.get('cursor');
    const key = `${query.get('path') ?? '/'}${cursor ? `|${cursor}` : ''}`;
    const answer = table[key];
    if (answer === undefined) return { status: 404, body: GONE.body };
    return 'status' in answer ? answer : { status: 200, body: answer };
  });
}

// Requests so far whose target starts with `prefix`, with their parsed query.
export function requestsTo(fake: FakeBrowser, prefix: string) {
  return fake.requests
    .filter((request) => request.target.startsWith(prefix))
    .map((request) => ({
      method: request.method,
      query: new URL(request.target, 'https://hive.test').searchParams,
    }));
}

// Answers the search route from a table keyed `q` or `q|cursor`; the rest is a 404.
export function scriptSearch(
  fake: FakeBrowser,
  table: Record<string, Schemas['HoneySearchPage'] | FakeAnswer>,
): void {
  fake.routes.set('GET /v1/honey/search', (request) => {
    const query = new URL(request.target, 'https://hive.test').searchParams;
    const cursor = query.get('cursor');
    const answer = table[`${query.get('q') ?? ''}${cursor ? `|${cursor}` : ''}`];
    if (answer === undefined) return { status: 404, body: GONE.body };
    return 'status' in answer ? answer : { status: 200, body: answer };
  });
}
