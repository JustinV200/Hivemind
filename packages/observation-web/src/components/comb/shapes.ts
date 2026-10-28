/**
 * Comb shapes as SVG path data: the hexagon card, the hex-cornered frame and the small cell.
 *
 * The diagrams draw bees as comb cells (a flat-top hexagon stretched wide enough for text), the
 * Cell as a frame with its corners cut, and a role glyph inside a small regular hexagon. These are
 * pure functions from a rectangle to path data, so the Cell diagram and the task graph share one
 * geometry and a test can check the numbers. Every value is rounded to whole units, which keeps
 * two renders of the same layout byte-identical.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/comb/`. Used by `src/views/cell/diagram/` and
 *   `src/views/attendant/graph/`.
 *
 * Key invariants:
 *   - Pure and deterministic; no React, no colour.
 *   - A shape never leaves its rectangle: every point is inside `[x, x+w]` by `[y, y+h]`.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.4 and 4.5.
 */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const point = (x: number, y: number): string => `${Math.round(x)} ${Math.round(y)}`;

// A wide flat-top hexagon: the left and right sides come to a point at mid-height, `cut` in.
export function hexCardPath(box: Box, cut: number): string {
  const { x, y, w, h } = box;
  const mid = y + h / 2;
  return (
    `M${point(x + cut, y)} L${point(x + w - cut, y)} L${point(x + w, mid)} ` +
    `L${point(x + w - cut, y + h)} L${point(x + cut, y + h)} L${point(x, mid)} Z`
  );
}

// A rectangle with its four corners cut by `cut`: the hex-cornered frame.
export function chamferedPath(box: Box, cut: number): string {
  const { x, y, w, h } = box;
  return (
    `M${point(x + cut, y)} L${point(x + w - cut, y)} L${point(x + w, y + cut)} ` +
    `L${point(x + w, y + h - cut)} L${point(x + w - cut, y + h)} L${point(x + cut, y + h)} ` +
    `L${point(x, y + h - cut)} L${point(x, y + cut)} Z`
  );
}

// A regular flat-top hexagon of radius `r` around a centre: the cell a role glyph sits in.
export function smallHexPath(cx: number, cy: number, r: number): string {
  const dy = (r * Math.sqrt(3)) / 2;
  return (
    `M${point(cx + r, cy)} L${point(cx + r / 2, cy + dy)} L${point(cx - r / 2, cy + dy)} ` +
    `L${point(cx - r, cy)} L${point(cx - r / 2, cy - dy)} L${point(cx + r / 2, cy - dy)} Z`
  );
}
