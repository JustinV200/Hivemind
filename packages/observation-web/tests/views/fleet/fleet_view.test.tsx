/**
 * Tests for the Fleet view: rows, badges at every width, filter, sort, links, live updates.
 *
 * The view is mounted on a fake browser with the store built around the Fleet slice, the seed
 * read scripted and the Cells socket driven by hand. Time is faked only for the re-read's
 * debounce, and only after the view is up, so the sign-in and the socket run on the real clock.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/fleet/ end to end below the shell.
 *
 * Key invariants:
 *   - No request is made that the test did not route; an unrouted read answers 404.
 *   - The view reads the aggregate once on mount and again only after a Cell frame; the tests prove
 *     that no frame means no read.
 *
 * See Also:
 *   - tests/views/fleet/slice.test.ts for the reducer rules.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KIND_KEY } from '../../../src/views/fleet/filter';
import { sortRows } from '../../../src/views/fleet/table';
import { flush } from '../../support/fake_browser';
import { cellFrame } from '../../support/frames';
import { drawnIds, fleetAnswer, fleetCss, fleetRow, openFleet } from './support';
import type { OpenedFleet } from './support';

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  // A width set by `setWidth` must not leak into the next test, which expects jsdom's none.
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const rows = () => screen.getAllByRole('row').slice(1);
const real = fleetRow({
  id: 'cell_r',
  name: 'hive-stand',
  kind: 'REAL',
  comb_shield: 'PROPOLIS',
  mask_state: 'QUEEN_FORCED',
  source: 'hive_stand',
  access_level: 'FULL',
  warden_id: 'warden_1',
  warden_runs: 'hive_stand',
  models_from: 'hive_stand',
  mode: 'ACTIVE',
  status: 'ACTIVE',
  current_task_id: 'task_1',
  current_task_role: 'GUARD_BEE',
  open_alarms: 3,
  highest_wax_severity: 'CAUTION',
  lease_age_s: 3700,
  links: {
    attendant_principal: 'warden_1',
    cell_id: 'cell_r',
    thought_principals: ['warden_1', 'worker_7'],
  },
});
const virtual = fleetRow({
  id: 'cell_v',
  name: 'ubuntu',
  kind: 'VIRTUAL',
  comb_shield: 'NIGHT_VEIL',
  mask_state: null,
  warden_runs: 'on_cell',
  models_from: 'local',
  status: 'READY',
});
const meadow = fleetRow({ id: 'cell_m', comb_shield: 'MEADOW', mask_state: 'WARDEN' });

describe('rows', () => {
  it('draws one row per Cell with the tier and the mask in every one', async () => {
    await openFleet({ rows: [real, virtual, meadow] });
    expect(await screen.findAllByRole('row')).toHaveLength(4);
    const [a, b, c] = rows();
    expect(within(a!).getByText('PROPOLIS')).toBeTruthy();
    expect(within(a!).getByText('QUEEN FORCED')).toBeTruthy();
    expect(within(b!).getByText('NIGHT VEIL')).toBeTruthy();
    // No mask is information: OFF is drawn, not left blank.
    expect(within(b!).getByText('OFF')).toBeTruthy();
    expect(within(c!).getByText('MEADOW')).toBeTruthy();
    expect(within(c!).getByText('WARDEN')).toBeTruthy();
  });

  it('shows the kind, source, status and mode, access, task and role, Alarms, Wax and lease', async () => {
    await openFleet({ rows: [real] });
    const row = (await screen.findAllByRole('row'))[1]!;
    // The status and the mode both read `active`, in plain words rather than the schema's code.
    expect(within(row).getAllByText('active')).toHaveLength(2);
    for (const text of ['full', 'guard bee', 'caution']) {
      expect(within(row).getAllByText(text).length).toBeGreaterThan(0);
    }
    // The kind, the source and the lease age share one muted line under the name.
    expect(within(row).getByText('Real · hive_stand · lease 1h 1m')).toBeTruthy();
    expect(within(row).getByText('3').className).toContain('fleet-alarms');
    expect(within(row).getByText('caution').closest('.fleet-wax')).not.toBeNull();
    expect(within(row).getByLabelText('task_1')).toBeTruthy();
  });

  it('shows the two place badges in words', async () => {
    await openFleet({ rows: [real, virtual, fleetRow({ id: 'cell_x' })] });
    await screen.findAllByRole('row');
    const places = rows().map((row) =>
      [...row.querySelectorAll('.badge-place')].map((badge) => badge.textContent),
    );
    expect(places).toEqual([
      ['Hive Stand', 'Hive Stand'],
      ['on the Cell', 'local'],
      ['no Warden', 'unknown'],
    ]);
  });

  it('says what would fill an empty fleet', async () => {
    await openFleet({ rows: [] });
    expect(await screen.findByText(/No Cells yet/)).toBeTruthy();
  });
});

// Sets the viewport the way a browser would report it, then lets the page hear about it.
function setWidth(px: number): void {
  window.innerWidth = px;
  window.matchMedia = ((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    const max = /max-width:\s*(\d+)px/.exec(query);
    const matches = (!min || px >= Number(min[1])) && (!max || px <= Number(max[1]));
    return { matches, media: query, addEventListener: () => {}, removeEventListener: () => {} };
  }) as unknown as typeof window.matchMedia;
  act(() => {
    window.dispatchEvent(new Event('resize'));
  });
}

describe('badges at every width', () => {
  it.each([1280, 900, 360])('keep a tier and a mask badge in every row at %i px', async (width) => {
    await openFleet({ rows: [real, virtual, meadow] });
    await screen.findAllByRole('row');
    setWidth(width);
    for (const row of rows()) {
      expect(row.querySelectorAll('.badge-tier')).toHaveLength(1);
      expect(row.querySelectorAll('.badge-mask')).toHaveLength(1);
      expect(row.querySelectorAll('.badge-place')).toHaveLength(2);
    }
  });

  it('puts the two badges ahead of everything else in the card', async () => {
    const css = await fleetCss();
    expect(css).toMatch(/\.fleet-tier\s*\{\s*order:\s*-3/);
    expect(css).toMatch(/\.fleet-mask\s*\{\s*order:\s*-2/);
    // Nothing in the sheet hides anything, so no width can hide a badge.
    expect(css).not.toMatch(/display:\s*none|visibility:\s*hidden/);
    // Tokens only.
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe('legend', () => {
  it('is fixed above the table with the three levels and the schema meanings', async () => {
    await openFleet({ rows: [virtual] });
    const legend = await screen.findByRole('region', { name: 'Comb Shield levels' });
    for (const word of ['MEADOW', 'PROPOLIS', 'NIGHT VEIL']) {
      expect(within(legend).getByText(word)).toBeTruthy();
    }
    expect(within(legend).getByText(/throwaway Cell/)).toBeTruthy();
    expect(within(legend).getByText(/secrets live here/)).toBeTruthy();
    // It is not a disclosure: nothing to open or close.
    expect(within(legend).queryByRole('button')).toBeNull();
    const table = screen.getByRole('table');
    expect(legend.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('still names the three levels when the read is refused', async () => {
    await openFleet({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    const legend = await screen.findByRole('region', { name: 'Comb Shield levels' });
    expect(within(legend).getAllByText(/^(MEADOW|PROPOLIS|NIGHT VEIL)$/)).toHaveLength(3);
    // The meanings fall back to the Entrance's fixed sentences, so the badges are never bare.
    expect(within(legend).getByText(/Baseline: any machine/)).toBeTruthy();
    expect(within(legend).getByText(/Virtual only/)).toBeTruthy();
  });
});

describe('filter', () => {
  it('defaults to All and narrows to Real or Virtual', async () => {
    await openFleet({ rows: [real, virtual, meadow] });
    await screen.findAllByRole('row');
    expect(screen.getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    expect(drawnIds()).toEqual(['cell_r']);
    fireEvent.click(screen.getByRole('button', { name: 'Virtual' }));
    expect(drawnIds()).toEqual(['cell_v', 'cell_m']);
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(drawnIds()).toHaveLength(3);
  });

  it('is remembered under one key and restored on the next visit', async () => {
    await openFleet({ rows: [real, virtual] });
    await screen.findAllByRole('row');
    fireEvent.click(screen.getByRole('button', { name: 'Virtual' }));
    expect(window.localStorage.getItem(KIND_KEY)).toBe('VIRTUAL');
    document.body.innerHTML = '';
    await openFleet({ rows: [real, virtual] });
    await screen.findAllByRole('row');
    expect(screen.getByRole('button', { name: 'Virtual' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(drawnIds()).toEqual(['cell_v']);
  });

  it('works when storage throws, and starts at All', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    await openFleet({ rows: [real, virtual] });
    await screen.findAllByRole('row');
    expect(drawnIds()).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    expect(drawnIds()).toEqual(['cell_r']);
  });

  it('says so when the filter leaves nothing', async () => {
    await openFleet({ rows: [virtual] });
    await screen.findAllByRole('row');
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    expect(screen.getByText(/No Real Cells/)).toBeTruthy();
  });
});

describe('sort', () => {
  const a = fleetRow({ id: 'cell_a', comb_shield: 'NIGHT_VEIL', lease_age_s: 50, open_alarms: 1 });
  const b = fleetRow({ id: 'cell_b', comb_shield: 'MEADOW', lease_age_s: null, open_alarms: 1 });
  const c = fleetRow({ id: 'cell_c', comb_shield: 'PROPOLIS', lease_age_s: 900, open_alarms: 7 });
  const header = (name: string) => screen.getByRole('columnheader', { name });

  it('starts in the aggregate order with every header unsorted', async () => {
    await openFleet({ rows: [c, a, b] });
    await screen.findAllByRole('row');
    expect(drawnIds()).toEqual(['cell_c', 'cell_a', 'cell_b']);
    expect(header('Shield').getAttribute('aria-sort')).toBe('none');
  });

  it('sorts by a header click, flips on the next, and says so with aria-sort', async () => {
    await openFleet({ rows: [c, a, b] });
    await screen.findAllByRole('row');
    fireEvent.click(within(header('Shield')).getByRole('button'));
    // By meaning, open to closed, not by spelling.
    expect(drawnIds()).toEqual(['cell_b', 'cell_c', 'cell_a']);
    expect(header('Shield').getAttribute('aria-sort')).toBe('ascending');
    fireEvent.click(within(header('Shield')).getByRole('button'));
    expect(drawnIds()).toEqual(['cell_a', 'cell_c', 'cell_b']);
    expect(header('Shield').getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(within(header('Cell')).getByRole('button'));
    expect(header('Shield').getAttribute('aria-sort')).toBe('none');
    expect(drawnIds()).toEqual(['cell_a', 'cell_b', 'cell_c']);
  });

  it('breaks ties on the Cell id in either direction, so the order is stable', async () => {
    await openFleet({ rows: [c, b, a] });
    await screen.findAllByRole('row');
    const button = within(header('Alarms / Wax')).getByRole('button');
    fireEvent.click(button);
    expect(drawnIds()).toEqual(['cell_a', 'cell_b', 'cell_c']);
    fireEvent.click(button);
    expect(drawnIds()).toEqual(['cell_c', 'cell_a', 'cell_b']);
  });

  it('puts a Cell with no lease last whichever way the column runs', () => {
    // Lease age has no header (it sits under the Cell's name); the "Sort by" control drives it.
    const ids = (direction: 'ascending' | 'descending') =>
      sortRows([a, b, c], { key: 'lease', direction }).map((row) => row.id);
    expect(ids('ascending')).toEqual(['cell_a', 'cell_c', 'cell_b']);
    expect(ids('descending')).toEqual(['cell_c', 'cell_a', 'cell_b']);
  });

  it('is reached by the keyboard: the header is a real button', async () => {
    await openFleet({ rows: [a] });
    await screen.findAllByRole('row');
    expect(within(header('Shield')).getByRole('button').tagName).toBe('BUTTON');
  });
});

describe('links', () => {
  it('go to the Cell page, the Warden Attendant view and each bee thoughts by hash route', async () => {
    await openFleet({ rows: [real] });
    const row = (await screen.findAllByRole('row'))[1]!;
    const hrefs = within(row)
      .getAllByRole('link')
      .map((link) => [
        link.getAttribute('aria-label') ?? link.textContent,
        link.getAttribute('href'),
      ]);
    expect(hrefs).toEqual([
      ['hive-stand cell_r', '#/cells/cell_r'],
      ['Attendant', '#/attendant/warden_1'],
      ['Thoughts of warden_1', '#/thoughts/warden_1'],
      ['Thoughts of worker_7', '#/thoughts/worker_7'],
    ]);
    // The sub-bee's link is a numbered comb cell, named in full for assistive technology.
    expect(within(row).getByRole('link', { name: 'Thoughts of worker_7' }).textContent).toBe('1');
  });

  it('offer no Attendant or thoughts link for a Cell without a Warden', async () => {
    await openFleet({ rows: [virtual] });
    const row = (await screen.findAllByRole('row'))[1]!;
    expect(within(row).queryByRole('link', { name: 'Attendant' })).toBeNull();
    expect(within(row).queryByText('Thoughts')).toBeNull();
  });
});

describe('refusal', () => {
  it('shows the error table sentence for a 403 and no table', async () => {
    await openFleet({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(
      document.querySelector('[data-code="hivemind.entrance.capability_denied"]'),
    ).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByText(/No Cells yet/)).toBeNull();
  });
});

// Goes to fake time for the debounce only; the view and its socket are already up.
function withFakeTimers(): void {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
}

// Back to the real clock and lets the read that the timer started finish.
async function settle(): Promise<void> {
  vi.useRealTimers();
  await act(async () => {
    await flush();
  });
}

describe('live', () => {
  let opened: OpenedFleet;

  beforeEach(async () => {
    opened = await openFleet({ rows: [real, virtual] });
    await screen.findAllByRole('row');
  });

  it('opens the Cells stream and reads the aggregate once', () => {
    expect(opened.fake.sockets.map((s) => s.url)).toEqual(['wss://hive.test/v1/cells/stream']);
    expect(opened.reads()).toBe(1);
  });

  it('folds a frame into its row at once, with no read yet', () => {
    act(() => {
      opened.socket.receive(
        cellFrame({
          id: 'cell_v',
          kind: 'VIRTUAL',
          comb_shield: 'PROPOLIS',
          mask_state: 'WARDEN',
          access_level: 'FULL',
        }),
      );
    });
    const row = rows()[1]!;
    expect(within(row).getByText('PROPOLIS')).toBeTruthy();
    expect(within(row).getByText('WARDEN')).toBeTruthy();
    expect(within(row).queryByText('NIGHT VEIL')).toBeNull();
    expect(opened.reads()).toBe(1);
  });

  it('adds a row for a Cell it has not seen', () => {
    act(() => {
      opened.socket.receive(cellFrame({ id: 'cell_new', kind: 'VIRTUAL' }));
    });
    expect(drawnIds()).toEqual(['cell_r', 'cell_v', 'cell_new']);
  });

  it('holds new rows behind a button while the pointer is in the table, updating the rest', () => {
    const box = screen.getByRole('table').parentElement!;
    fireEvent.pointerEnter(box);
    act(() => {
      opened.socket.receive(cellFrame({ id: 'cell_new', kind: 'VIRTUAL' }));
      opened.socket.receive(cellFrame({ id: 'cell_v', comb_shield: 'NIGHT_VEIL' }));
    });
    // The new Cell waits; the Cell already shown still changed in place.
    expect(drawnIds()).toEqual(['cell_r', 'cell_v']);
    expect(within(rows()[1]!).getByText('NIGHT VEIL')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '1 new row, re-sort' }));
    expect(drawnIds()).toEqual(['cell_r', 'cell_v', 'cell_new']);
    // Leaving the table lets later rows through at once.
    fireEvent.pointerLeave(box);
    act(() => {
      opened.socket.receive(cellFrame({ id: 'cell_late', kind: 'VIRTUAL' }));
    });
    expect(drawnIds()).toContain('cell_late');
  });

  it('reads again once, one second after a burst of frames, and takes the aggregate fields', async () => {
    opened.fake.respond('GET /v1/fleet', 200, fleetAnswer([{ ...virtual, open_alarms: 4 }, real]));
    withFakeTimers();
    act(() => {
      opened.socket.receive(cellFrame({ id: 'cell_v', mode: 'WATCH' }));
      opened.socket.receive(cellFrame({ id: 'cell_v', mode: 'ACTIVE' }));
    });
    await vi.advanceTimersByTimeAsync(999);
    expect(opened.reads()).toBe(1);
    // A third frame inside the window rides on the same read.
    act(() => {
      opened.socket.receive(cellFrame({ id: 'cell_v', mode: 'WATCH' }));
    });
    await vi.advanceTimersByTimeAsync(1);
    expect(opened.reads()).toBe(2);
    await settle();
    expect(opened.sent()).toBe(2);
    await waitFor(() => expect(drawnIds()).toEqual(['cell_v', 'cell_r']));
    expect(within(rows()[0]!).getByText('4').className).toContain('fleet-alarms');
    expect(opened.reads()).toBe(2);
  });

  it('never reads without a frame, however long it waits', async () => {
    withFakeTimers();
    await vi.advanceTimersByTimeAsync(60_000);
    await settle();
    expect(opened.reads()).toBe(1);
  });

  it('cancels a pending read when the reader leaves', async () => {
    withFakeTimers();
    act(() => {
      opened.socket.receive(cellFrame({ id: 'cell_v' }));
    });
    document.body.innerHTML = '';
    const { cleanup } = await import('@testing-library/react');
    cleanup();
    await vi.advanceTimersByTimeAsync(5_000);
    await settle();
    expect(opened.reads()).toBe(1);
    expect(opened.socket.closedWith).toBe(1000);
  });
});
