/**
 * Tests for the help pages' markdown subset and the search over their sections.
 *
 * The parser must turn the grammar the pages use (headings with ids, paragraphs joined across
 * lines, lists with continuation lines, bold, code and links) into blocks and leave anything else
 * as text; the search must find a section by every word of the query and put a heading that says
 * the query before a passing mention.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/help/markdown.ts and search.ts.
 *
 * Key invariants:
 *   - The search test reads the real pages, so a renamed glossary entry shows here.
 *
 * See Also:
 *   - src/views/help/README.md.
 */
import { describe, expect, it } from 'vitest';
import { parseMarkdown } from '../../../src/views/help/markdown';
import { searchHelp } from '../../../src/views/help/search';

describe('parseMarkdown', () => {
  it('parses headings with ids, joined paragraphs, lists and inline marks', () => {
    const blocks = parseMarkdown(
      [
        'Opening line',
        'joined to it.',
        '',
        '## Comb Shield level',
        '',
        'A **tier** with `MEADOW` and [a link](#/fleet).',
        '',
        '- first item',
        '  continued',
        '- second',
        '### Sub',
        'tail',
      ].join('\n'),
    );
    expect(blocks).toEqual([
      { kind: 'paragraph', spans: [{ kind: 'text', text: 'Opening line joined to it.' }] },
      { kind: 'heading', level: 2, id: 'comb-shield-level', text: 'Comb Shield level' },
      {
        kind: 'paragraph',
        spans: [
          { kind: 'text', text: 'A ' },
          { kind: 'strong', text: 'tier' },
          { kind: 'text', text: ' with ' },
          { kind: 'code', text: 'MEADOW' },
          { kind: 'text', text: ' and ' },
          { kind: 'link', text: 'a link', href: '#/fleet' },
          { kind: 'text', text: '.' },
        ],
      },
      {
        kind: 'list',
        items: [
          [{ kind: 'text', text: 'first item continued' }],
          [{ kind: 'text', text: 'second' }],
        ],
      },
      { kind: 'heading', level: 3, id: 'sub', text: 'Sub' },
      { kind: 'paragraph', spans: [{ kind: 'text', text: 'tail' }] },
    ]);
  });
});

describe('searchHelp', () => {
  it('finds a section by every word, the heading that says the query first', () => {
    const hits = searchHelp('night veil');
    expect(hits[0]?.page.id).toBe('glossary');
    expect(hits[0]?.heading).toBe('Night Veil');
    // A section that only mentions the words is found too, after the entry.
    expect(hits.length).toBeGreaterThan(1);
    expect(hits.every((hit) => hit.words.includes('night') && hit.words.includes('veil'))).toBe(
      true,
    );
  });

  it('matches nothing for an empty query or words no page holds', () => {
    expect(searchHelp('   ')).toEqual([]);
    expect(searchHelp('xyzzy plugh')).toEqual([]);
  });
});
