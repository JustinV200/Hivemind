/**
 * Passkeys on Android: the Credential Manager behind `Browser.assertPasskey` and `createPasskey`.
 *
 * The Landing Board hands a phone WebAuthn options as JSON and wants WebAuthn JSON back. In the
 * bundled WebView `navigator.credentials` cannot make a credential bound to the Entrance's DNS name
 * (ADR-0047), so the options go to the native `Passkey` plugin as a string and its answer is parsed
 * back into the object the Entrance expects. The shape is the same as the web adapter's, so nothing
 * above the seam can tell the two apart.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/android/`. Built by `index.ts`
 *   and used as the passkey members of the Android `Browser`.
 *
 * Key invariants:
 *   - Every plugin failure becomes a `PasskeyError` carrying the plugin's code; `session.ts` turns
 *     any rejection into the client's one cancelled-passkey outcome, so a refused prompt is never
 *     shown as a network failure.
 *   - The assertion or registration is returned to the caller only, never stored or logged.
 *   - An answer that is not a JSON object is a failure, not a half-trusted value.
 *
 * See Also:
 *   - src/landing_board/client/platform/passkey.ts for the browser adapter this mirrors.
 *   - android/.../passkey/PasskeyPlugin.kt for the native side.
 */
import type { PasskeyPlugin } from './plugins';

type Json = Record<string, unknown>;

// The codes the Kotlin plugin rejects with, plus the one this adapter adds for a bad answer.
export type PasskeyFailure = 'CANCELLED' | 'NO_CREDENTIAL' | 'UNSUPPORTED' | 'FAILED';

export class PasskeyError extends Error {
  constructor(
    readonly code: PasskeyFailure,
    message: string,
  ) {
    super(message);
    this.name = 'PasskeyError';
  }
}

function codeOf(error: unknown): PasskeyFailure {
  const code = (error as { code?: unknown } | null)?.code;
  return code === 'CANCELLED' || code === 'NO_CREDENTIAL' || code === 'UNSUPPORTED'
    ? code
    : 'FAILED';
}

// Runs one plugin call and turns its string answer into the JSON object the Entrance expects.
async function ceremony(call: () => Promise<{ json: string }>): Promise<Json> {
  let answer: { json: string };
  try {
    answer = await call();
  } catch (error) {
    throw new PasskeyError(codeOf(error), 'The passkey prompt did not finish.');
  }
  try {
    const parsed: unknown = JSON.parse(answer.json);
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Json;
    }
  } catch {
    // Falls through to the single failure below.
  }
  throw new PasskeyError('FAILED', 'The passkey provider returned an unreadable answer.');
}

export interface AndroidPasskeys {
  assertPasskey(options: Json): Promise<Json>;
  createPasskey(options: Json): Promise<Json>;
}

export function androidPasskeys(plugin: PasskeyPlugin): AndroidPasskeys {
  return {
    assertPasskey: (options) =>
      ceremony(() => plugin.get({ optionsJson: JSON.stringify(options) })),
    createPasskey: (options) =>
      ceremony(() => plugin.create({ optionsJson: JSON.stringify(options) })),
  };
}
