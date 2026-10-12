/**
 * `useHold(recorder, onClip)`: record while a button is held, and hand over the clip on release.
 *
 * `begin` opens the microphone and starts the countdown; `end` (release, or the cap) stops the
 * recording and gives the clip to `onClip`. A hold too short to hold any speech is dropped with a
 * hint instead of being handed over. The microphone is released on every path: handed over, too
 * short, failed, cancelled or unmounted.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/voice/`. Used by `use_voice.ts`; records through the
 *   `AudioRecorder` it is given and never touches `MediaRecorder`.
 *
 * Key invariants:
 *   - A release that arrives while the microphone is still opening ends the recording as soon as
 *     it has opened, so a quick tap never leaves it running.
 *   - The cap is `DEFAULT_MAX_CLIP_SECONDS`: the Entrance's `max_clip_seconds` is not in the
 *     published document, so the Hive's default stands in for it.
 *   - A device with no recorder reports `available: false` and does nothing.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.4.
 *   - src/landing_board/client/platform/recorder.ts for the real recorder.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { HttpClient } from '../../../landing_board/client';
import { useCountdown } from './use_countdown';

// The Hive's default `[entrance.voice] max_clip_seconds`; the document does not publish the value.
export const DEFAULT_MAX_CLIP_SECONDS = 60;
// Under this a hold caught no speech: a tap, not a sentence.
export const MIN_HOLD_SECONDS = 0.4;

export type VoicePhase = 'idle' | 'starting' | 'recording' | 'sending';

type Recorder = NonNullable<HttpClient['recorder']>;
type Recording = Awaited<ReturnType<Recorder['start']>>;
type Clip = Awaited<ReturnType<Recording['stop']>>;

export interface Hold {
  available: boolean;
  phase: VoicePhase;
  secondsLeft: number;
  hint: string | null;
  error: unknown;
  begin(): void;
  end(): void;
}

export function useHold(recorder: Recorder | null, onClip: (clip: Clip) => Promise<void>): Hold {
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const live = useRef({ phase: 'idle' as VoicePhase, released: false });
  const recording = useRef<Recording | null>(null);
  // The countdown ends the hold, and ending it stops the countdown, so each reaches the other
  // through a ref.
  const ending = useRef<() => void>(() => {});
  const countdown = useCountdown(DEFAULT_MAX_CLIP_SECONDS, () => ending.current());

  const go = useCallback((next: VoicePhase) => {
    live.current.phase = next;
    setPhase(next);
  }, []);

  const end = useCallback(async () => {
    live.current.released = true;
    const active = recording.current;
    // Still opening (or idle): `begin` checks `released` once the microphone is open.
    if (!active) return;
    recording.current = null;
    countdown.stop();
    go('sending');
    try {
      const clip = await active.stop();
      if (clip.durationS < MIN_HOLD_SECONDS) setHint('Hold the button while you speak.');
      else await onClip(clip);
    } catch (failure) {
      setError(failure);
    } finally {
      go('idle');
    }
  }, [onClip, go, countdown]);

  useEffect(() => {
    ending.current = () => void end();
  }, [end]);

  const begin = useCallback(async () => {
    if (!recorder || live.current.phase !== 'idle') return;
    live.current.released = false;
    setError(null);
    setHint(null);
    go('starting');
    try {
      // Latency: the browser's permission prompt the first time; a refusal rejects.
      recording.current = await recorder.start();
    } catch (failure) {
      setError(failure);
      go('idle');
      return;
    }
    // Let go before the microphone opened: stop at once instead of recording unheld.
    if (live.current.released) return void end();
    go('recording');
    countdown.start();
  }, [recorder, go, end, countdown]);

  // Leaving the view mid-hold drops the audio and turns the microphone off.
  useEffect(
    () => () => {
      recording.current?.cancel();
      recording.current = null;
    },
    [],
  );

  return {
    available: recorder !== null,
    phase,
    secondsLeft: countdown.left,
    hint,
    error,
    begin: () => void begin(),
    end: () => void end(),
  };
}
