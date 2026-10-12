/**
 * `LandingBoardError` and the fixed table that turns an error code into words for the human.
 *
 * The Landing Board refuses with an `ErrorBody` (`error` code, `detail` sentence). The UI shows
 * the code in monospace, then one human sentence, then what to do (design.md section 8). The
 * sentence comes from the table here, keyed by code, so wording is one reviewed place and never
 * improvised per view; a code not in the table falls back to the Entrance's own `detail`, which
 * it writes as one sentence and never fills with a credential.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Thrown by `http.ts`, and read by
 *   `describeError` in the login screen and any view that shows a refusal.
 *
 * Key invariants:
 *   - The table never says why a login failed: the Entrance does not say, so neither does the UI.
 *   - Words follow the README vocabulary (Queen, Cell, Alarm) and use no em-dashes.
 *   - `status` 0 marks a refusal made on this device before any request was sent.
 *
 * See Also:
 *   - docs/observation/design.md section 8 for the wording rules.
 *   - docs/entrance/openapi.json, schema `ErrorBody`.
 */
import type { components } from '../types';

export type ErrorBody = components['schemas']['ErrorBody'];

export interface ErrorWords {
  sentence: string;
  action: string;
}

// Local refusals use their own namespace so they cannot be mistaken for the Entrance's codes.
export const CLIENT_CODES = {
  passkeyRequired: 'hivemind.client.passkey_required',
  writeRefused: 'hivemind.client.write_refused',
  network: 'hivemind.client.network',
} as const;

const ENTRANCE = 'hivemind.entrance.';

// The fixed code to words table. Keys are the stable codes of the Landing Board.
export const ERROR_TABLE: Readonly<Record<string, ErrorWords>> = {
  [`${ENTRANCE}authentication_failed`]: {
    sentence: 'The Hive did not accept this login.',
    action: 'Check the device and the password, then log in again.',
  },
  [`${ENTRANCE}challenge_rejected`]: {
    sentence: 'The login challenge expired or was already used.',
    action: 'Log in again.',
  },
  [`${ENTRANCE}passkey_rejected`]: {
    sentence: 'The passkey was not accepted.',
    action: 'Try again with the passkey this device was enrolled with.',
  },
  [`${ENTRANCE}step_up_required`]: {
    sentence: 'This needs you to confirm again.',
    action: 'Enter your password and use your device key.',
  },
  [`${ENTRANCE}capability_denied`]: {
    sentence: 'This device is not allowed to see or do that.',
    action: 'Ask the operator to widen what this device may do.',
  },
  [`${ENTRANCE}rate_limited`]: {
    sentence: 'Too many requests came from this device.',
    action: 'Wait a minute, then try again.',
  },
  [`${ENTRANCE}enrolment_refused`]: {
    sentence: 'The invite was not accepted.',
    action: 'Ask the operator for a new invite.',
  },
  [`${ENTRANCE}audio_over_budget`]: {
    sentence: 'This device has sent as much audio as it may for now.',
    action: 'Wait a minute, or type instead.',
  },
  [`${ENTRANCE}voice_not_served`]: {
    sentence: 'Voice is switched off on this Hive.',
    action: 'Type instead.',
  },
  [`${ENTRANCE}push_registration_refused`]: {
    sentence: 'The Hive refused this notification address.',
    action: 'Try again from a browser that supports Web Push.',
  },
  [`${ENTRANCE}cell_not_found`]: {
    sentence: 'That Cell is not known to the Hive.',
    action: 'Go back to the fleet and pick another.',
  },
  [`${ENTRANCE}not_found`]: {
    sentence: 'That does not exist, or this listener does not serve it.',
    action: 'Go back and pick another.',
  },
  [`${ENTRANCE}invalid_request`]: {
    sentence: 'The Hive could not read that request.',
    action: 'Check what you entered and try again.',
  },
  [`${ENTRANCE}http_refused`]: {
    sentence: 'The Hive refused the request before reading it.',
    action: 'Check the address of the Hive and try again.',
  },
  'hivemind.conflict': {
    sentence: 'That already changed.',
    action: 'Look at the current state and try again.',
  },
  [CLIENT_CODES.passkeyRequired]: {
    sentence: 'This device is enrolled with a program key, not a passkey.',
    action: 'Log in from a browser with the device passkey.',
  },
  [CLIENT_CODES.writeRefused]: {
    sentence: 'The Observation Hive does not write there.',
    action: 'Use the chat to ask the Queen.',
  },
  [CLIENT_CODES.network]: {
    sentence: 'The Hive could not be reached.',
    action: 'Check the connection and try again.',
  },
};

export class LandingBoardError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly reason: string | null;
  readonly pendingId: string | null;
  readonly capability: string | null;

  constructor(status: number, body: ErrorBody) {
    super(`${body.error}: ${body.detail}`);
    this.name = 'LandingBoardError';
    this.status = status;
    this.code = body.error;
    this.detail = body.detail;
    this.reason = body.reason ?? null;
    this.pendingId = body.pending_id ?? null;
    this.capability = body.capability ?? null;
  }
}

// An error made on this device, before or instead of an answer from the Entrance.
export function clientError(code: string, detail: string): LandingBoardError {
  return new LandingBoardError(0, { error: code, detail });
}

export interface DescribedError {
  code: string;
  sentence: string;
  action: string;
}

export function describeError(error: unknown): DescribedError {
  if (!(error instanceof LandingBoardError)) {
    return { code: CLIENT_CODES.network, ...ERROR_TABLE[CLIENT_CODES.network]! };
  }
  const words = ERROR_TABLE[error.code];
  // An unknown code shows the Entrance's own one-sentence detail and no invented advice.
  return {
    code: error.code,
    sentence: words?.sentence ?? error.detail,
    action: words?.action ?? '',
  };
}
