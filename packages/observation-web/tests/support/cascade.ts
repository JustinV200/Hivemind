/**
 * A small CSS cascade for layout tests: which `display` wins for an element at a given width.
 *
 * jsdom does not evaluate media queries and does not rank rules by specificity, so a test that
 * asks it whether a table is a table at 1280 px learns nothing. This reads a stylesheet as text,
 * keeps the rules whose `@media` (min-width and max-width only) holds at the width, ranks them by
 * specificity then source order as a browser does, and answers with the winning declaration. It
 * exists because a desktop rule one specificity step too weak left the Fleet's rows as cards.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the table layout tests; reads `src/**.css` as text.
 *
 * Key invariants:
 *   - Pseudo-element rules and `@media` queries it cannot read are skipped, never guessed at.
 *   - `!important` is not modelled; the stylesheets under test use none.
 *
 * See Also:
 *   - tests/styles/table_layout.test.ts, the user of this.
 */

export interface Rule {
  selector: string;
  decls: Map<string, string>;
  specificity: number;
  order: number;
}

// The file's text through the runtime's own `fs` by a name the bundler cannot see: Vitest blanks
// stylesheets outside `src/styles/` even with `?raw`, and the project has no Node typings.
export async function readCss(path: string): Promise<string> {
  const module = 'node:fs';
  const fs = (await import(/* @vite-ignore */ module)) as {
    readFileSync(file: string, encoding: 'utf8'): string;
  };
  return fs.readFileSync(path, 'utf8');
}

// Ids, then classes/attributes/pseudo-classes, then elements, packed so a plain compare ranks them.
export function specificityOf(selector: string): number {
  const ids = (selector.match(/#[\w-]+/g) ?? []).length;
  const classes = (selector.match(/\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+/g) ?? []).length;
  const bare = selector.replace(/#[\w-]+|\.[\w-]+|\[[^\]]*\]|::?[\w-]+(\([^)]*\))?/g, ' ');
  const elements = (bare.match(/[a-zA-Z][\w-]*/g) ?? []).length;
  return ids * 10000 + classes * 100 + elements;
}

function holds(query: string, width: number): boolean {
  const min = /\(min-width:\s*(\d+)px\)/.exec(query);
  const max = /\(max-width:\s*(\d+)px\)/.exec(query);
  // Anything else (orientation, colour scheme) is not a width rule: leave it out.
  const rest = query.replace(/\((min|max)-width:\s*\d+px\)/g, '').replace(/\band\b/g, '');
  if (rest.trim() !== '' || (!min && !max)) return false;
  return width >= Number(min?.[1] ?? 0) && width <= Number(max?.[1] ?? Infinity);
}

// Splits `a { b } c { d }` into [head, body] pairs at the top nesting level.
function blocks(css: string): [string, string][] {
  const found: [string, string][] = [];
  let depth = 0;
  let head = 0;
  let body = 0;
  for (let i = 0; i < css.length; i++) {
    if (css[i] === '{' && depth++ === 0) body = i;
    if (css[i] === '}' && --depth === 0) {
      found.push([css.slice(head, body).trim(), css.slice(body + 1, i)]);
      head = i + 1;
    }
  }
  return found;
}

function declarations(body: string): Map<string, string> {
  const decls = new Map<string, string>();
  for (const part of body.split(';')) {
    const colon = part.indexOf(':');
    if (colon > 0) decls.set(part.slice(0, colon).trim(), part.slice(colon + 1).trim());
  }
  return decls;
}

export function rulesAt(css: string, width: number): Rule[] {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const rules: Rule[] = [];
  const add = (source: string, order: { n: number }): void => {
    for (const [head, body] of blocks(source)) {
      if (head.startsWith('@media')) {
        if (holds(head.slice('@media'.length), width)) add(body, order);
        continue;
      }
      if (head.startsWith('@')) continue;
      const decls = declarations(body);
      for (const selector of head.split(',').map((s) => s.trim())) {
        if (selector.includes('::')) continue;
        rules.push({ selector, decls, specificity: specificityOf(selector), order: order.n++ });
      }
    }
  };
  add(plain, { n: 0 });
  return rules;
}

// The winning value of `property` for `element`, or null when no rule sets it.
export function winning(element: Element, rules: Rule[], property: string): string | null {
  let best: Rule | null = null;
  for (const rule of rules) {
    if (!rule.decls.has(property) || !element.matches(rule.selector)) continue;
    const ahead =
      best === null ||
      rule.specificity > best.specificity ||
      (rule.specificity === best.specificity && rule.order > best.order);
    if (ahead) best = rule;
  }
  return best?.decls.get(property) ?? null;
}
