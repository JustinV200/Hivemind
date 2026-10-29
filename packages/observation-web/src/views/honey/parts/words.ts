/**
 * The Honey view's words and addresses: kind labels, clearance names, and path to link.
 *
 * The schema's kind is an open enum (`honey|note|wax|bee_bread`, and it may grow), so a kind this
 * file does not know is shown in plain case (`plainCode`) rather than hidden. Clearance names are
 * the README's (Wildflower, Apiary, Royal), shared with the other views from `components/words`.
 * Paths are held with a leading slash; the route table wants them without one. An item's address
 * is its folder's with `?item=<id>`, so an open reading pane is a link that can be shared and
 * comes back on reload.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by the badge, the rows, the breadcrumbs
 *   and the screen.
 *
 * Key invariants:
 *   - Pure: no state, no I/O.
 *   - `normalisePath` always returns a path that starts with `/` and has no trailing `/` (but `/`).
 *   - `folderHref` round-trips through the router: `#/honey` for the root, `#/honey/<path>` else;
 *     `itemHref` adds the item to the same address.
 *
 * See Also:
 *   - README.md for the clearance vocabulary; src/router/routes.ts for the hash grammar.
 */
import { plainCode } from '../../../components';
import { hrefFor } from '../../../router';

const KIND_LABELS: Readonly<Record<string, string>> = {
  honey: 'Honey',
  note: 'Note',
  wax: 'Cell Wax',
  bee_bread: 'Bee Bread',
};

// An unknown kind is shown as sent, in the plain case every other code takes (design.md 8).
export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? plainCode(kind);
}

// The route hands the path over without its leading slash; the Entrance wants it with one.
export function normalisePath(route: string): string {
  const segments = route.split('/').filter((segment) => segment !== '');
  return `/${segments.join('/')}`;
}

export function folderHref(path: string): string {
  return hrefFor({ name: 'honey', path: path.replace(/^\/+/, ''), item: null });
}

export function itemHref(path: string, item: string): string {
  return hrefFor({ name: 'honey', path: path.replace(/^\/+/, ''), item });
}

export interface Crumb {
  name: string;
  path: string;
}

// One crumb per path segment, each with the path up to and including it, behind the root.
export function crumbsOf(path: string): Crumb[] {
  const segments = path.split('/').filter((segment) => segment !== '');
  const crumbs = segments.map((name, index) => ({
    name,
    path: `/${segments.slice(0, index + 1).join('/')}`,
  }));
  return [{ name: 'Honey', path: '/' }, ...crumbs];
}
