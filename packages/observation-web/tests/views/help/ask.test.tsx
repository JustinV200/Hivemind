/**
 * Tests for the Help screen's Ask: a question is answered with its sources, or refused in words.
 *
 * The screen is mounted at `#/help/ask` on the fake browser, which answers `GET /v1/help/ask`
 * from a table. A test looks at what a person sees: the note that an AI helper answers, the
 * example questions, the question echoed while the answer is out, then the answer with each
 * source as a link into its page and `(cited)` after the ones the answer rests on; a links-only
 * answer says why in a sentence; and a refusal shows the error notice's words.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/help/ask/ through the Help screen.
 *
 * Key invariants:
 *   - Exactly one request per question, and it is a GET with the words in the query.
 *
 * See Also:
 *   - tests/views/help/help_view.test.tsx for the rest of the screen.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { APP_SLICES } from '../../../src/app';
import { createLandingBoard } from '../../../src/landing_board/client';
import type { components } from '../../../src/landing_board/types';
import { createHiveStore, HiveProvider } from '../../../src/store';
import { HelpScreen } from '../../../src/views/help';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';

type HelpAnswer = components['schemas']['HelpAnswerView'];

const ANSWERED: HelpAnswer = {
  question: 'What is the Night Veil?',
  outcome: 'ANSWERED',
  answer: 'The sealed tier: a Virtual Cell behind a VPN and Tor [1].',
  sources: [
    {
      page: 'glossary',
      section: 'night-veil',
      heading: 'Night Veil',
      snippet: 'The sealed tier',
      cited: true,
    },
    { page: 'bees', section: null, heading: '', snippet: 'The Queen, Wardens', cited: false },
  ],
  slot: 'ATTENDANT',
};

function mount(): FakeBrowser {
  const fake = createFakeBrowser();
  const store = createHiveStore(APP_SLICES);
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  render(
    <HiveProvider hive={{ store, client }}>
      <HelpScreen page="ask" section={null} />
    </HiveProvider>,
  );
  return fake;
}

describe('Ask', () => {
  it('answers a question with its sources linked into the pages, the cited ones marked', async () => {
    const fake = mount();
    fake.respond('GET /v1/help/ask', 200, ANSWERED);
    expect(screen.getByText(/An AI helper answers from these help pages/)).toBeTruthy();
    const pages = screen.getByRole('navigation', { name: 'Help pages' });
    expect(within(pages).getByRole('link', { name: 'Ask' }).getAttribute('aria-current')).toBe(
      'page',
    );

    fireEvent.change(screen.getByLabelText('Your question'), {
      target: { value: 'What is the Night Veil?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    expect(screen.getByText('What is the Night Veil?', { exact: false })).toBeTruthy();
    await waitFor(() => expect(screen.getByText(ANSWERED.answer!)).toBeTruthy());

    expect(fake.requests.map((r) => `${r.method} ${r.target}`)).toEqual([
      'GET /v1/help/ask?q=What+is+the+Night+Veil%3F',
    ]);
    const answers = within(screen.getByRole('list', { name: 'Answers' }));
    const veil = answers.getByRole('link', { name: 'Words: Night Veil' });
    expect(veil.getAttribute('href')).toBe('#/help/glossary?s=night-veil');
    expect(veil.parentElement?.textContent).toBe('Words: Night Veil (cited)');
    const bees = answers.getByRole('link', { name: 'Bees and Cells' });
    expect(bees.getAttribute('href')).toBe('#/help/bees');
    expect(bees.parentElement?.textContent).toBe('Bees and Cells');
  });

  it('says in words why there are no words, and shows a refusal as the error notice', async () => {
    const fake = mount();
    fake.respond('GET /v1/help/ask', 200, {
      ...ANSWERED,
      outcome: 'NO_MODEL',
      answer: null,
      sources: ANSWERED.sources.map((s) => ({ ...s, cited: false })),
    });

    fireEvent.click(screen.getByRole('button', { name: 'What is a Warden?' }));
    await waitFor(() =>
      expect(screen.getByText(/No model is configured on this Hive/)).toBeTruthy(),
    );
    expect(screen.queryByText('(cited)')).toBeNull();

    fake.respond('GET /v1/help/ask', 429, {
      error: 'hivemind.entrance.help_over_budget',
      detail: 'This session has asked its help questions for the minute; ask again shortly.',
    });
    fireEvent.click(screen.getByRole('button', { name: 'How do I sort the Fleet table?' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('You have asked the help as many questions');
    expect(fake.requests).toHaveLength(2);
  });
});
