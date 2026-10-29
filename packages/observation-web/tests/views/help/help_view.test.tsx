/**
 * Tests for the Help screen: the pages, the search, a link into a section, and the view link.
 *
 * The screen is mounted under a provider on the fake browser, since the title row reads the
 * connection slice. A test looks at what a person sees: the start page with the list and the
 * current page marked, hits appearing as the words are typed and going away on Clear, focus
 * landing on the section an address names, and the "More in Help" link a view's gloss carries.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/help/ and the help link of
 *   src/components/view_title.tsx.
 *
 * Key invariants:
 *   - No request is made: the fake browser's route table is empty.
 *
 * See Also:
 *   - tests/views/help/tour.test.tsx for the tour.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ReactElement } from 'react';
import { APP_SLICES } from '../../../src/app';
import { ViewTitle } from '../../../src/components';
import { createLandingBoard } from '../../../src/landing_board/client';
import { createHiveStore, HiveProvider } from '../../../src/store';
import { HelpScreen } from '../../../src/views/help';
import { createFakeBrowser } from '../../support/fake_browser';

function mount(element: ReactElement): void {
  const store = createHiveStore(APP_SLICES);
  const client = createLandingBoard({
    browser: createFakeBrowser().browser,
    onSessionLost: () => {},
  });
  render(<HiveProvider hive={{ store, client }}>{element}</HiveProvider>);
}

describe('HelpScreen', () => {
  it('opens on Start here, lists every page and marks the open one', () => {
    mount(<HelpScreen page={null} section={null} />);
    expect(screen.getByRole('heading', { name: 'Start here', level: 2 })).toBeTruthy();
    const pages = screen.getByRole('navigation', { name: 'Help pages' });
    expect(
      within(pages).getByRole('link', { name: 'Start here' }).getAttribute('aria-current'),
    ).toBe('page');
    expect(within(pages).getByRole('link', { name: 'Words' }).getAttribute('href')).toBe(
      '#/help/glossary',
    );
    expect(screen.getByRole('button', { name: 'Take the tour' })).toBeTruthy();
  });

  it('searches as the words are typed, links each hit to its section, and clears', () => {
    mount(<HelpScreen page="glossary" section={null} />);
    fireEvent.change(screen.getByLabelText('Search the help'), { target: { value: 'night veil' } });
    const results = screen.getByRole('region', { name: /^Results for "night veil"/ });
    expect(within(results).getByRole('heading', { name: 'Night Veil' })).toBeTruthy();
    expect(within(results).getAllByRole('link', { name: 'Words' })[0]?.getAttribute('href')).toBe(
      '#/help/glossary?s=night-veil',
    );
    fireEvent.change(screen.getByLabelText('Search the help'), { target: { value: 'xyzzy' } });
    expect(screen.getByText('No matches for "xyzzy".')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByRole('heading', { name: 'Words', level: 2 })).toBeTruthy();
  });

  it('lands focus on the section the address names', () => {
    mount(<HelpScreen page="glossary" section="warden" />);
    expect(document.activeElement?.id).toBe('help-warden');
    expect(document.activeElement?.textContent).toBe('Warden');
  });

  it("links a view's gloss to its section of the screens page", () => {
    mount(<ViewTitle title="Fleet" hint="Every machine." help="fleet" />);
    expect(screen.getByRole('link', { name: 'More in Help' }).getAttribute('href')).toBe(
      '#/help/screens?s=fleet',
    );
  });
});
