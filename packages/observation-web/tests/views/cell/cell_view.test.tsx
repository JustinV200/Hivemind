/**
 * Tests for the Cell page: header badges, live folding, the trail re-read, panels and refusals.
 *
 * The view is rendered directly over a store that holds the app's slices plus the Cell's (the app
 * table does not hold it until the orchestrator registers it), on the real client and the fake
 * browser. A test answers the page read, opens the view's three sockets by hand and sends frames.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/cell/ through its route component.
 *
 * Key invariants:
 *   - The view opens exactly three sockets (Cells, telemetry, trail), in that order, and makes one
 *     read of the page at mount; any further read is a reaction to a trail frame.
 *   - Every request the view makes is a GET (it is read-only).
 *   - The debounce tests use real time (the delay is one second), so they are the slow ones.
 *
 * See Also:
 *   - tests/views/cell/slice.test.ts for the reducer rules and diagram.test.tsx for the SVG.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HiveProvider } from '../../../src/store';
import { CellPageView } from '../../../src/views/cell';
import { flush } from '../../support/fake_browser';
import type { OpenedHive } from './support';
import {
  alarmView,
  beeRow,
  cellFrameOf,
  cellRecord,
  forageView,
  grantView,
  openHive,
  pageView,
  proposalRow,
  sampleFrame,
  taskView,
  trailFrame,
  verdictRow,
  waxEntry,
} from './support';

type Answer = Parameters<typeof openHive>[0];

// Mounts the view, waits for its three sockets and opens them, as the shell's other tests do.
async function mount(
  answer?: Answer,
  id = 'cell_1',
): Promise<OpenedHive & { view: ReturnType<typeof render> }> {
  const opened = await openHive(answer);
  const view = render(
    <HiveProvider hive={opened.hive}>
      <CellPageView id={id} />
    </HiveProvider>,
  );
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(3));
  await act(async () => {
    opened.fake.sockets.forEach((socket) => socket.open());
    await flush();
  });
  return { ...opened, view };
}

const [CELLS, TELEMETRY, TRAIL] = [0, 1, 2];
const header = () => document.querySelector('.cell-header') as HTMLElement;

describe('the header', () => {
  it('leads with the name over the id, kind and source, then badges, access and mode', async () => {
    await mount({
      body: pageView({
        cell: cellRecord({
          name: 'laptop',
          comb_shield: 'PROPOLIS',
          mask_state: 'WARDEN',
          access_level: 'READ_ONLY',
        }),
      }),
    });
    await screen.findByText('PROPOLIS');
    const head = within(header());
    // The name leads, as the Fleet row does; the full id stays beside it as the subject.
    expect(head.getByRole('heading', { name: 'laptop' })).toBeTruthy();
    expect(head.queryByRole('heading', { name: 'Cell' })).toBeNull();
    expect(head.getByText('cell_1')).toBeTruthy();
    expect(head.getByText(/Real Cell, source hive_stand, active/)).toBeTruthy();
    expect(head.getByText('PROPOLIS')).toBeTruthy();
    expect(head.getByText('WARDEN')).toBeTruthy();
    expect(head.getByText('Access read only').getAttribute('title')).toBe('READ_ONLY');
    expect(head.getByText('active')).toBeTruthy();
  });

  it('shows the mask as OFF with a note while the schema does not track it yet', async () => {
    await mount();
    const head = within(await screen.findByText('MEADOW').then(header));
    expect(head.getByText('OFF')).toBeTruthy();
    expect(head.getByText('mask state not tracked yet')).toBeTruthy();
  });

  it('shows the subject at once, before the page is read', async () => {
    const opened = await openHive();
    // No answer yet: the read is still in flight when the first paint happens, and the name is
    // not known, so the title is the plain word until the page lands.
    render(
      <HiveProvider hive={opened.hive}>
        <CellPageView id="cell_1" />
      </HiveProvider>,
    );
    expect(screen.getByRole('heading', { name: 'Cell' })).toBeTruthy();
    expect(screen.getByText('cell_1')).toBeTruthy();
    expect(screen.getByText('Reading the Cell')).toBeTruthy();
    await screen.findByText('MEADOW');
  });
});

describe('live frames', () => {
  it("redraws the header from this Cell's frame and ignores another Cell's", async () => {
    const opened = await mount();
    await screen.findByText('MEADOW');
    act(() => {
      opened.fake.sockets[CELLS]!.receive(
        cellFrameOf({ id: 'cell_2', comb_shield: 'PROPOLIS', mask_state: 'QUEEN_FORCED' }),
      );
    });
    expect(screen.queryByText('PROPOLIS')).toBeNull();
    act(() => {
      opened.fake.sockets[CELLS]!.receive(
        cellFrameOf({ comb_shield: 'NIGHT_VEIL', mask_state: 'QUEEN_FORCED', mode: 'WATCH' }),
      );
    });
    const head = within(header());
    expect(head.getByText('NIGHT VEIL')).toBeTruthy();
    expect(head.getByText('QUEEN FORCED')).toBeTruthy();
    expect(head.getByText('watch')).toBeTruthy();
  });

  it("moves a sub-bee's context gauge on a telemetry sample from this Warden only", async () => {
    const opened = await mount();
    const gauge = async () => await screen.findByRole('meter', { name: 'Context of worker_1' });
    expect((await gauge()).getAttribute('aria-valuenow')).toBe('32000');
    act(() => {
      opened.fake.sockets[TELEMETRY]!.receive(
        sampleFrame({ warden_id: 'warden_9', tokens_used: 1 }),
      );
    });
    expect((await gauge()).getAttribute('aria-valuenow')).toBe('32000');
    act(() => {
      opened.fake.sockets[TELEMETRY]!.receive(sampleFrame({ tokens_used: 115200 }));
    });
    expect((await gauge()).getAttribute('aria-valuenow')).toBe('115200');
  });
});

describe('the trail re-read', () => {
  it('makes no read without a frame, and none for a frame about another Cell', async () => {
    const opened = await mount();
    await screen.findByText('MEADOW');
    act(() => {
      opened.fake.sockets[TRAIL]!.receive(
        trailFrame({ subject_id: 'cell_2', payload: { cell_id: 'cell_2' } }),
      );
    });
    // Longer than the one second window, so a read that was going to happen would have.
    await new Promise((resolve) => setTimeout(resolve, 1250));
    expect(opened.pageReads()).toBe(1);
  });

  it('reads once, a second after the first matching frame, however many follow', async () => {
    const opened = await mount();
    await screen.findByText('MEADOW');
    opened.fake.respond(
      'GET /v1/cells/cell_1/page',
      200,
      pageView({ alarms: [alarmView({ kind: 'GRANT_EXCEEDED' })] }),
    );
    act(() => {
      const trail = opened.fake.sockets[TRAIL]!;
      trail.receive(trailFrame({ id: 'e1', subject_id: 'cell_1' }));
      trail.receive(trailFrame({ id: 'e2', actor: 'warden_1', subject_id: 'task_1' }));
      trail.receive(trailFrame({ id: 'e3', payload: { cell_id: 'cell_1' } }));
    });
    // Not before the window closes.
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(opened.pageReads()).toBe(1);
    expect(await screen.findByText('spent past its grant', {}, { timeout: 2500 })).toBeTruthy();
    expect(opened.pageReads()).toBe(2);
    // The three frames cost one read: nothing else follows.
    await new Promise((resolve) => setTimeout(resolve, 1250));
    expect(opened.pageReads()).toBe(2);
  });
});

describe('the panels', () => {
  it('list tasks by goal with links to the bees behind them and the Warden', async () => {
    await mount({
      body: pageView({
        tasks: [
          taskView({
            id: 'task_2',
            goal_id: 'goal_2',
            status: 'BLOCKED',
            blocked_on: 'which file?',
          }),
          taskView({ id: 'task_1', goal_id: 'goal_1', fraction_done: 0.4 }),
        ],
        cell: cellRecord({ current_tasks: ['task_1', 'task_2', 'task_3'] }),
      }),
    });
    const tasks = within(await screen.findByRole('region', { name: 'Tasks and goals' }));
    const goals = tasks.getAllByRole('heading', { level: 3 });
    expect(goals.map((g) => g.textContent)).toEqual(['Goal goal_1', 'Goal goal_2']);
    expect(tasks.getByText('40% done')).toBeTruthy();
    expect(tasks.getByText('waiting on: which file?')).toBeTruthy();
    // task_1 is worker_1's current task; task_3 is on the Cell frame but not on the page yet.
    expect(tasks.getByRole('link', { name: 'thoughts of worker_1' }).getAttribute('href')).toBe(
      '#/thoughts/worker_1',
    );
    expect(
      tasks.getByRole('link', { name: 'Attendant view of warden_1' }).getAttribute('href'),
    ).toBe('#/attendant/warden_1');
    expect(tasks.getByText('placed just now')).toBeTruthy();
  });

  it('show the Forage used against issued, grants and pending requests', async () => {
    await mount({
      body: pageView({
        forage: forageView({
          grants: [grantView({ id: 'grant_7', state: 'EXHAUSTED', seats: 5 })],
          pending_requests: [
            { grant_id: 'grant_7', request_kind: 'SPEND', requested_at: '2026-01-01T00:30:00Z' },
          ],
        }),
      }),
    });
    const forage = within(await screen.findByRole('region', { name: 'Forage' }));
    expect(
      forage.getByRole('meter', { name: 'Tokens, all grants' }).getAttribute('aria-valuemax'),
    ).toBe('100000');
    expect(forage.getAllByText('40,000 / 100,000 (40%)')).toHaveLength(2);
    expect(forage.getByText('exhausted')).toBeTruthy();
    expect(forage.getByText('5 seats')).toBeTruthy();
    expect(forage.getByText('spend')).toBeTruthy();
  });

  it('say so when there is no Warden and so no Forage', async () => {
    await mount({ body: pageView({ forage: null, warden: null }) });
    expect(await screen.findByText(/holds no Forage/)).toBeTruthy();
  });

  it('link into the Honey browser for this Cell', async () => {
    await mount();
    const link = await screen.findByRole('link', { name: /Browse the Honey/ });
    expect(link.getAttribute('href')).toBe('#/honey/cells/cell_1');
  });

  it('show access level, mode and "not tracked yet" for the last Patrol', async () => {
    await mount({ body: pageView({ cell: cellRecord({ mode: null }) }) });
    const mode = within(await screen.findByRole('region', { name: 'Access and mode' }));
    expect(mode.getByText('scratch')).toBeTruthy();
    expect(mode.getByText('not reported yet')).toBeTruthy();
    expect(mode.getByText('not tracked yet')).toBeTruthy();
  });

  it('show the last Patrol once the schema carries one', async () => {
    await mount({ body: pageView({ last_patrol_at: '2026-01-01T00:10:00Z' }) });
    const region = await screen.findByRole('region', { name: 'Access and mode' });
    expect(within(region).queryByText('not tracked yet')).toBeNull();
    // The visible text is relative to the clock, so the ISO time in `<time>` is what is asserted.
    expect(region.querySelector('time')?.getAttribute('datetime')).toBe('2026-01-01T00:10:00Z');
  });

  it('list Cell Wax with severity, proposer and expiry, and standing wax', async () => {
    await mount({
      body: pageView({
        wax: [
          waxEntry({ id: 'wax_a', severity: 'BLOCK', proposer: 'worker_1' }),
          waxEntry({
            id: 'wax_b',
            severity: 'NOTE',
            proposer: null,
            origin: 'HUMAN',
            expires_at: null,
          }),
        ],
      }),
    });
    const wax = within(await screen.findByRole('region', { name: 'Cell Wax' }));
    const rows = wax.getAllByRole('listitem');
    expect(within(rows[0]!).getByText('BLOCK')).toBeTruthy();
    expect(within(rows[0]!).getByText('by worker_1')).toBeTruthy();
    expect(within(rows[0]!).getByText(/lapses/)).toBeTruthy();
    expect(within(rows[1]!).getByText('NOTE')).toBeTruthy();
    expect(within(rows[1]!).getByText('by HUMAN')).toBeTruthy();
    expect(within(rows[1]!).getByText('standing')).toBeTruthy();
    // The row has no title or text; the note itself is read in the Honey browser.
    expect(within(rows[1]!).getByRole('link', { name: 'read the note' }).getAttribute('href')).toBe(
      '#/honey/cells/cell_1/wax?item=wax_b',
    );
  });

  it('list open Alarms, and say when none are open', async () => {
    await mount({
      body: pageView({
        alarms: [alarmView({ severity: 'CRITICAL', escalated: true, task_id: 'task_1' })],
      }),
    });
    const alarms = within(await screen.findByRole('region', { name: 'Alarms' }));
    expect(alarms.getByText('CRITICAL')).toBeTruthy();
    // The kind as a plain phrase, with the schema's code kept on the phrase's title.
    expect(alarms.getByText('a Worker crashed').getAttribute('title')).toBe('WORKER_CRASHED');
    // An escalated Alarm is acknowledged in the chat, so the row leads there.
    expect(
      alarms.getByRole('link', { name: 'reached you: see the chat' }).getAttribute('href'),
    ).toBe('#/chat');
  });

  it('say there are no Alarms, Wax or tasks when the lists are empty', async () => {
    await mount();
    await screen.findByText('MEADOW');
    expect(screen.getByText('No open Alarms on this Cell.')).toBeTruthy();
    expect(screen.getByText(/No Cell Wax/)).toBeTruthy();
    expect(screen.getByText(/holds no Forage/)).toBeTruthy();
  });
});

describe('Capping', () => {
  const capping = {
    audit_rates: [],
    audits: [],
    by_tier_state: [],
    in_flight: [proposalRow({ id: 'prop_flight', state: 'CHECKING' })],
    finished: [proposalRow({ id: 'prop_gui', state: 'VERIFIED', action_kind: 'GUI' })],
    rollbacks: [
      proposalRow({ id: 'prop_back', state: 'ROLLED_BACK', rollback_method: 'SNAPSHOT_RESTORE' }),
    ],
    verdicts: [
      verdictRow({ proposal_id: 'prop_gui', verdict: 'CAPPED' }),
      verdictRow({
        proposal_id: 'prop_no',
        verdict: 'REJECTED',
        failing_check: 'path_allowlist',
        judge_error: true,
      }),
    ],
  };

  it('lists proposals in flight, verdicts with reasons and rollbacks', async () => {
    await mount({ body: pageView({ capping }) });
    const box = within(await screen.findByRole('region', { name: 'Capping' }));
    expect(box.getByLabelText('prop_flight')).toBeTruthy();
    // The gate's states and verdicts are the Capping view's words, the code on the title.
    expect(box.getByText('Approved').getAttribute('title')).toBe('CAPPED');
    expect(box.getByText('Refused')).toBeTruthy();
    expect(box.getByText('Checking')).toBeTruthy();
    expect(box.getAllByText('failed check path_allowlist').length).toBeGreaterThan(0);
    expect(box.getByText('the judge could not answer')).toBeTruthy();
    expect(box.getByText('restored by snapshot restore')).toBeTruthy();
  });

  it('says "recording not available" on Exoskeleton actions only, with no control', async () => {
    await mount({ body: pageView({ capping }) });
    const box = within(await screen.findByRole('region', { name: 'Capping' }));
    // The finished GUI proposal and the CAPPED verdict for it; the rest are not Exoskeleton.
    expect(box.getAllByText('recording not available')).toHaveLength(2);
    expect(box.queryByRole('button')).toBeNull();
  });
});

describe('refusals', () => {
  it('shows the unknown Cell sentence for a 404 and draws no diagram', async () => {
    await mount({
      status: 404,
      body: { error: 'hivemind.entrance.cell_not_found', detail: 'no such Cell' },
    });
    expect(await screen.findByText('That Cell is not known to the Hive.')).toBeTruthy();
    expect(document.querySelector('[data-code="hivemind.entrance.cell_not_found"]')).toBeTruthy();
    expect(document.querySelector('svg.dg')).toBeNull();
    expect(screen.queryByText('Reading the Cell')).toBeNull();
  });

  it('shows the capability sentence for a 403', async () => {
    await mount({
      status: 403,
      body: { error: 'hivemind.entrance.capability_denied', detail: 'x' },
    });
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(document.querySelector('svg.dg')).toBeNull();
  });
});

describe('the whole page', () => {
  it('only reads: every request is a GET, and the three streams are the ones named', async () => {
    const opened = await mount({ body: pageView({ bees: [beeRow()] }) });
    await screen.findByText('MEADOW');
    expect(opened.fake.requests.every((r) => r.method === 'GET')).toBe(true);
    expect(opened.fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/cells/stream',
      '/v1/telemetry/stream',
      '/v1/trail/stream',
    ]);
  });

  it('closes its three streams when the reader leaves', async () => {
    const opened = await mount();
    await screen.findByText('MEADOW');
    expect(opened.fake.sockets.every((s) => s.closedWith === null)).toBe(true);
    opened.view.unmount();
    expect(opened.fake.sockets.map((s) => s.closedWith)).toEqual([1000, 1000, 1000]);
  });
});
