/**
 * A markdown subset for the help pages: headings, paragraphs, lists, and inline bold, code and
 * links, parsed into plain blocks.
 *
 * The help content is kept as markdown files under `content/`, so a person can maintain the words
 * without touching code, and the app bundles their text with Vite's `?raw` import. The app has no
 * markdown dependency (every runtime dependency is audited for as long as the Hive ships it,
 * ADR-0049), and the pages need only a small grammar, so this file parses exactly that grammar and
 * nothing more: `##` and `###` headings, paragraphs, `-` lists, `**bold**`, `` `code` `` and
 * `[text](href)`. Anything else is text. Every heading gets an id from its words, which is what a
 * link into a section (`#/help/<page>?s=<id>`) names.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/`. Read by `render.tsx` (blocks to React) and
 *   `search.ts` (blocks to sections).
 *
 * Key invariants:
 *   - Pure: no state, no I/O; the same text gives the same blocks.
 *   - A heading's id is `slugOf(its text)`, so a link can be written from the heading alone.
 *   - Unknown markup is never dropped: it stays in the text as written.
 *
 * See Also:
 *   - src/views/help/content/ for the pages this parses.
 */

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'link'; text: string; href: string };

export type Block =
  | { kind: 'heading'; level: 2 | 3; id: string; text: string }
  | { kind: 'paragraph'; spans: Inline[] }
  | { kind: 'list'; items: Inline[][] };

// The id a heading's words make: lower case, runs of anything else as one hyphen.
export function slugOf(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

// One inline token at a time: code first (its text is literal), then bold, then a link.
const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g;

export function inlineOf(text: string): Inline[] {
  const spans: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index;
    // The plain text before this token.
    if (at > last) spans.push({ kind: 'text', text: text.slice(last, at) });
    const token = match[0];
    if (token.startsWith('`')) spans.push({ kind: 'code', text: token.slice(1, -1) });
    else if (token.startsWith('**')) spans.push({ kind: 'strong', text: token.slice(2, -2) });
    else {
      const close = token.indexOf('](');
      spans.push({ kind: 'link', text: token.slice(1, close), href: token.slice(close + 2, -1) });
    }
    last = at + token.length;
  }
  if (last < text.length) spans.push({ kind: 'text', text: text.slice(last) });
  return spans;
}

// The words of a run of spans, for search and for a heading's accessible name.
export function plainText(spans: readonly Inline[]): string {
  return spans.map((span) => span.text).join('');
}

export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  // The lines gathered so far for the open paragraph or list; a blank line closes either.
  let paragraph: string[] = [];
  let list: string[] = [];
  const close = (): void => {
    if (paragraph.length > 0)
      blocks.push({ kind: 'paragraph', spans: inlineOf(paragraph.join(' ')) });
    if (list.length > 0) blocks.push({ kind: 'list', items: list.map(inlineOf) });
    paragraph = [];
    list = [];
  };
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    const heading = /^(##|###) (.+)$/.exec(line);
    if (line === '') {
      close();
    } else if (heading) {
      close();
      const level = heading[1] === '##' ? 2 : 3;
      blocks.push({ kind: 'heading', level, id: slugOf(heading[2]!), text: heading[2]! });
    } else if (line.startsWith('- ')) {
      // A list item; a paragraph open before it ends, since the markup changed.
      if (paragraph.length > 0) close();
      list.push(line.slice(2));
    } else if (list.length > 0 && line.startsWith('  ')) {
      // An indented line continues the item above it.
      list[list.length - 1] += ` ${line.trim()}`;
    } else {
      if (list.length > 0) close();
      paragraph.push(line.trim());
    }
  }
  close();
  return blocks;
}
