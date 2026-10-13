/**
 * Pure builders for the Landing Board's signed strings, plus the encodings they use.
 *
 * Every authenticated request and every socket opening carries a signature over a fixed string
 * (`hive-request-v1`, `hive-ws-v1`), and the Entrance verifies it byte for byte under the
 * session's binding key. The strings, their field order, the line-feed join and the encodings are
 * published in `x-hive-signing` in the OpenAPI document; this file transcribes them and nothing
 * else. It holds no keys and does no I/O, so it is tested by rebuilding the document's worked
 * example exactly.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/`. Used by `http.ts` (requests),
 *   `socket.ts` (the first frame), `keys.ts` (base64url of keys and signatures) and `session.ts`.
 *
 * Key invariants:
 *   - A signed string is its tag, then its fields, one per line, joined by U+000A with no trailing
 *     newline; no field may contain a line break (a field with one would let a path forge a
 *     second field, so it is refused rather than escaped).
 *   - base64url is unpadded everywhere, and a nonce is at least 16 random bytes.
 *   - Timestamps are integer Unix seconds.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `x-hive-signing`, for the strings and the worked example.
 *   - docs/entrance/landing-board.md section 6.
 */
import type { Bytes, SubtleLike } from './browser';

// The SHA-256 of zero bytes: the body hash of a request that has no body.
export const EMPTY_BODY_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

// 16 random bytes is the document's floor for a nonce; encoded it is 22 characters.
export const NONCE_BYTES = 16;

export interface RequestFields {
  method: string;
  target: string;
  timestamp: number;
  nonce: string;
  bodySha256: string;
}

export interface SocketFields {
  target: string;
  timestamp: number;
  nonce: string;
}

export function base64urlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  // btoa gives padded standard base64; the contract wants the URL alphabet with no padding.
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function base64urlDecode(text: string): Bytes {
  const standard = text.replace(/-/g, '+').replace(/_/g, '/');
  const padded = standard + '='.repeat((4 - (standard.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

export function joinSigned(tag: string, fields: string[]): string {
  for (const field of fields) {
    if (/[\r\n]/.test(field)) throw new Error(`${tag}: a signed field contains a line break`);
  }
  return [tag, ...fields].join('\n');
}

export function requestString(fields: RequestFields): string {
  return joinSigned('hive-request-v1', [
    fields.method.toUpperCase(),
    fields.target,
    String(fields.timestamp),
    fields.nonce,
    fields.bodySha256,
  ]);
}

export function socketString(fields: SocketFields): string {
  return joinSigned('hive-ws-v1', [fields.target, String(fields.timestamp), fields.nonce]);
}

export function unixSeconds(ms: number): number {
  return Math.floor(ms / 1000);
}

export function newNonce(randomBytes: (length: number) => Bytes): string {
  return base64urlEncode(randomBytes(NONCE_BYTES));
}

export async function sha256Hex(subtle: SubtleLike, bytes: Bytes): Promise<string> {
  const digest = new Uint8Array(await subtle.digest('SHA-256', bytes));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
