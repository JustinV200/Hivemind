/**
 * The steps that get a new browser from an invite link to a logged-in shell.
 *
 * `join` opens the invite, makes the passkey through the context's virtual authenticator and waits
 * on the "Waiting for the operator" screen; `await_approval_and_login` then taps "Check again"
 * until the operator has approved (the test approves between the two), goes on to the login form,
 * types the operator's password and waits for the shell. The page never polls by itself, so every
 * check here is a tap, exactly as a person would make it.
 *
 * Fits into the Hive:
 *   Test tooling for phase 12's exit run, not shipped. Used by `observation_run.mjs`.
 *
 * Key invariants:
 *   - The password is typed into the form and never written to a report or a screenshot name.
 *   - A step waits on a named screen, never on a fixed sleep, except between two taps.
 */
import { startStripLog } from './steps_views.mjs';

const HEADING = (page, name) => page.getByRole('heading', { level: 1, name });

export async function join(ctx, step) {
  const { page } = ctx;
  await page.goto(step.inviteUrl ?? ctx.spec.inviteUrl, { waitUntil: 'load' });
  await HEADING(page, 'Join the Observation Hive').waitFor({ timeout: 15000 });
  await page.getByLabel('Name this device').fill(step.name ?? 'observer');
  await page.getByRole('button', { name: 'Make passkey and join' }).click();
  await HEADING(page, 'Waiting for the operator').waitFor({ timeout: 20000 });
  const fingerprint = await page.locator('[aria-label="Key fingerprint"]').innerText();
  const credentials = await ctx.cdp.send('WebAuthn.getCredentials', {
    authenticatorId: ctx.authenticatorId,
  });
  return { fingerprint, passkeys: credentials.credentials.length, url: page.url() };
}

async function tapUntilApproved(page, attempts) {
  for (let tried = 1; tried <= attempts; tried += 1) {
    await page.getByRole('button', { name: 'Check again' }).click();
    const approved = HEADING(page, 'Approved');
    const outcome = await Promise.race([
      approved.waitFor({ timeout: 8000 }).then(() => 'approved'),
      page
        .getByText('Not approved yet.')
        .waitFor({ timeout: 8000 })
        .then(() => 'pending'),
    ]).catch(() => 'unknown');
    if (outcome === 'approved') return tried;
    await page.waitForTimeout(400);
  }
  throw new Error(`the device was still not approved after ${attempts} taps of Check again`);
}

export async function awaitApprovalAndLogin(ctx, step) {
  const { page } = ctx;
  const taps = await tapUntilApproved(page, step.attempts ?? 10);
  await page.getByRole('button', { name: 'Go to login' }).click();
  await HEADING(page, 'Log in to the Observation Hive').waitFor({ timeout: 10000 });
  const deviceId = await page.getByLabel('Device').inputValue();
  await page.getByLabel('Password').fill(step.password ?? ctx.spec.password);
  await page.getByRole('button', { name: 'Log in' }).click();
  // The shell is drawn once the session is open: its summary strip carries a "Bees" group.
  await page.getByRole('group', { name: /^Bees:/ }).waitFor({ timeout: 20000 });
  await startStripLog(page);
  return { taps, deviceId };
}
