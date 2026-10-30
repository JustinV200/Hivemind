/**
 * Tests for the Enrol view's watch list: a task asking for the running screen or audio (ADR-0069).
 *
 * Mounted on a loopback session over a fake Entrance: a waiting request must say what it asks for,
 * on which machine, for which task and bee; Allow must post to that request's approve row and
 * drop it once the Queen settled it; and a refusal the Entrance gives (here the bare 403 of a tab
 * without the stand key) must be shown as its sentence, with the request still waiting.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/enrol/watch/ through the Enrol screen.
 *
 * Key invariants:
 *   - Assertions are on what the page shows and on the requests sent.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EnrolScreen } from '../../../src/views/enrol';
import { mountScreen } from '../devices/mount';
import { watchView } from '../devices/support';

const APPROVE = 'POST /v1/entrance/watch/watch_1/approve';

describe('the watch list', () => {
  it.each([
    [200, 'Allowed Take notes from the call.', 0],
    [403, "Only the Hive Stand's own browser tab can do this.", 1],
  ])('answers Allow with %i and shows %s', async (status, shown, left) => {
    const opened = await mountScreen(<EnrolScreen />, {
      listener: 'loopback',
      watch: [watchView()],
    });
    const card = await screen.findByRole('listitem', { name: 'Waiting: Take notes from the call' });
    expect(card.textContent).toContain('asks to hear and play sound and see and use your screen');
    expect(within(card).getByText('hive-stand')).toBeTruthy();
    // The role in words with its code on title, the ids as the tables draw them.
    expect(card.textContent).toContain('drone under warden_1');
    expect(within(card).getByTitle('DRONE').textContent).toBe('drone');

    opened.fake.respond(APPROVE, status, status === 200 ? watchView({ status: 'APPROVED' }) : '');
    opened.fake.respond('GET /v1/entrance/watch', 200, { requests: [] });
    fireEvent.click(within(card).getByRole('button', { name: 'Allow Take notes from the call' }));

    await screen.findByText(shown);
    expect(opened.sent(APPROVE)).toHaveLength(1);
    await waitFor(() =>
      expect(screen.queryAllByRole('listitem', { name: /^Waiting: Take notes/ })).toHaveLength(
        left,
      ),
    );
  });
});
