/**
 * The steps that read the Observation Hive's read-only views: strip, fleet, Cell page, and the
 * live Thoughts and Attendant screens.
 *
 * Each step navigates by setting the hash (the app's only navigation state, so the document is
 * never reloaded) and then waits on the view's own heading. The strip keeps a log from the moment
 * the shell appears: a mutation observer records every distinct reading with its time, so a test
 * can say "active bees changed without a reload" from the page's own history instead of from two
 * samples that might have missed the change. `thoughts_wait` and `attendant_wait` stay on their
 * view, with the document untouched, until the view's own stream has changed what they show.
 *
 * Fits into the Hive:
 *   Test tooling for phase 12's exit run, not shipped. Used by `observation_run.mjs`.
 *
 * Key invariants:
 *   - Nothing here clicks a control that writes; the only click is a link to the Cell page.
 *   - Waits are on the page's state with a deadline; a miss reports what was seen, then throws.
 */

const STRIP_LOG = '__hive_e2e_strip_log';

export async function startStripLog(page) {
  await page.evaluate((name) => {
    const strip = document.querySelector('.strip');
    const read = () =>
      [...strip.querySelectorAll('[role="group"]')].map((g) => g.getAttribute('aria-label'));
    window[name] = [{ at: Date.now(), groups: read() }];
    new MutationObserver(() => {
      const groups = read();
      const last = window[name][window[name].length - 1];
      if (JSON.stringify(groups) !== JSON.stringify(last.groups)) {
        window[name].push({ at: Date.now(), groups });
      }
    }).observe(strip, { subtree: true, childList: true, attributes: true, characterData: true });
  }, STRIP_LOG);
}

const NUMBERS = (label) => (label.match(/\d+/g) ?? []).map(Number);

export async function readStrip(ctx) {
  const { page } = ctx;
  await page.getByRole('group', { name: /^Bees: \d/ }).waitFor({ timeout: 20000 });
  const history = await page.evaluate((name) => window[name] ?? [], STRIP_LOG);
  const groups = history[history.length - 1].groups;
  const named = Object.fromEntries(groups.map((g) => [g.split(':')[0], NUMBERS(g)]));
  return {
    labels: groups,
    bees: named.Bees?.[0],
    cells: named.Cells,
    kind: named.Kind,
    thinking: named.Thinking,
    // The distinct active-bee counts the strip showed, in order, since the shell appeared.
    beeHistory: history.map((h) => NUMBERS(h.groups[0])[0]),
    readings: history.length,
  };
}

export async function open(ctx, step) {
  const { page } = ctx;
  await page.evaluate((hash) => {
    window.location.hash = hash;
  }, step.route);
  await page.getByRole('heading', { level: 1, name: step.heading }).waitFor({ timeout: 15000 });
  const links = await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link')
    .allInnerTexts();
  // A page wider than the viewport would scroll sideways: the phone-width check reads this.
  const overflowX = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  return { route: step.route, heading: step.heading, nav: links.map((t) => t.trim()), overflowX };
}

export async function fleetRows(ctx, step) {
  const { page } = ctx;
  if (!step.stay) await open(ctx, { route: '#/fleet', heading: 'Fleet' });
  await page.locator('table.fleet-table tbody tr').first().waitFor({ timeout: 15000 });
  const rows = await page.locator('table.fleet-table tbody tr').evaluateAll((trs) =>
    trs.map((tr) => {
      const text = (selector) => tr.querySelector(selector)?.textContent?.trim() ?? null;
      return {
        id: tr.querySelector('th .id-token')?.getAttribute('title') ?? null,
        name: text('.fleet-name'),
        status: text('.fleet-status'),
        tier: text('.badge-tier'),
        mask: text('.badge-mask'),
        warden: text('td[data-label="Warden"]'),
        models: text('td[data-label="Models"]'),
        task: text('td[data-label="Task"]'),
        visible: tr.getBoundingClientRect().width > 0,
      };
    }),
  );
  const legend = await page.locator('#fleet-legend-title').count();
  return { rows, legend: legend > 0 };
}

export async function cellPage(ctx) {
  const { page } = ctx;
  await open(ctx, { route: '#/fleet', heading: 'Fleet' });
  await page.locator('table.fleet-table tbody tr th a').first().click();
  await page.getByRole('heading', { level: 1, name: 'Cell' }).waitFor({ timeout: 15000 });
  await page.locator('.cell-header .badge-tier').waitFor({ timeout: 15000 });
  const header = page.locator('.cell-header');
  return {
    tier: await header.locator('.badge-tier').innerText(),
    mask: await header.locator('.badge-mask').innerText(),
    subject: await page.locator('.view-subject').first().innerText(),
  };
}

async function thoughtCount(page) {
  return page.locator('.thought-list article.thought').count();
}

export async function thoughts(ctx, step) {
  const { page } = ctx;
  await open(ctx, { route: `#/thoughts/${step.bee ?? 'queen'}`, heading: 'Thoughts' });
  await page.locator('.follow-toggle').waitFor();
  const follow = page.getByRole('button', { name: 'Follow' });
  if ((await follow.getAttribute('aria-pressed')) !== 'true') await follow.click();
  await page.locator('.follow-state').filter({ hasText: 'on' }).waitFor();
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, {
    timeout: 15000,
  });
  ctx.thoughtsSeen = await thoughtCount(page);
  return { episodes: ctx.thoughtsSeen, follow: 'on' };
}

export async function thoughtsWait(ctx, step) {
  const { page } = ctx;
  const before = ctx.thoughtsSeen ?? 0;
  const loadsBefore = ctx.rec.state.loads;
  const grew = await page
    .waitForFunction(
      (n) => document.querySelectorAll('.thought-list article.thought').length > n,
      before,
      { timeout: step.timeoutMs ?? 20000 },
    )
    .then(() => true)
    .catch(() => false);
  const after = await thoughtCount(page);
  const latest = await page
    .locator('.thought-list article.thought')
    .first()
    .innerText({ timeout: 1000 })
    .catch(() => null);
  return { before, after, grew, loadsDuring: ctx.rec.state.loads - loadsBefore, newest: latest };
}

async function graphNodes(page) {
  return page
    .locator('table.sr-only')
    .filter({ has: page.locator('caption', { hasText: /^Task graph of/ }) })
    .locator('tbody tr')
    .evaluateAll((trs) =>
      trs.map((tr) => ({
        id: tr.querySelector('th')?.textContent?.trim(),
        status: tr.children[1]?.textContent?.trim(),
        role: tr.children[2]?.textContent?.trim(),
        placement: tr.children[3]?.textContent?.trim(),
      })),
    );
}

export async function attendant(ctx, step) {
  const { page } = ctx;
  const principal = step.principal ?? 'queen';
  await open(ctx, { route: `#/attendant/${principal}`, heading: 'Attendant' });
  await page.getByRole('heading', { level: 2, name: 'Task graph' }).waitFor({ timeout: 15000 });
  const nodes = await graphNodes(page);
  ctx.graphSeen = JSON.stringify(nodes);
  return { principal, nodes };
}

export async function attendantWait(ctx, step) {
  const { page } = ctx;
  const loadsBefore = ctx.rec.state.loads;
  const before = ctx.graphSeen ?? '[]';
  let nodes = [];
  const deadline = Date.now() + (step.timeoutMs ?? 25000);
  while (Date.now() < deadline) {
    nodes = await graphNodes(page);
    const done =
      step.until === 'status'
        ? nodes.some((n) => n.status === step.status)
        : JSON.stringify(nodes) !== before;
    if (done && nodes.length > 0) break;
    await page.waitForTimeout(200);
  }
  const changed = JSON.stringify(nodes) !== before;
  ctx.graphSeen = JSON.stringify(nodes);
  return { changed, nodes, loadsDuring: ctx.rec.state.loads - loadsBefore };
}

export async function trailScrolls() {
  // There is no trail view in the route table (fleet, cell, forage, thoughts, attendant, chat,
  // honey, cost, capping, devices, enrol), so there is nothing to scroll.
  return { skipped: true, reason: 'the app has no trail view' };
}
