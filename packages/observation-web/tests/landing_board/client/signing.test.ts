/**
 * Tests for the signed strings and encodings of the Landing Board client.
 *
 * The Entrance verifies signatures byte for byte, so the strings must match `x-hive-signing`
 * exactly. The strongest check available is the document's own worked example: rebuild its
 * `signed_string` from its fields and compare. The document is read from the committed
 * `docs/entrance/openapi.json`, so a change to the contract breaks the test.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/signing.ts.
 *
 * Key invariants:
 *   - Every assertion is on exact bytes or exact alphabets, never on shape alone.
 *
 * See Also:
 *   - docs/entrance/openapi.json, `x-hive-signing`.
 */
import { describe, expect, it } from 'vitest';
import document from '../../../../../docs/entrance/openapi.json';
import {
  base64urlDecode,
  base64urlEncode,
  EMPTY_BODY_SHA256,
  newNonce,
  requestString,
  sha256Hex,
  socketString,
  unixSeconds,
} from '../../../src/landing_board/client/signing';

const example = document['x-hive-signing'].example;

describe('requestString', () => {
  it('rebuilds the documented example byte for byte', async () => {
    // The body hash is computed from the example's body, not copied, so the whole chain is tested.
    const bodySha256 = await sha256Hex(crypto.subtle, new TextEncoder().encode(example.body));
    const built = requestString({
      method: example.method,
      target: example.target,
      timestamp: example.timestamp,
      nonce: example.nonce,
      bodySha256,
    });
    expect(built).toBe(example.signed_string);
  });

  it('upper-cases the method and has no trailing newline', () => {
    const built = requestString({
      method: 'get',
      target: '/v1/cells?x=1',
      timestamp: 5,
      nonce: 'n',
      bodySha256: EMPTY_BODY_SHA256,
    });
    expect(built).toBe(`hive-request-v1\nGET\n/v1/cells?x=1\n5\nn\n${EMPTY_BODY_SHA256}`);
    expect(built.endsWith('\n')).toBe(false);
  });

  it('refuses a field with a line break instead of signing a forged second field', () => {
    expect(() =>
      requestString({
        method: 'GET',
        target: '/v1/a\n/v1/b',
        timestamp: 1,
        nonce: 'n',
        bodySha256: 'h',
      }),
    ).toThrow(/line break/);
  });
});

describe('socketString', () => {
  it('is the tag, the raw path and query, the timestamp and the nonce', () => {
    expect(socketString({ target: '/v1/chat/stream?after=7', timestamp: 9, nonce: 'abc' })).toBe(
      'hive-ws-v1\n/v1/chat/stream?after=7\n9\nabc',
    );
  });
});

describe('body hash', () => {
  it('uses the documented constant for an empty body', async () => {
    expect(await sha256Hex(crypto.subtle, new Uint8Array(0))).toBe(EMPTY_BODY_SHA256);
  });
});

describe('nonce', () => {
  it('is 16 random bytes in unpadded base64url, fresh each time', () => {
    const random = (length: number) => crypto.getRandomValues(new Uint8Array(length));
    const first = newNonce(random);
    expect(first).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(base64urlDecode(first)).toHaveLength(16);
    expect(newNonce(random)).not.toBe(first);
  });

  it('encodes the documented example bytes to the documented nonce', () => {
    const bytes = Uint8Array.from({ length: 16 }, (_, i) => i);
    expect(base64urlEncode(bytes)).toBe(example.nonce);
  });
});

describe('encodings', () => {
  it('round-trips bytes that need the URL alphabet, with no padding', () => {
    const bytes = Uint8Array.from([251, 255, 254, 0, 63, 62]);
    const text = base64urlEncode(bytes);
    expect(text).not.toMatch(/[+/=]/);
    expect([...base64urlDecode(text)]).toEqual([...bytes]);
  });

  it('takes integer Unix seconds from milliseconds', () => {
    expect(unixSeconds(1_767_225_600_999)).toBe(1_767_225_600);
  });
});
