/**
 * Role glyphs: one small single-path picture and one label per bee role.
 *
 * A node says what a bee is by its glyph and its role word together, so a reader who cannot tell
 * the shapes still reads the word. The roles are the Warden, the six Worker roles the schema
 * names (`WorkerRole`) and an "other" for a sub-bee whose role the Brood Chamber could not name
 * (the page says `UNKNOWN`). Each glyph is a stroke path on a 16 by 16 grid, drawn in the text
 * colour, so it follows the theme.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Used by `model.ts` to describe a node
 *   and by `bee_node.tsx` to draw it.
 *
 * Key invariants:
 *   - Pure data: no colour, no size and no React here.
 *   - Every role string maps to a glyph; one this table does not know falls back to "other".
 *
 * See Also:
 *   - docs/observation/design.md section 4.5 (the glyph set).
 */

export type GlyphKey =
  'warden' | 'drone' | 'forager' | 'scout' | 'guard_bee' | 'undertaker' | 'house_bee' | 'other';

// Stroke paths on a 16 by 16 grid, one per role.
export const GLYPH_PATHS: Readonly<Record<GlyphKey, string>> = {
  warden: 'M2 12 L2 5 L5.5 8 L8 3 L10.5 8 L14 5 L14 12 Z',
  drone: 'M8 2 L13 5 L13 11 L8 14 L3 11 L3 5 Z',
  forager: 'M8 14 V7 M8 7 C4 7 3 4 3 2 C6 2 8 4 8 7 C8 4 10 2 13 2 C13 4 12 7 8 7',
  scout: 'M7 2 A5 5 0 1 0 7.01 2 Z M10.5 10.5 L14 14',
  guard_bee: 'M8 2 L13 4 V8 C13 11 10.5 13 8 14 C5.5 13 3 11 3 8 V4 Z',
  undertaker: 'M8 1 V8 M4 8 H12 L10.5 14 H5.5 Z',
  house_bee: 'M2 8 L8 2 L14 8 M4 7 V14 H12 V7',
  other: 'M8 2 L14 8 L8 14 L2 8 Z',
};

interface RoleLook {
  glyph: GlyphKey;
  label: string;
}

// The page's role strings (the Warden, or the Worker role of the bee's task) in the README's words.
const ROLES: Readonly<Record<string, RoleLook>> = {
  WARDEN: { glyph: 'warden', label: 'Warden' },
  DRONE: { glyph: 'drone', label: 'Drone' },
  FORAGER: { glyph: 'forager', label: 'Forager' },
  SCOUT: { glyph: 'scout', label: 'Scout' },
  GUARD_BEE: { glyph: 'guard_bee', label: 'Guard Bee' },
  UNDERTAKER: { glyph: 'undertaker', label: 'Undertaker' },
  HOUSE_BEE: { glyph: 'house_bee', label: 'House Bee' },
};

// UNKNOWN is the page's word for a sub-bee with no task to name its role; it reads as "Bee".
const OTHER: RoleLook = { glyph: 'other', label: 'Bee' };

export function roleLook(role: string): RoleLook {
  return ROLES[role] ?? OTHER;
}
