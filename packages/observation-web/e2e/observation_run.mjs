/**
 * Drive the Observation Hive in a real Chromium for phase 12's exit run.
 *
 * Reads one JSON object per line on stdin. The first is the spec, `{ baseUrl, inviteUrl, password,
 * outDir, steps? }`: it opens a headless Chromium at 1280x900 with a CDP virtual authenticator (so a
 * passkey is made and asserted without a human), and runs any `steps` it carries. Every later line
 * is one more step, `{ op, ...args }`, so a test can act on the Hive between steps (approve the
 * device, submit a goal) while the one page, session and passkey live on. Each step answers with one
 * JSON line, `{ op, ok, result | error, requests, loads, marker, screenshot }`. The line `{ "op":
 * "end" }` (or end of input) writes `report.json` and a final screenshot to `outDir`, prints the
 * whole report on stdout and closes the browser.
 *
 * Fits into the Hive:
 *   Test tooling for phase 12's exit run, not shipped. Driven by
 *   `packages/hivemind/tests/e2e/observation_browser.py`; steps live in the `steps_*.mjs` beside it.
 *
 * Key invariants:
 *   - The script never reloads the page; `loads` and `marker` in every answer prove it.
 *   - Requests are logged as method and path only, so a report holds no secret.
 *   - Playwright is found beside the app, then at `HIVE_PLAYWRIGHT_MODULE`, then in the global tools.
 */
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import process from 'node:process';
import { drain, marker, record, summarise } from './recorder.mjs';
import { awaitApprovalAndLogin, join } from './steps_session.mjs';
import {
  awaitQuestionAndAnswer,
  awaitReply,
  chatLayout,
  honey,
  requestGoal,
  sendChat,
} from './steps_chat.mjs';
import * as views from './steps_views.mjs';
import { watchAttendant, watchAttendantWait } from './steps_watch.mjs';

const GLOBAL_PLAYWRIGHT = '/opt/node-tools/node_modules/playwright';
const CHROMIUM = process.env.HIVE_CHROMIUM ?? '/opt/pw-browsers/chromium';

function loadPlaywright() {
  const require = createRequire(import.meta.url);
  const wanted = process.env.HIVE_PLAYWRIGHT_MODULE;
  for (const candidate of [wanted, 'playwright', GLOBAL_PLAYWRIGHT].filter(Boolean)) {
    try {
      return require(candidate);
    } catch {
      // Try the next place.
    }
  }
  throw new Error('Playwright for Node was not found; set HIVE_PLAYWRIGHT_MODULE');
}

const STEPS = {
  join,
  await_approval_and_login: awaitApprovalAndLogin,
  read_strip: views.readStrip,
  open: views.open,
  fleet_rows: views.fleetRows,
  cell_page: views.cellPage,
  send_chat: sendChat,
  request_goal: requestGoal,
  await_question_and_answer: awaitQuestionAndAnswer,
  await_reply: awaitReply,
  thoughts: views.thoughts,
  thoughts_wait: views.thoughtsWait,
  attendant: views.attendant,
  attendant_wait: views.attendantWait,
  watch_attendant: watchAttendant,
  watch_attendant_wait: watchAttendantWait,
  trail_scrolls: views.trailScrolls,
  honey,
  chat_layout: chatLayout,
  viewport: async (ctx, step) => {
    await ctx.page.setViewportSize({ width: step.width, height: step.height });
    return { width: step.width, height: step.height };
  },
  console_errors: async (ctx) => ({
    pageErrors: ctx.rec.state.errors,
    consoleErrors: ctx.rec.state.consoleErrors,
  }),
};

async function launch(spec) {
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const rec = record(page, context);
  await rec.planted;
  const cdp = await context.newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const added = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
    },
  });
  mkdirSync(spec.outDir, { recursive: true });
  const statuses = [];
  page.on('response', (response) => {
    if (response.url().includes('/v1/')) {
      statuses.push(
        `${response.request().method()} ${new URL(response.url()).pathname} ${response.status()}`,
      );
    }
  });
  return {
    browser,
    context,
    page,
    cdp,
    rec,
    spec,
    authenticatorId: added.authenticatorId,
    outDir: spec.outDir,
    statuses,
    seq: 0,
  };
}

async function runStep(ctx, step) {
  const started = Date.now();
  const entry = { op: step.op, ok: true };
  try {
    const action = STEPS[step.op];
    if (!action) throw new Error(`unknown step ${step.op}`);
    entry.result = await action(ctx, step);
  } catch (error) {
    entry.ok = false;
    entry.error = String(error.stack ?? error);
  }
  ctx.seq += 1;
  const size = ctx.page.viewportSize();
  const shot = `${String(ctx.seq).padStart(2, '0')}-${step.op}-${size.width}.png`;
  await ctx.page.screenshot({ path: `${ctx.outDir}/${shot}` }).catch(() => undefined);
  const { requests, sockets } = drain(ctx.rec.state);
  Object.assign(entry, {
    requests: requests.filter((r) => r.path.startsWith('/v1/') || r.method !== 'GET'),
    sockets,
    summary: summarise(requests),
    loads: ctx.rec.state.loads,
    marker: await marker(ctx.page).catch(() => null),
    screenshot: shot,
    // The top bar's connection word and the strip's stale note, as a person would read them.
    connection: await ctx.page
      .evaluate(() => ({
        word: document.querySelector('.topbar [aria-live]')?.textContent?.trim() ?? null,
        stale: document.querySelector('.strip-stale')?.textContent?.trim() ?? null,
      }))
      .catch(() => null),
    // True when the page is wider than its viewport, which would scroll sideways on a phone.
    overflowX: await ctx.page
      .evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
      .catch(() => null),
    ms: Date.now() - started,
  });
  return entry;
}

async function main() {
  const lines = createInterface({ input: process.stdin });
  const report = { steps: [] };
  let ctx = null;
  const answer = (entry) => {
    report.steps.push(entry);
    process.stdout.write(`${JSON.stringify(entry)}\n`);
  };
  for await (const line of lines) {
    if (line.trim() === '') continue;
    const message = JSON.parse(line);
    if (ctx === null) {
      ctx = await launch(message);
      for (const step of message.steps ?? []) answer(await runStep(ctx, step));
      process.stdout.write(`${JSON.stringify({ op: 'ready' })}\n`);
      continue;
    }
    if (message.op === 'end') break;
    answer(await runStep(ctx, message));
  }
  if (ctx !== null) {
    writeFileSync(`${ctx.outDir}/report.json`, JSON.stringify(report, null, 2));
    await ctx.browser.close();
  }
  process.stdout.write(`${JSON.stringify({ op: 'report', report })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exit(1);
});
