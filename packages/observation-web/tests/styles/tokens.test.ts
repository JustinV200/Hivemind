/**
 * Tests for the design tokens: contrast, theme parity and "no hex outside tokens.css".
 *
 * Design section 7 says a token change must re-run a contrast test that reads the tokens and
 * fails below the published numbers. This is that test: it parses `tokens.css`, computes WCAG
 * contrast for every pairing the design lists in both themes, and checks that the dark theme
 * defined under the media query is the same as the one under `data-theme="dark"`. It also
 * checks that no other stylesheet carries a colour literal, since a hex value in a component is
 * a bug by the same document.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Reads src/styles/*.css as text.
 *
 * Key invariants:
 *   - Thresholds are the design's: 4.5:1 for text and badges, 3:1 for control borders.
 *
 * See Also:
 *   - docs/observation/design.md sections 3 and 7.
 */
import { describe, expect, it } from 'vitest';
import base from '../../src/styles/base.css?raw';
import components from '../../src/styles/components.css?raw';
import shell from '../../src/styles/shell.css?raw';
import tokens from '../../src/styles/tokens.css?raw';

type Palette = Record<string, string>;

// The text of the block opened by `marker`, up to its matching closing brace.
function block(css: string, marker: string): string {
  const open = css.indexOf('{', css.indexOf(marker));
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth += 1;
    if (css[i] === '}' && --depth === 0) return css.slice(open + 1, i);
  }
  throw new Error(`no block for ${marker}`);
}

function colours(text: string): Palette {
  const found: Palette = {};
  for (const match of text.matchAll(/--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b/g)) {
    found[match[1]!] = match[2]!.toUpperCase();
  }
  return found;
}

const light = colours(block(tokens, '\n:root {'));
const darkMedia = colours(
  block(block(tokens, '@media (prefers-color-scheme: dark)'), ":root:not([data-theme='light'])"),
);
const darkAttribute = colours(block(tokens, ":root[data-theme='dark']"));

function luminance(hex: string): number {
  const channel = (offset: number): number => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const THEMES: [string, Palette][] = [
  ['light', light],
  ['dark', darkMedia],
];
const SURFACES = ['bg', 'surface', 'surface-2'];
const TEXTS = ['text', 'text-muted', 'accent'];
const STATUSES = ['ok', 'warn', 'alarm', 'idle'];
const BADGES = [
  'tier-meadow',
  'tier-propolis',
  'tier-night-veil',
  'mask-off',
  'mask-warden',
  'mask-queen-forced',
];

describe.each(THEMES)('%s theme', (_, palette) => {
  it.each(TEXTS.flatMap((t) => SURFACES.map((s) => [t, s])))(
    '%s on %s is at least 4.5:1',
    (t, s) => {
      expect(contrast(palette[t!]!, palette[s!]!)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(STATUSES.flatMap((t) => ['surface', 'surface-2'].map((s) => [t, s])))(
    '%s on %s is at least 4.5:1',
    (t, s) => {
      expect(contrast(palette[t!]!, palette[s!]!)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it('puts on-accent on accent at 4.5:1 or better', () => {
    expect(contrast(palette['on-accent']!, palette.accent!)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(BADGES)('badge %s text on its fill is at least 4.5:1', (badge) => {
    expect(contrast(palette[`${badge}-fg`]!, palette[`${badge}-bg`]!)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps control borders at 3:1 on the surface', () => {
    expect(contrast(palette.border!, palette.surface!)).toBeGreaterThanOrEqual(3);
  });
});

describe('published numbers', () => {
  // A transcription check: the design's own ratios must come out of the tokens as written.
  it('matches design 3.1 for text on the surface', () => {
    expect(contrast(light.text!, light.surface!)).toBeCloseTo(17.1, 0);
    expect(contrast(darkMedia.text!, darkMedia.surface!)).toBeCloseTo(14.7, 0);
    expect(contrast(light['text-muted']!, light.surface!)).toBeCloseTo(7.4, 0);
  });

  it('matches design 3.3 for the Night Veil badge, the heaviest in either theme', () => {
    expect(contrast(light['tier-night-veil-fg']!, light['tier-night-veil-bg']!)).toBeCloseTo(
      15.7,
      0,
    );
    expect(
      contrast(darkMedia['tier-night-veil-fg']!, darkMedia['tier-night-veil-bg']!),
    ).toBeCloseTo(12.4, 0);
  });
});

describe('theme parity and hygiene', () => {
  it('defines the dark theme identically under the media query and the attribute', () => {
    expect(darkAttribute).toEqual(darkMedia);
    expect(Object.keys(darkMedia).sort()).toEqual(Object.keys(light).sort());
  });

  it('keeps colour literals out of every other stylesheet', () => {
    for (const css of [base, shell, components]) {
      expect(css.replace(/\/\*[\s\S]*?\*\//g, '')).not.toMatch(
        /#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(/,
      );
    }
  });
});
