/**
 * `Markdown`: the help pages' blocks drawn as headings, paragraphs and lists.
 *
 * Each block from `markdown.ts` becomes one element. A heading carries the id its words make,
 * prefixed `help-`, so a link into a section (`#/help/<page>?s=<id>`) can find and focus it; it is
 * focusable by script only, never by Tab, so landing on a section reads its heading first. A link
 * in the text is a plain anchor: a hash route (`#/fleet`, `#/help/forage`) goes through the router
 * like every nav link.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/`. Drawn by `help_screen.tsx` for a page and for
 *   each search hit.
 *
 * Key invariants:
 *   - Holds no state; the same blocks draw the same elements.
 *   - Headings are `h2` and `h3` under the view's `h1`, so the page keeps one top heading.
 *
 * See Also:
 *   - src/views/help/markdown.ts for the grammar.
 */
import type { ReactElement } from 'react';
import type { Block, Inline } from './markdown';

// The element id a section heading carries, which `?s=<id>` and the legends' links name.
export const headingId = (id: string): string => `help-${id}`;

function Spans(props: { spans: readonly Inline[] }): ReactElement {
  return (
    <>
      {props.spans.map((span, i) => {
        switch (span.kind) {
          case 'strong':
            return <strong key={i}>{span.text}</strong>;
          case 'code':
            return <code key={i}>{span.text}</code>;
          case 'link':
            return (
              <a key={i} href={span.href}>
                {span.text}
              </a>
            );
          default:
            return span.text;
        }
      })}
    </>
  );
}

export function Markdown(props: { blocks: readonly Block[] }): ReactElement {
  return (
    <>
      {props.blocks.map((block, i) => {
        switch (block.kind) {
          case 'heading': {
            const Tag = block.level === 2 ? 'h2' : 'h3';
            return (
              <Tag key={i} id={headingId(block.id)} className="help-heading" tabIndex={-1}>
                {block.text}
              </Tag>
            );
          }
          case 'list':
            return (
              <ul key={i}>
                {block.items.map((item, j) => (
                  <li key={j}>
                    <Spans spans={item} />
                  </li>
                ))}
              </ul>
            );
          default:
            return (
              <p key={i}>
                <Spans spans={block.spans} />
              </p>
            );
        }
      })}
    </>
  );
}
