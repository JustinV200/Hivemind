/**
 * Tests for passkey creation: the browser's own parse and serialise, and the hand-made fallback.
 *
 * jsdom has no WebAuthn, so the tests stand in a `PublicKeyCredential` and `navigator.credentials`.
 * With the modern statics the options go in and the registration comes out through the browser's
 * own methods; without them the file must decode the base64url members itself and encode the
 * attestation back, since that is the shape the Entrance verifies.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/platform/passkey.ts.
 *
 * Key invariants:
 *   - Binary members reach the browser as buffers and leave it as unpadded base64url.
 *   - A browser that returns no credential is an error, never an empty registration.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createWithPasskey } from '../../../src/landing_board/client/platform/passkey';
import { base64urlEncode } from '../../../src/landing_board/client/signing';

const OPTIONS = {
  challenge: 'AQID',
  rp: { id: 'hive.test', name: 'Hive' },
  user: { id: 'BAUG', name: 'op', displayName: 'op' },
  excludeCredentials: [{ id: 'BwgJ', type: 'public-key' }],
  pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
};

// What the hand-made parse hands the browser: the binary members as buffers.
interface Parsed {
  challenge: ArrayBuffer;
  user: { id: ArrayBuffer };
  excludeCredentials: { id: ArrayBuffer }[];
  rp: unknown;
}

const bytes = (...values: number[]): ArrayBuffer => new Uint8Array(values).buffer;

function stubBrowser(
  credential: unknown,
  statics: Record<string, unknown> = {},
): { created: unknown[] } {
  const created: unknown[] = [];
  class Credential {
    static parseCreationOptionsFromJSON = statics.parseCreationOptionsFromJSON;
    id = 'cred-1';
    rawId = bytes(1, 2);
    type = 'public-key';
    authenticatorAttachment = 'platform';
    response = {
      attestationObject: bytes(9, 9),
      clientDataJSON: bytes(8, 8),
      getTransports: () => ['internal'],
    };
    toJSON = statics.toJSON as (() => unknown) | undefined;
    getClientExtensionResults = () => ({});
  }
  vi.stubGlobal('PublicKeyCredential', Credential);
  Object.defineProperty(navigator, 'credentials', {
    configurable: true,
    value: {
      create: (init: unknown) => {
        created.push(init);
        return Promise.resolve(credential === 'instance' ? new Credential() : credential);
      },
    },
  });
  return { created };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('createWithPasskey', () => {
  it('hands the options to the browser to parse and returns what its toJSON gives', async () => {
    const parsed = { parsed: true };
    const { created } = stubBrowser('instance', {
      parseCreationOptionsFromJSON: () => parsed,
      toJSON: () => ({ id: 'cred-1', via: 'toJSON' }),
    });
    expect(await createWithPasskey(OPTIONS)).toEqual({ id: 'cred-1', via: 'toJSON' });
    expect(created).toEqual([{ publicKey: parsed }]);
  });

  it('decodes the binary members by hand and encodes the attestation back, without the statics', async () => {
    const { created } = stubBrowser('instance');
    const registration = await createWithPasskey(OPTIONS);
    const { publicKey } = created[0] as { publicKey: Parsed };
    expect([...new Uint8Array(publicKey.challenge)]).toEqual([1, 2, 3]);
    expect([...new Uint8Array(publicKey.user.id)]).toEqual([4, 5, 6]);
    expect([...new Uint8Array(publicKey.excludeCredentials[0]!.id)]).toEqual([7, 8, 9]);
    expect(publicKey.rp).toEqual(OPTIONS.rp);
    expect(registration).toMatchObject({
      id: 'cred-1',
      rawId: base64urlEncode(new Uint8Array([1, 2])),
      type: 'public-key',
      authenticatorAttachment: 'platform',
      response: {
        attestationObject: base64urlEncode(new Uint8Array([9, 9])),
        clientDataJSON: base64urlEncode(new Uint8Array([8, 8])),
        transports: ['internal'],
      },
    });
  });

  it('refuses when the browser returns no credential', async () => {
    stubBrowser(null);
    await expect(createWithPasskey(OPTIONS)).rejects.toThrow('no passkey registration');
  });
});
