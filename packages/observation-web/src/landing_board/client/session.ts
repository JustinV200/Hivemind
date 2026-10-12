/**
 * Enrol a new device, log in with a passkey and the operator's password, restore, step up, log out.
 *
 * A new device redeems an invite with a fresh passkey (landing-board.md section 4): ask for the
 * creation options, make the passkey in the browser, send the registration with a self-description.
 * The Entrance answers with the device's id, which is kept here so the login screen can prefill it;
 * the device then waits for the operator, and `approved` is the one question it may ask meanwhile.
 *
 * A browser device logs in in three steps (landing-board.md section 5): ask for a challenge while
 * sending the public half of a fresh P-256 binding key, answer the challenge with a passkey
 * assertion (`navigator.credentials.get` over the challenge's `passkey_options`), and send the
 * assertion with the password and the same binding key. The session then lives as a bearer token
 * plus that key, kept in the store so a reload can restore it. Logout ends the session on the
 * Entrance and deletes the token and the key here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Built by `index.ts`; driven by the
 *   login screen and the shell. Uses `http.ts` for every request and `keys.ts` for storage.
 *
 * Key invariants:
 *   - The password is sent once, in the login body, and never stored or logged.
 *   - A device whose challenge has no `passkey_options` is a program key and cannot log in here;
 *     that is a local refusal, not a retry.
 *   - A passkey prompt the human cancels (or a browser with no passkeys) is one local refusal, so
 *     it is never reported as a network failure.
 *   - `approved` never says why a device is not approved: the Entrance answers a pending, denied,
 *     expired and unknown device alike, so only "approved or not yet" is returned.
 *   - Local state is cleared at logout even if the Entrance cannot be reached, because the key
 *     must not outlive the human's decision to leave.
 *
 * See Also:
 *   - docs/entrance/landing-board.md sections 5 and 7 for the ceremony and step-up.
 *   - src/landing_board/client/keys.ts for the key and its storage.
 */
import type { components } from '../types';
import type { Browser } from './browser';
import { CLIENT_CODES, clientError, LandingBoardError } from './errors';
import type { HttpClient } from './http';
import type { SessionHolder, StoredSession } from './keys';
import {
  clearSession,
  generateBindingKey,
  loadSession,
  recallDevice,
  rememberDevice,
  saveSession,
} from './keys';

// What a new device says about itself at redemption: display text only, shown to the operator.
export type DeviceDescription = components['schemas']['DeviceDescription'];

export interface Enrolled {
  deviceId: string;
  // The key's fingerprint: the operator sees the same one at approval.
  fingerprint: string;
}

export interface OpenedSession {
  deviceId: string;
  listener: string;
  expiresAt: string;
  needsStepUp: boolean;
}

export interface SessionClient {
  login(deviceId: string, password: string): Promise<OpenedSession>;
  restore(): Promise<OpenedSession | null>;
  logout(): Promise<void>;
  stepUp(password: string): Promise<void>;
  enrol(code: string, description: DeviceDescription): Promise<Enrolled>;
  // True once the operator has approved the device; false while it is pending (or was refused).
  approved(deviceId: string): Promise<boolean>;
  rememberedDevice(): Promise<string | null>;
  // Drops local state only; used when the Entrance already ended the session.
  forget(): Promise<void>;
}

export interface SessionDeps {
  browser: Browser;
  http: HttpClient;
  holder: SessionHolder;
}

function viewOf(session: StoredSession): OpenedSession {
  return {
    deviceId: session.deviceId,
    listener: session.listener,
    expiresAt: session.expiresAt,
    needsStepUp: session.needsStepUp,
  };
}

async function forget(deps: SessionDeps): Promise<void> {
  deps.holder.set(null);
  await clearSession(deps.browser.store);
}

async function assertionFor(
  deps: SessionDeps,
  options: Record<string, unknown> | null | undefined,
): Promise<Record<string, unknown>> {
  // No passkey options means an Ed25519 device, which a browser cannot be.
  if (!options) throw clientError(CLIENT_CODES.passkeyRequired, 'No passkey options.');
  return withPasskey(() => deps.browser.assertPasskey(options));
}

// A cancelled prompt, a timeout or a browser without passkeys all reject; say so in one code.
async function withPasskey<T>(ceremony: () => Promise<T>): Promise<T> {
  try {
    return await ceremony();
  } catch {
    throw clientError(CLIENT_CODES.passkeyCancelled, 'The passkey prompt did not finish.');
  }
}

async function login(
  deps: SessionDeps,
  deviceId: string,
  password: string,
): Promise<OpenedSession> {
  const { browser, http, holder } = deps;
  await forget(deps);
  const key = await generateBindingKey(browser.subtle);
  const challenge = await http.call('post', '/v1/auth/challenge', {
    body: { device_id: deviceId, binding_key: key.publicKey },
  });
  const assertion = await assertionFor(deps, challenge.passkey_options);
  const opened = await http.call('post', '/v1/auth/login', {
    body: {
      device_id: deviceId,
      nonce: challenge.nonce,
      password,
      binding_key: key.publicKey,
      assertion,
    },
  });
  const stored: StoredSession = {
    ...key,
    deviceId: opened.device_id,
    token: opened.token,
    listener: opened.listener,
    expiresAt: opened.expires_at,
    needsStepUp: opened.needs_step_up,
  };
  holder.set(stored);
  await saveSession(browser.store, stored);
  return viewOf(stored);
}

async function restore(deps: SessionDeps): Promise<OpenedSession | null> {
  const stored = await loadSession(deps.browser.store);
  if (!stored) return null;
  // A session past its absolute end cannot work; drop it without asking the Entrance.
  if (Date.parse(stored.expiresAt) <= deps.browser.nowMs()) {
    await forget(deps);
    return null;
  }
  deps.holder.set(stored);
  return viewOf(stored);
}

async function logout(deps: SessionDeps): Promise<void> {
  try {
    if (deps.holder.get()) await deps.http.call('post', '/v1/auth/logout');
  } catch {
    // The Entrance may be unreachable or may have ended the session; leave anyway.
  }
  await forget(deps);
}

async function stepUp(deps: SessionDeps, password: string): Promise<void> {
  const { http, holder, browser } = deps;
  const current = holder.get();
  if (!current) throw clientError(CLIENT_CODES.network, 'There is no session to confirm.');
  const challenge = await http.call('post', '/v1/auth/step-up/challenge');
  const assertion = await assertionFor(deps, challenge.passkey_options);
  await http.call('post', '/v1/auth/step-up', {
    body: { nonce: challenge.nonce, password, assertion },
  });
  const stepped: StoredSession = { ...current, needsStepUp: false };
  holder.set(stepped);
  await saveSession(browser.store, stepped);
}

async function enrol(
  deps: SessionDeps,
  code: string,
  description: DeviceDescription,
): Promise<Enrolled> {
  const { http, browser } = deps;
  const created = await http.call('post', '/v1/enrol/passkey-options', { body: { code } });
  const registration = await withPasskey(() => browser.createPasskey(created.options));
  const redeemed = await http.call('post', '/v1/enrol/passkey', {
    body: { code, registration, description },
  });
  // Kept at once, so a reload while waiting for the operator still finds the device id.
  await rememberDevice(browser.store, redeemed.device_id);
  return { deviceId: redeemed.device_id, fingerprint: redeemed.fingerprint };
}

async function approved(deps: SessionDeps, deviceId: string): Promise<boolean> {
  const key = await generateBindingKey(deps.browser.subtle);
  try {
    // A challenge is only issued to an approved device; the unused nonce lapses in a minute.
    await deps.http.call('post', '/v1/auth/challenge', {
      body: { device_id: deviceId, binding_key: key.publicKey },
    });
    return true;
  } catch (error) {
    // The Entrance answers 401 alike for pending, denied, expired and unknown: "not yet".
    if (error instanceof LandingBoardError && error.status === 401) return false;
    throw error;
  }
}

export function createSessionClient(deps: SessionDeps): SessionClient {
  return {
    forget: () => forget(deps),
    rememberedDevice: () => recallDevice(deps.browser.store),
    login: (deviceId, password) => login(deps, deviceId, password),
    restore: () => restore(deps),
    logout: () => logout(deps),
    stepUp: (password) => stepUp(deps, password),
    enrol: (code, description) => enrol(deps, code, description),
    approved: (deviceId) => approved(deps, deviceId),
  };
}
