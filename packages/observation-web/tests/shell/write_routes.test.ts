/**
 * The route-table test of codingrules 8.11: the UI can write nowhere but the Queen's inbox.
 *
 * The guard in the HTTP wrapper is only worth having if the document's mutating routes are
 * checked against it. This test lists every mutating operation in the committed OpenAPI document
 * and sends each through the wrapper. The allowed ones must reach `fetch`; the others must be
 * refused before anything is sent. The set that is refused is written out by hand, so a new
 * mutating route in the document fails this test until a person decides whether the UI may use it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/write_guard.ts through
 *   src/landing_board/client/http.ts.
 *
 * Key invariants:
 *   - The allow-list is compared with the exact list in the step: chat and answers into the
 *     inbox, plus the Entrance's own routes (auth, goals, push subscriptions, entrance, devices)
 *     and the two passkey routes of enrolment; a program's Ed25519 enrolment stays refused.
 *
 * See Also:
 *   - .claude/codingrules.md section 8.11 and docs/entrance/openapi.json.
 */
import { describe, expect, it } from 'vitest';
import document from '../../../../docs/entrance/openapi.json';
import { createHttp } from '../../src/landing_board/client/http';
import { createHolder } from '../../src/landing_board/client/keys';
import { isWriteAllowed, WRITE_PATTERNS } from '../../src/landing_board/client/write_guard';
import type { Method } from '../../src/landing_board/call_types';
import { createFakeBrowser } from '../support/fake_browser';
import { makeSession } from '../support/session_fixture';

interface Mutating {
  method: Method;
  template: string;
  path: string;
}

// Every non-GET operation in the document, with `{placeholders}` filled so the path is concrete.
function mutatingRoutes(): Mutating[] {
  const found: Mutating[] = [];
  for (const [template, item] of Object.entries(document.paths)) {
    for (const method of Object.keys(item)) {
      if (method === 'get' || method === 'parameters') continue;
      found.push({ method: method as Method, template, path: template.replace(/\{[^}]+\}/g, 'x') });
    }
  }
  return found;
}

// The mutating routes the UI must never reach, decided by hand.
const REFUSED = [
  'POST /v1/cells/{cell_id}/isolate',
  'POST /v1/cells/{cell_id}/lift',
  'POST /v1/enrol/ed25519',
];

describe('write list', () => {
  it('is exactly the list of the step', () => {
    expect([...WRITE_PATTERNS]).toEqual([
      '/v1/auth/*',
      '/v1/chat',
      '/v1/chat/audio',
      '/v1/inbox/*/answer',
      '/v1/inbox/*/acknowledge',
      '/v1/goals*',
      '/v1/push/subscriptions*',
      '/v1/entrance/*',
      '/v1/devices/*',
      '/v1/enrol/passkey*',
    ]);
  });

  it('refuses exactly the hand-written set of mutating routes in the document', () => {
    const refused = mutatingRoutes()
      .filter((route) => !isWriteAllowed(route.path))
      .map((route) => `${route.method.toUpperCase()} ${route.template}`);
    expect(refused.sort()).toEqual([...REFUSED].sort());
  });

  it('lets a browser enrol with a passkey and nothing else under /v1/enrol', () => {
    expect(isWriteAllowed('/v1/enrol/passkey-options')).toBe(true);
    expect(isWriteAllowed('/v1/enrol/passkey')).toBe(true);
    expect(isWriteAllowed('/v1/enrol/ed25519')).toBe(false);
    expect(isWriteAllowed('/v1/enrol/hive')).toBe(false);
  });

  it('lets the inbox routes through: chat, answer and acknowledge', () => {
    const allowed = mutatingRoutes().filter((route) => isWriteAllowed(route.path));
    const templates = allowed.map((route) => route.template);
    expect(templates).toEqual(
      expect.arrayContaining([
        '/v1/chat',
        '/v1/chat/audio',
        '/v1/inbox/questions/{question_id}/answer',
        '/v1/inbox/alarms/{alarm_id}/acknowledge',
      ]),
    );
  });

  it('does not let a GET-only route be written to by prefix tricks', () => {
    for (const path of [
      '/v1/chatter',
      '/v1/chat/x',
      '/v1/inbox',
      '/v1/inbox/answer',
      '/v1/cells/x/isolate',
      '/v1/tasks/x',
    ]) {
      expect(isWriteAllowed(path)).toBe(false);
    }
  });
});

describe('the wrapper enforces the list', () => {
  it('sends every allowed mutating route and refuses every other before any request', async () => {
    for (const route of mutatingRoutes()) {
      const fake = createFakeBrowser();
      const holder = createHolder();
      holder.set(await makeSession());
      const http = createHttp({ browser: fake.browser, holder, onUnauthorized: () => {} });
      fake.respond(`${route.method.toUpperCase()} ${route.path}`, 200, {});
      // The probe loosens the typed call so it can name a route the way the document lists it;
      // the wrapper under test is the real one, guard and all.
      const probe = http.call as unknown as (method: string, path: string) => Promise<unknown>;
      const outcome = await probe(route.method, route.path).catch((e: unknown) => e);
      const label = `${route.method.toUpperCase()} ${route.template}`;
      if (REFUSED.includes(label)) {
        expect(outcome, label).toMatchObject({ code: 'hivemind.client.write_refused' });
        expect(fake.requests, label).toHaveLength(0);
      } else {
        expect(outcome, label).toEqual({});
        expect(fake.requests, label).toHaveLength(1);
      }
    }
  });
});
