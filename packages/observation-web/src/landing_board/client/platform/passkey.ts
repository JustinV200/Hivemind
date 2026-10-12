/**
 * Passkey assertion for login and step-up, through `navigator.credentials.get`.
 *
 * The challenge's `passkey_options` is `PublicKeyCredentialRequestOptionsJSON`, with binary fields
 * as base64url strings. Current browsers parse it themselves (`parseRequestOptionsFromJSON`) and
 * serialise the answer with `toJSON()`; this file does the same by hand for the browsers that
 * lack those two methods. The result is the assertion JSON `LoginRequest.assertion` expects.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Used by `real_browser.ts` as
 *   `Browser.assertPasskey`. This is the "passkey plumbing" half of ADR-0046's platform seam.
 *
 * Key invariants:
 *   - User verification is whatever the options require; nothing here weakens or skips it.
 *   - The assertion is only ever returned to the caller, never stored or logged.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 5 for the login ceremony.
 */
import { base64urlDecode, base64urlEncode } from '../signing';

type Json = Record<string, unknown>;

function asBuffer(text: unknown): ArrayBuffer {
  return base64urlDecode(String(text)).buffer as ArrayBuffer;
}

// The hand-made parse for a browser without PublicKeyCredential.parseRequestOptionsFromJSON.
function parseOptions(json: Json): PublicKeyCredentialRequestOptions {
  const allow = Array.isArray(json.allowCredentials) ? (json.allowCredentials as Json[]) : [];
  return {
    ...(json as object),
    challenge: asBuffer(json.challenge),
    allowCredentials: allow.map((entry) => ({
      ...(entry as object),
      id: asBuffer(entry.id),
    })) as PublicKeyCredentialDescriptor[],
  } as PublicKeyCredentialRequestOptions;
}

function encode(buffer: ArrayBuffer | null | undefined): string | undefined {
  return buffer ? base64urlEncode(new Uint8Array(buffer)) : undefined;
}

// The hand-made serialisation for a credential that has no toJSON().
function serialise(credential: PublicKeyCredential): Json {
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      authenticatorData: encode(response.authenticatorData),
      clientDataJSON: encode(response.clientDataJSON),
      signature: encode(response.signature),
      userHandle: encode(response.userHandle),
    },
  };
}

export async function assertWithPasskey(options: Json): Promise<Json> {
  const parse = PublicKeyCredential.parseRequestOptionsFromJSON;
  const publicKey = parse
    ? parse.call(PublicKeyCredential, options as unknown as PublicKeyCredentialRequestOptionsJSON)
    : parseOptions(options);
  const credential = await navigator.credentials.get({ publicKey });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('The browser returned no passkey assertion.');
  }
  return typeof credential.toJSON === 'function'
    ? (credential.toJSON() as unknown as Json)
    : serialise(credential);
}
