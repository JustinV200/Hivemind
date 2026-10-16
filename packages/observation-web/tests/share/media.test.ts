/**
 * Tests for the pure helpers of the share intake: media types, descriptions and the waiting reducer.
 *
 * Another app's media type must map onto one the audio route takes, or be refused rather than
 * guessed; the card's one-line description says what will be sent; and the waiting set follows the
 * push stream's notices once per event id.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/share/intake/media.ts and src/share/waiting/waiting.ts.
 *
 * Key invariants:
 *   - Unknown audio types map to null, never to a nearest guess.
 *   - A repeated event id changes nothing.
 *
 * See Also:
 *   - docs/entrance/landing-board.md sections 8.4 and 9.
 */
import { describe, expect, it } from 'vitest';
import { audioTypeFor, describeItem, imageTypeFor } from '../../src/share/intake/media';
import { applyNotice, NOTHING_WAITING, reseeded, seeded } from '../../src/share/waiting/waiting';
import type { PushNotice } from '../../src/share/waiting/waiting';

describe('audioTypeFor', () => {
  it.each([
    ['audio/mp4', 'audio/mp4'],
    ['audio/x-m4a', 'audio/mp4'],
    ['audio/mp3', 'audio/mpeg'],
    ['audio/x-wav', 'audio/wav'],
    ['AUDIO/WEBM; codecs=opus', 'audio/webm'],
  ])('maps %s to %s', (given, taken) => {
    expect(audioTypeFor(given)).toBe(taken);
  });

  it.each(['audio/amr', 'audio/aac', 'video/mp4', ''])('refuses %s', (given) => {
    expect(audioTypeFor(given)).toBeNull();
  });
});

describe('imageTypeFor', () => {
  it('maps the spellings of the three formats the Hive takes, and nothing else', () => {
    expect(imageTypeFor('image/jpg')).toBe('image/jpeg');
    expect(imageTypeFor('IMAGE/PNG; q=1')).toBe('image/png');
    expect(imageTypeFor('image/webp')).toBe('image/webp');
    expect(imageTypeFor('image/heic')).toBeNull();
    expect(imageTypeFor('application/pdf')).toBeNull();
  });
});

describe('describeItem', () => {
  it('says what will be sent', () => {
    expect(describeItem({ kind: 'text', text: 'hello' })).toBe('A message of 5 characters');
    const bytes = new Uint8Array(2048);
    expect(describeItem({ kind: 'image', bytes, mediaType: 'image/png' })).toBe(
      'An image (image/png, 2 KB), kept as Nectar',
    );
    expect(describeItem({ kind: 'audio', bytes, mediaType: 'audio/mp4' })).toContain(
      'transcribed on the Hive Stand',
    );
    expect(describeItem({ kind: 'refused', reason: 'too_large' })).toMatch(/could not take/);
  });
});

const notice = (kind: PushNotice['kind'], ref: string, id: string): PushNotice => ({
  kind,
  ref,
  event_id: id,
  created_at: '2026-01-01T00:00:00Z',
});

describe('applyNotice', () => {
  it('adds a waiting question and removes it when withdrawn', () => {
    const waiting = applyNotice(NOTHING_WAITING, notice('question_waiting', 'q1', 'e1'));
    expect([...waiting.refs]).toEqual(['q1']);
    expect(applyNotice(waiting, notice('withdrawn', 'q1', 'e2')).refs.size).toBe(0);
  });

  it('counts questions only, and processes an event id once', () => {
    let state = applyNotice(NOTHING_WAITING, notice('alarm_waiting', 'a1', 'e1'));
    expect(state.refs.size).toBe(0);
    state = applyNotice(state, notice('question_waiting', 'q1', 'e2'));
    const again = applyNotice(state, notice('withdrawn', 'q1', 'e2'));
    expect(again).toBe(state);
  });

  it('starts from the questions already waiting', () => {
    const state = applyNotice(seeded(['q1']), notice('withdrawn', 'q1', 'e1'));
    expect(state.refs.size).toBe(0);
  });
});

describe('reseeded', () => {
  it('replaces the set with the inbox, keeping what it heard since the read started', () => {
    // q1 was answered elsewhere while the stream was down: the inbox no longer lists it.
    const stale = seeded(['q1']);
    const since = [notice('question_waiting', 'q3', 'e1'), notice('withdrawn', 'q2', 'e2')];
    const state = reseeded(stale, ['q2', 'q4'], since);
    expect([...state.refs].sort()).toEqual(['q3', 'q4']);
  });

  it('keeps the event ids seen, so an old notice is still processed once', () => {
    const before = applyNotice(NOTHING_WAITING, notice('question_waiting', 'q1', 'e1'));
    const state = reseeded(before, [], []);
    expect(state.refs.size).toBe(0);
    expect(applyNotice(state, notice('question_waiting', 'q1', 'e1'))).toBe(state);
  });
});
