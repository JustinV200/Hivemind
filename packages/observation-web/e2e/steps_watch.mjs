/**
 * The steps that watch the Queen's Attendant view in a second tab of the same device.
 *
 * A goal asked for in the chatbox is planned, placed and running within a second, so a tab that
 * only navigates to the Attendant after asking arrives when the graph has already moved. The
 * watcher is a second tab of the one device (same origin, so it restores the session the first tab
 * logged in with): it opens the Attendant before the goal is asked for and stays on it, and
 * `watch_attendant_wait` then reports whether the graph changed under it with the document
 * untouched. The tab counts its own document loads; the recorder counts the first tab's.
 *
 * Fits into the Hive:
 *   Test tooling for phase 12's exit run, not shipped. Used by `observation_run.mjs`.
 *
 * Key invariants:
 *   - The watcher never reloads and never navigates after it has opened the Attendant.
 *   - Nothing here clicks a control that writes.
 *   - Waits are on the graph's own table with a deadline; a miss reports what was seen.
 */

const GRAPH = 'table.sr-only';

async function graphNodes(page) {
  return page
    .locator(GRAPH)
    .filter({ has: page.locator('caption', { hasText: /^Task graph of/ }) })
    .locator('tbody tr')
    .evaluateAll((trs) =>
      trs.map((tr) => ({
        id: tr.querySelector('th')?.textContent?.trim(),
        status: tr.children[1]?.textContent?.trim(),
      })),
    );
}

export async function watchAttendant(ctx) {
  const watcher = await ctx.context.newPage();
  ctx.watcher = { page: watcher, loads: 0, seen: '[]' };
  watcher.on('load', () => (ctx.watcher.loads += 1));
  await watcher.goto(`${ctx.spec.baseUrl}/#/attendant/queen`, { waitUntil: 'load' });
  await watcher.getByRole('heading', { level: 2, name: 'Task graph' }).waitFor({ timeout: 20000 });
  const nodes = await graphNodes(watcher);
  ctx.watcher.seen = JSON.stringify(nodes);
  return { nodes, loads: ctx.watcher.loads };
}

export async function watchAttendantWait(ctx, step) {
  const { page, loads: loadsBefore, seen } = ctx.watcher;
  let nodes = [];
  const deadline = Date.now() + (step.timeoutMs ?? 25000);
  while (Date.now() < deadline) {
    nodes = await graphNodes(page);
    if (nodes.length > 0 && JSON.stringify(nodes) !== seen) break;
    await page.waitForTimeout(200);
  }
  ctx.watcher.seen = JSON.stringify(nodes);
  return {
    changed: JSON.stringify(nodes) !== seen,
    nodes,
    loadsDuring: ctx.watcher.loads - loadsBefore,
    shown: await page.evaluate(() => window.location.hash),
  };
}
