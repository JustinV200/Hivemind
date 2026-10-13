/**
 * The Honey view's words and addresses: kind labels, clearance names, and path to link.
 *
 * The schema's kind is an open enum (`honey|note|wax|bee_bread`, and it may grow), so a kind this
 * file does not know is shown as the schema gave it rather than hidden. Clearance names are the
 * README's: Wildflower (C0), Apiary (C1), Royal (C2). Paths are held with a leading slash; the
 * route table wants them without one.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by the badge, the rows, the breadcrumbs
 *   and the screen.
 *
 * Key invariants:
 *   - Pure: no state, no I/O.
 *   - `normalisePath` always returns a path that starts with `/` and has no trailing `/` (but `/`).
 *   - `folderHref` round-trips through the router: `#/honey` for the root, `#/honey/<path>` else.
 *
 * See Also:
 *   - README.md for the clearance vocabulary; src/router/routes.ts for the hash grammar.
 */
import { hrefFor } from '../../../router';
import type { components } from '../../../landing_board/types';

type Schemas = components['schemas'];

const KIND_LABELS: Readonly<Record<string, string>> = {
  honey: 'Honey',
  note: 'Note',
  wax: 'Cell Wax',
  bee_bread: 'Bee Bread',
};

export const CLEARANCE_NAMES: Readonly<Record<Schemas['HoneyClearance'], string>> = {
  C0: 'Wildflower',
  C1: 'Apiary',
  C2: 'Royal',
};

// An unknown kind is shown as sent, with its underscores read as spaces.
export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, ' ');
}

// The route hands the path over without its leading slash; the Entrance wants it with one.
export function normalisePath(route: string): string {
  const segments = route.split('/').filter((segment) => segment !== '');
  return `/${segments.join('/')}`;
}

export function folderHref(path: string): string {
  return hrefFor({ name: 'honey', path: path.replace(/^\/+/, '') });
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
