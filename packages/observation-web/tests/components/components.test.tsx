/**
 * Tests for the shared components: tier badge, mask badge, status dot, gauge, error notice.
 *
 * The badges' meaning must be in their words and their shape class, never in colour alone: the
 * tests check every label, that tier and mask carry different shape classes, that a Queen-forced
 * mask shows its expiry and the others do not, that the gauge exposes a meter with the right
 * numbers and tone, that an error shows its code, its table sentence and its next step, that a
 * time stamp's relative words follow the clock, and that a view that throws is caught in place.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/components/.
 *
 * Key invariants:
 *   - Assertions are on text and ARIA, which is what a reader gets, not on styling.
 *
 * See Also:
 *   - docs/observation/design.md section 4.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  ErrorNotice,
  Gauge,
  IdToken,
  MaskBadge,
  StatusDot,
  TierBadge,
  ViewBoundary,
} from '../../src/components';
import { relativeText } from '../../src/components/time';
import { CLIENT_CODES, clientError } from '../../src/landing_board/client/errors';

describe('TierBadge', () => {
  it.each([
    ['MEADOW', 'MEADOW', 'badge-tier--meadow'],
    ['PROPOLIS', 'PROPOLIS', 'badge-tier--propolis'],
    ['NIGHT_VEIL', 'NIGHT VEIL', 'badge-tier--night-veil'],
  ] as const)('shows %s as the word %s in a pill', (level, label, modifier) => {
    render(<TierBadge level={level} />);
    const badge = screen.getByText(label);
    expect(badge.className).toContain('badge-tier');
    expect(badge.className).toContain(modifier);
    expect(badge.className).not.toContain('badge-mask');
  });
});

describe('MaskBadge', () => {
  it.each([
    ['OFF', 'OFF', 'badge-mask--off'],
    ['WARDEN', 'WARDEN', 'badge-mask--warden'],
    ['QUEEN_FORCED', 'QUEEN FORCED', 'badge-mask--queen-forced'],
  ] as const)('shows %s as the word %s in a square tag', (state, label, modifier) => {
    render(<MaskBadge state={state} />);
    const badge = screen.getByText(label);
    expect(badge.className).toContain('badge-mask');
    expect(badge.className).toContain(modifier);
    expect(badge.className).not.toContain('badge-tier');
  });

  it('shows the expiry of a Queen-forced mask and of no other', () => {
    const until = '2026-01-01T09:05:00';
    const { container, rerender } = render(<MaskBadge state="QUEEN_FORCED" until={until} />);
    expect(screen.getByText('until 09:05')).toBeTruthy();
    expect(container.querySelector('time')?.getAttribute('datetime')).toBe(until);
    rerender(<MaskBadge state="WARDEN" until={until} />);
    expect(screen.queryByText(/until/)).toBeNull();
  });
});

describe('StatusDot', () => {
  it('shows the word and hides the dot from assistive technology', () => {
    const { container } = render(<StatusDot word="reconnecting" />);
    expect(screen.getByText('reconnecting')).toBeTruthy();
    const dot = container.querySelector('.status-dot');
    expect(dot?.getAttribute('aria-hidden')).toBe('true');
    expect(dot?.className).toContain('status-dot--warn');
  });

  it.each([
    ['live', 'ok'],
    ['offline', 'alarm'],
    ['idle', 'idle'],
    ['failed', 'alarm'],
  ] as const)('draws %s in the %s tone', (word, tone) => {
    const { container } = render(<StatusDot word={word} />);
    expect(container.querySelector('.status-dot')?.className).toContain(`status-dot--${tone}`);
  });
});

describe('Gauge', () => {
  it('is a meter with the numbers beside it', () => {
    render(<Gauge used={41200} budget={128000} label="Context" />);
    const meter = screen.getByRole('meter', { name: 'Context' });
    expect(meter.getAttribute('aria-valuenow')).toBe('41200');
    expect(meter.getAttribute('aria-valuemin')).toBe('0');
    expect(meter.getAttribute('aria-valuemax')).toBe('128000');
    expect(screen.getByText('41,200 / 128,000 (32%)')).toBeTruthy();
  });

  it.each([
    [69, 'ok'],
    [70, 'warn'],
    [90, 'warn'],
    [91, 'alarm'],
  ])('uses the %s percent tone %s', (percent, tone) => {
    const { container } = render(<Gauge used={percent} budget={100} label="x" />);
    expect(container.querySelector('.gauge-fill')?.className).toContain(`gauge-fill--${tone}`);
  });

  it('clamps the bar to the track but shows the true overrun', () => {
    const { container } = render(<Gauge used={150} budget={100} label="x" />);
    expect((container.querySelector('.gauge-fill') as HTMLElement).style.width).toBe('100%');
    expect(screen.getByText('150 / 100 (150%)')).toBeTruthy();
  });

  it('never shows NaN for a zero budget', () => {
    render(<Gauge used={5} budget={0} label="x" />);
    expect(screen.getByText('5 / 0 (0%)')).toBeTruthy();
  });
});

describe('IdToken', () => {
  // A real id is `<kind>_<26-char ULID>` whose head is the timestamp every id of the day shares.
  it.each([
    ['cell_01HZK4R0P3L1NT8U4WR1B2C3D4', 'cell_…2C3D4'],
    ['task_01HZK4R0P3L1NT8U4WR1B2C3D4', 'task_…2C3D4'],
    ['01HZK4R0P3L1NT8U4WR1B2C3D4', '…2C3D4'],
    ['worker_12', 'worker_12'],
  ])('shows %s as %s with the whole id in its name', (id, shown) => {
    render(<IdToken id={id} />);
    const token = screen.getByText(shown);
    expect(token.getAttribute('title')).toBe(id);
    expect(token.getAttribute('aria-label')).toBe(id);
  });
});

describe('ErrorNotice', () => {
  it('shows the table sentence and what to do, keeping the raw code off the screen', () => {
    // Regression: a stand-key refusal on device approval showed its raw code above the sentence.
    render(<ErrorNotice error={clientError(CLIENT_CODES.standKeyRequired, 'x')} />);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).not.toContain(CLIENT_CODES.standKeyRequired);
    expect(alert.dataset.code).toBe(CLIENT_CODES.standKeyRequired);
    expect(screen.getByText("Only the Hive Stand's own browser tab can do this.")).toBeTruthy();
  });

  it('draws the next step as a link when the table says where to go', () => {
    render(<ErrorNotice error={clientError('hivemind.entrance.cell_not_found', 'x')} />);
    const link = screen.getByRole('link', { name: 'Go back to the Fleet and pick another.' });
    expect(link.getAttribute('href')).toBe('#/fleet');
  });

  it('falls back to the Entrance detail for an unknown code, with no advice', () => {
    render(<ErrorNotice error={clientError('hivemind.mystery', 'It broke.')} />);
    expect(screen.getByText('It broke.')).toBeTruthy();
    expect(screen.getByRole('alert').querySelectorAll('p')).toHaveLength(1);
  });

  it('says the Hive could not be reached for a non-Landing-Board failure', () => {
    render(<ErrorNotice error={new Error('boom')} />);
    expect(screen.getByText('The Hive could not be reached.')).toBeTruthy();
  });
});

describe('TimeStamp words', () => {
  const now = Date.parse('2026-01-08T12:00:00Z');
  it.each([
    ['2026-01-08T11:59:40Z', 'just now'],
    ['2026-01-08T11:55:00Z', '5 min ago'],
    ['2026-01-08T12:04:00Z', 'in 4 min'],
    ['2026-01-08T09:30:00Z', '2 h 30 min ago'],
    // A gap that rounds up to a whole hour or day carries, never "60 min" or "23 h 60 min".
    ['2026-01-08T11:00:20Z', '1 h ago'],
    ['2026-01-07T12:00:20Z', '1 d ago'],
    ['2026-01-06T12:00:00Z', '2 d ago'],
    ['2025-12-01T00:00:00Z', /^2025-1[12]-\d\d$/],
    ['not a date', 'not a date'],
  ])('reads %s as %s', (iso, words) => {
    expect(relativeText(iso, now)).toMatch(words);
  });
});

describe('ViewBoundary', () => {
  it('shows the view-failed notice in place of a view that throws, and nothing else', () => {
    const Broken = (): never => {
      throw new Error('malformed answer');
    };
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <ViewBoundary>
        <Broken />
      </ViewBoundary>,
    );
    expect(screen.getByRole('alert').dataset.code).toBe('hivemind.client.view_failed');
    expect(screen.getByText(/could not be drawn/)).toBeTruthy();
    expect(screen.queryByText('malformed answer')).toBeNull();
    quiet.mockRestore();
  });
});
