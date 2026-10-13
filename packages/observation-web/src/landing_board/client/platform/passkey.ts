/**
 * Passkey assertion for login and step-up, and passkey creation for enrolment.
 *
 * The challenge's `passkey_options` is `PublicKeyCredentialRequestOptionsJSON`, with binary fields
 * as base64url strings. Current browsers parse it themselves (`parseRequestOptionsFromJSON`) and
 * serialise the answer with `toJSON()`; this file does the same by hand for the browsers that
 * lack those two methods. The result is the assertion JSON `LoginRequest.assertion` expects.
 * Enrolment is the same shape the other way: an invite's `PublicKeyCredentialCreationOptionsJSON`
 * goes to `navigator.credentials.create`, and the registration JSON goes back to the Entrance.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Used by `real_browser.ts` as
 *   `Browser.assertPasskey` and `Browser.createPasskey`. This is the "passkey plumbing" half of
 *   ADR-0049's platform seam.
 *
 * Key invariants:
 *   - User verification is whatever the options require; nothing here weakens or skips it.
 *   - An assertion or a registration is only ever returned to the caller, never stored or logged.
 *
 * See Also:
 *   - docs/entrance/landing-board.md sections 4 and 5 for the enrolment and login ceremonies.
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

// The hand-made parse for a browser without PublicKeyCredential.parseCreationOptionsFromJSON.
function parseCreation(json: Json): PublicKeyCredentialCreationOptions {
  const user = (json.user ?? {}) as Json;
  const exclude = Array.isArray(json.excludeCredentials) ? (json.excludeCredentials as Json[]) : [];
  return {
    ...(json as object),
    challenge: asBuffer(json.challenge),
    user: { ...(user as object), id: asBuffer(user.id) },
    excludeCredentials: exclude.map((entry) => ({
      ...(entry as object),
      id: asBuffer(entry.id),
    })) as PublicKeyCredentialDescriptor[],
  } as PublicKeyCredentialCreationOptions;
}

// The hand-made serialisation for a registration that has no toJSON().
function serialiseRegistration(credential: PublicKeyCredential): Json {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      attestationObject: encode(response.attestationObject),
      clientDataJSON: encode(response.clientDataJSON),
      transports: response.getTransports(),
    },
  };
}

export async function createWithPasskey(options: Json): Promise<Json> {
  const parse = PublicKeyCredential.parseCreationOptionsFromJSON;
  const publicKey = parse
    ? parse.call(PublicKeyCredential, options as unknown as PublicKeyCredentialCreationOptionsJSON)
    : parseCreation(options);
  const credential = await navigator.credentials.create({ publicKey });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('The browser returned no passkey registration.');
  }
  return typeof credential.toJSON === 'function'
    ? (credential.toJSON() as unknown as Json)
    : serialiseRegistration(credential);
}
