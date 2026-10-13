/**
 * The steps that talk to the Queen in the chat, and that read the Honey browser.
 *
 * The chat is the Observation Hive's only write path, so these are the steps whose requests the
 * test expects to see as POSTs: a typed message, a typed goal (the composer's "Request as a goal"
 * switch), and the answer to a waiting question. The Honey
 * steps only navigate and read, and report what the folder tree and the 403 look like, because
 * what a device may see there is decided by the capabilities the operator gave it.
 *
 * Fits into the Hive:
 *   Test tooling for phase 12's exit run, not shipped. Used by `observation_run.mjs`.
 *
 * Key invariants:
 *   - The message and the answer are typed into the real controls and sent with the real buttons.
 *   - A wait names the line it waits for (an article's accessible name), never a fixed delay.
 */
import { open } from './steps_views.mjs';

const LINE = (page, label) => page.locator(`article[aria-label="${label}"]`);

export async function sendChat(ctx, step) {
  const { page } = ctx;
  await open(ctx, { route: '#/chat', heading: 'Chat' });
  const box = page.getByRole('textbox', { name: 'Message to the Queen' });
  await box.fill(step.text);
  await page.getByRole('button', { name: 'Send' }).click();
  // The words show at once as a pending line, then become the Queen's log line.
  const pending = await page
    .locator('li.chat-line--pending .chat-text')
    .first()
    .innerText({ timeout: 3000 })
    .catch(() => null);
  await LINE(page, 'You, Message')
    .filter({ hasText: step.text })
    .first()
    .waitFor({ timeout: 20000 });
  return { pending, text: step.text, boxAfter: await box.inputValue() };
}

// Asks for a task the way a person does: switch "Request as a goal" on, type it, press the button,
// then follow the new line's link to the Queen's Attendant view.
export async function requestGoal(ctx, step) {
  const { page } = ctx;
  await open(ctx, { route: '#/chat', heading: 'Chat' });
  const toggle = page.getByRole('switch', { name: 'Request as a goal' });
  const offByDefault = !(await toggle.isChecked());
  await toggle.check();
  const box = page.getByRole('textbox', { name: 'Message to the Queen' });
  await box.fill(step.text);
  await page.getByRole('button', { name: 'Request goal' }).click();
  const line = LINE(page, 'You, requested a goal').filter({ hasText: step.text }).first();
  await line.waitFor({ timeout: 20000 });
  const requestId = await line.locator('.id-token').first().getAttribute('title');
  const href = await line.getByRole('link').first().getAttribute('href');
  const boxAfter = await box.inputValue();
  await line.getByRole('link').first().click();
  await page.getByRole('heading', { level: 1, name: 'Attendant' }).waitFor({ timeout: 15000 });
  return { requestId, href, offByDefault, boxAfter, text: step.text };
}

export async function awaitQuestionAndAnswer(ctx, step) {
  const { page } = ctx;
  await open(ctx, { route: '#/chat', heading: 'Chat' });
  const question = LINE(page, 'Queen, Question').filter({
    has: page.getByRole('textbox', { name: 'Your answer' }),
  });
  await question.first().waitFor({ timeout: step.timeoutMs ?? 30000 });
  const asked = await question.first().locator('.chat-text').innerText();
  await page.screenshot({ path: `${ctx.outDir}/question-waiting.png` });
  await question.first().getByRole('textbox', { name: 'Your answer' }).fill(step.answer);
  await question.first().getByRole('button', { name: 'Answer' }).click();
  // Answered, the line stays in the log and its form is gone.
  const asker = LINE(page, 'Queen, Question').filter({ hasText: asked });
  await asker.first().locator('text=No longer waiting.').waitFor({ timeout: 20000 });
  const humanLines = await page.locator('article[aria-label^="You,"] .chat-text').allInnerTexts();
  return { asked, answer: step.answer, resolved: true, youSaid: humanLines };
}

export async function awaitReply(ctx, step) {
  const { page } = ctx;
  await open(ctx, { route: '#/chat', heading: 'Chat' });
  const replies = LINE(page, 'Queen, Reply');
  await page.waitForFunction(
    ([label, text, count]) =>
      [...document.querySelectorAll(`article[aria-label="${label}"] .chat-text`)].filter((el) =>
        el.textContent.includes(text),
      ).length >= count,
    ['Queen, Reply', step.text ?? '', step.minCount ?? 1],
    { timeout: step.timeoutMs ?? 30000 },
  );
  const texts = await replies.locator('.chat-text').allInnerTexts();
  const lines = await page
    .locator('li.chat-line')
    .evaluateAll((items) =>
      items.map((li) => li.querySelector('article')?.getAttribute('aria-label')),
    );
  return { replies: texts, order: lines };
}

async function folderView(ctx, route) {
  const { page } = ctx;
  await open(ctx, { route, heading: 'Honey' });
  await page.locator('.honey-ceiling, [role="alert"]').first().waitFor({ timeout: 15000 });
  const alert = await page.locator('[role="alert"]').allInnerTexts();
  return {
    route,
    subject: await page.locator('.view-subject').first().innerText({ timeout: 2000 }),
    folders: await page.locator('.honey-folders li').evaluateAll((items) =>
      items.map((li) => ({
        title: li.querySelector('a')?.textContent ?? '',
        name: li.querySelector('code')?.textContent ?? '',
        href: li.querySelector('a')?.getAttribute('href') ?? '',
      })),
    ),
    items: await page.locator('.honey-items li').count(),
    ceiling: await page
      .locator('.honey-ceiling')
      .first()
      .innerText({ timeout: 500 })
      .catch(() => null),
    alert: alert.length > 0 ? alert.join(' | ') : null,
  };
}

export async function honey(ctx, step) {
  const top = await folderView(ctx, '#/honey');
  const shown = {};
  for (const route of step.folders ?? []) shown[route] = await folderView(ctx, route);
  const forbidden = {};
  for (const route of step.forbidden ?? []) {
    const before = ctx.rec.state.requests.length;
    forbidden[route] = await folderView(ctx, route);
    // The 403 as the browser saw it: the status of the folder read behind the alert.
    forbidden[route].statuses = ctx.statuses.slice(-3);
    forbidden[route].requestsMade = ctx.rec.state.requests.length - before;
  }
  return { top, shown, forbidden };
}

// Whether the composer and its Send button are drawn inside the viewport (the phone-width check).
export async function chatLayout(ctx) {
  const { page } = ctx;
  await open(ctx, { route: '#/chat', heading: 'Chat' });
  const width = page.viewportSize().width;
  const inside = async (locator) => {
    const box = await locator.boundingBox();
    return box !== null && box.x >= 0 && box.x + box.width <= width + 1;
  };
  return {
    composer: await inside(page.getByRole('textbox', { name: 'Message to the Queen' })),
    send: await inside(page.getByRole('button', { name: 'Send' })),
    width,
  };
}
