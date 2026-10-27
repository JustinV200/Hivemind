/**
 * Tests for the Capping view: the strip, the queue, its chips and sort, the links, refusals and live frames.
 *
 * The view is rendered directly over a store that holds the app's slices plus Capping's (the app
 * table does not hold it until the orchestrator registers it), on the real client and the fake
 * browser. A test answers the read, opens the view's one socket by hand and sends trail frames.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/capping/ through its route component.
 *
 * Key invariants:
 *   - The view opens exactly one socket, the trail stream, and makes one read of the queue at
 *     mount; any further read is a reaction to a `capping.*` frame (see capping_reread.test.tsx).
 *   - Every request the view makes is a GET (it is read-only).
 *
 * See Also:
 *   - tests/views/capping/slice.test.ts for the reducer rules.
 */
import { act, fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mount } from './harness';
import { auditRow, busyView, emptyView, hop, openHive } from './support';
import { HiveProvider } from '../../../src/store';
import { CappingScreen } from '../../../src/views/capping';
import { render } from '@testing-library/react';

const region = (name: string) => within(screen.getByRole('region', { name }));
const queue = () => region('Queue');
// The rows of a table, by the full proposal id in each first cell, in the order drawn.
const order = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.querySelector('td code')?.getAttribute('title'));
const rowOf = (id: string) => queue().getByLabelText(id).closest('tr') as HTMLElement;
const chip = (group: string, name: RegExp) =>
  within(screen.getByRole('group', { name: group })).getByRole('button', { name });

describe('the rates strip', () => {
  it('shows one card per tier, least dangerous first, with the figures the schema gives', async () => {
    await mount();
    const strip = region('Rates per tier');
    const tiers = strip.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(tiers).toEqual(['SCRATCH WRITE', 'NETWORK EGRESS', 'IRREVERSIBLE']);
    const figure = (tier: string, label: string) => {
      const card = strip.getByRole('heading', { name: tier }).closest('li') as HTMLElement;
      return within(card).getByText(label).nextElementSibling?.textContent;
    };
    expect(figure('SCRATCH WRITE', 'Waiting')).toBe('1');
    expect(figure('NETWORK EGRESS', 'Refused')).toBe('1');
    expect(figure('NETWORK EGRESS', 'Approved')).toBe('0');
    expect(figure('IRREVERSIBLE', 'Approved')).toBe('1');
    expect(figure('IRREVERSIBLE', 'Rolled back')).toBe('1');
    expect(figure('IRREVERSIBLE', 'Audited')).toBe('1 of 1 (100%)');
    expect(figure('IRREVERSIBLE', 'Findings')).toBe('1 (100% of scored)');
    // A tier nothing was audited for says so with the hyphen, not a made-up zero percent.
    expect(figure('SCRATCH WRITE', 'Audited')).toBe('0 of 0 (-)');
  });
});

describe('the queue', () => {
  it('lists every proposal newest change first, with state, verdict, reason and rollback', async () => {
    await mount();
    expect(order(queue().getByRole('table'))).toEqual(['prop_roll', 'prop_rej', 'prop_wait']);
    const rolled = within(rowOf('prop_roll'));
    expect(rolled.getByText('ROLLED BACK')).toBeTruthy();
    expect(rolled.getByText('CAPPED')).toBeTruthy();
    expect(rolled.getByText('4 checks passed')).toBeTruthy();
    expect(rolled.getByText('rolled back, restored by SNAPSHOT')).toBeTruthy();
    const refused = within(rowOf('prop_rej'));
    // The state and the gate's verdict both read REJECTED.
    expect(refused.getAllByText('REJECTED', { selector: '.tag' })).toHaveLength(2);
    expect(refused.getByText('failed check allowlist')).toBeTruthy();
    expect(refused.getAllByText('-').length).toBeGreaterThan(0);
    // A proposal still waiting has no verdict, reason or rollback to show.
    expect(within(rowOf('prop_wait')).getByText('CHECKING')).toBeTruthy();
    expect(within(rowOf('prop_wait')).queryByText('CAPPED')).toBeNull();
  });

  it('says the judge could not answer when the verdict carries that flag', async () => {
    const view = busyView();
    view.verdicts[1] = { ...view.verdicts[1]!, judge_error: true };
    await mount({ body: view });
    expect(
      within(rowOf('prop_rej')).getByText('failed check allowlist, the judge could not answer'),
    ).toBeTruthy();
  });
});

describe('the filter chips', () => {
  it('keep the rows of the chosen tiers and states, and All clears a group', async () => {
    await mount();
    const table = () => queue().getByRole('table');
    // Each chip counts the rows it would keep.
    expect(chip('Show tier', /^All 3$/).getAttribute('aria-pressed')).toBe('true');
    expect(chip('Show state', /^REJECTED 1$/)).toBeTruthy();
    fireEvent.click(chip('Show tier', /^NETWORK EGRESS/));
    expect(order(table())).toEqual(['prop_rej']);
    expect(chip('Show tier', /^NETWORK EGRESS/).getAttribute('aria-pressed')).toBe('true');
    expect(chip('Show tier', /^All/).getAttribute('aria-pressed')).toBe('false');
    // Two tiers are a union; a state narrows both.
    fireEvent.click(chip('Show tier', /^IRREVERSIBLE/));
    expect(order(table())).toEqual(['prop_roll', 'prop_rej']);
    fireEvent.click(chip('Show state', /^ROLLED BACK/));
    expect(order(table())).toEqual(['prop_roll']);
    fireEvent.click(chip('Show tier', /^All/));
    fireEvent.click(chip('Show state', /^All/));
    expect(order(table())).toEqual(['prop_roll', 'prop_rej', 'prop_wait']);
  });

  it('say so when nothing matches, and the chips stay put', async () => {
    await mount();
    fireEvent.click(chip('Show tier', /^SCRATCH WRITE/));
    fireEvent.click(chip('Show state', /^REJECTED/));
    expect(queue().getByText('No proposals match these filters.')).toBeTruthy();
    expect(queue().queryByRole('table')).toBeNull();
    // The seven states are always listed, in the gate's order.
    const states = within(screen.getByRole('group', { name: 'Show state' }))
      .getAllByRole('button')
      .map((b) => b.textContent?.replace(/ \d+$/, ''));
    expect(states).toEqual([
      'All',
      'PROPOSED',
      'CHECKING',
      'CAPPED',
      'APPLIED',
      'VERIFIED',
      'REJECTED',
      'ROLLED BACK',
    ]);
  });
});

describe('sorting', () => {
  it('sorts by a header, reverses on a second press, and marks the column', async () => {
    await mount();
    const table = () => queue().getByRole('table');
    const header = (name: RegExp) => within(table()).getByRole('columnheader', { name });
    fireEvent.click(within(table()).getByRole('button', { name: /^Tier/ }));
    expect(order(table())).toEqual(['prop_wait', 'prop_rej', 'prop_roll']);
    expect(header(/^Tier/).getAttribute('aria-sort')).toBe('ascending');
    expect(header(/^Bee/).getAttribute('aria-sort')).toBe('none');
    fireEvent.click(within(table()).getByRole('button', { name: /^Tier/ }));
    expect(order(table())).toEqual(['prop_roll', 'prop_rej', 'prop_wait']);
    expect(header(/^Tier/).getAttribute('aria-sort')).toBe('descending');
    // Another column starts ascending: states in the gate's order.
    fireEvent.click(within(table()).getByRole('button', { name: /^State/ }));
    expect(order(table())).toEqual(['prop_wait', 'prop_rej', 'prop_roll']);
    // Time starts newest first, then flips to oldest first.
    fireEvent.click(within(table()).getByRole('button', { name: /^Updated/ }));
    expect(order(table())).toEqual(['prop_roll', 'prop_rej', 'prop_wait']);
    fireEvent.click(within(table()).getByRole('button', { name: /^Updated/ }));
    expect(order(table())).toEqual(['prop_wait', 'prop_rej', 'prop_roll']);
  });
});

describe('the links and the recording note', () => {
  it("link every row to its bee's thoughts and to its Cell page when one is named", async () => {
    await mount();
    const rolled = within(rowOf('prop_roll'));
    expect(rolled.getByRole('link', { name: 'thoughts of worker_3' }).getAttribute('href')).toBe(
      '#/thoughts/worker_3',
    );
    expect(rolled.getByRole('link', { name: 'Cell page of cell_2' }).getAttribute('href')).toBe(
      '#/cells/cell_2',
    );
    // The rejected proposal names no Cell, so it has a thoughts link and no Cell link.
    const refused = within(rowOf('prop_rej'));
    expect(refused.getByRole('link', { name: 'thoughts of worker_2' })).toBeTruthy();
    expect(refused.queryByRole('link', { name: /Cell page/ })).toBeNull();
  });

  it('shows "recording not available" for an Exoskeleton action only, and no control for it', async () => {
    await mount();
    const note = screen.getAllByText('recording not available');
    expect(note).toHaveLength(1);
    const gui = within(rowOf('prop_roll'));
    expect(gui.getByText('recording not available')).toBeTruthy();
    expect(within(rowOf('prop_rej')).queryByText('recording not available')).toBeNull();
    // Never a dead control: the note is plain text with no link or button around it.
    expect(note[0]!.closest('a, button')).toBeNull();
  });
});

describe('the sampled audits', () => {
  it('list each audit with its finding and a link to the bee', async () => {
    await mount({
      body: {
        ...busyView(),
        audits: [
          auditRow({ proposal_id: 'prop_roll', outcome: 'REJECT', bee_id: 'worker_3' }),
          auditRow({ proposal_id: 'prop_a', outcome: null, judge_error: true, bee_id: 'worker_4' }),
        ],
      },
    });
    const audits = region('Sampled audits');
    expect(audits.getByText('REJECT')).toBeTruthy();
    expect(audits.getByText('not scored, the judge could not answer')).toBeTruthy();
    expect(audits.getByRole('link', { name: 'thoughts of worker_4' }).getAttribute('href')).toBe(
      '#/thoughts/worker_4',
    );
  });

  it('say what would fill the table when there are none', async () => {
    await mount({ body: { ...busyView(), audits: [] } });
    expect(region('Sampled audits').getByText(/No audits yet/)).toBeTruthy();
  });
});

describe('empty, waiting and refused', () => {
  it('says what would fill an empty queue', async () => {
    await mount({ body: emptyView() });
    expect(await screen.findByText(/Nothing has been proposed yet/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Capping' })).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('shows the title and a wait at once, before the queue is read', async () => {
    const opened = await openHive();
    render(
      <HiveProvider hive={opened.hive}>
        <CappingScreen />
      </HiveProvider>,
    );
    expect(screen.getByRole('heading', { name: 'Capping' })).toBeTruthy();
    expect(screen.getByText('Reading the Capping queue')).toBeTruthy();
    await screen.findByRole('region', { name: 'Queue' });
  });

  it('shows the capability sentence for a 403 and no table', async () => {
    await mount({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(screen.getByText('hivemind.entrance.capability_denied')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByText('Reading the Capping queue')).toBeNull();
  });
});

describe('live frames', () => {
  it('moves a proposal and adds its verdict at once, in place', async () => {
    const opened = await mount();
    await screen.findByRole('region', { name: 'Queue' });
    act(() => {
      opened.fake.sockets[0]!.receive(
        hop('capping.capped', 'prop_wait', { payload: { checks_passed: 3 } }),
      );
    });
    const row = within(rowOf('prop_wait'));
    // Both the state and the verdict now read CAPPED.
    expect(row.getAllByText('CAPPED', { selector: '.tag' })).toHaveLength(2);
    expect(row.getByText('3 checks passed')).toBeTruthy();
    // The newest change is now first, and the chip counts followed.
    expect(order(queue().getByRole('table'))[0]).toBe('prop_wait');
    expect(chip('Show state', /^CAPPED 1$/)).toBeTruthy();
  });

  it('marks a rollback from its frame and ignores a frame of another family', async () => {
    const opened = await mount();
    await screen.findByRole('region', { name: 'Queue' });
    act(() => {
      const trail = opened.fake.sockets[0]!;
      trail.receive(hop('alarm.raised', 'cell_1', { family: 'alarm' }));
      trail.receive(hop('capping.rolled_back', 'prop_wait', { payload: { method: 'GUI_STATE' } }));
    });
    expect(within(rowOf('prop_wait')).getByText('rolled back, restored by GUI_STATE')).toBeTruthy();
  });
});

describe('the whole page', () => {
  it('only reads: every request is a GET, and the one stream is the trail', async () => {
    const opened = await mount();
    await screen.findByRole('region', { name: 'Queue' });
    expect(opened.fake.requests.every((r) => r.method === 'GET')).toBe(true);
    expect(opened.fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/trail/stream',
    ]);
  });
});
