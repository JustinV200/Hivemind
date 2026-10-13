/**
 * Tests for the real recorder: format choice, the clip it returns, and releasing the microphone.
 *
 * jsdom has no `MediaRecorder` or microphone, so both are stubbed as globals for each test. The
 * rules under test: a browser with no recorder, or none of the formats the Hive takes, has no
 * recorder at all; the clip's media type has its codec parameters removed; the duration is wall
 * time between start and stop; and the microphone's tracks are stopped on stop, cancel and failure.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/landing_board/client/platform/recorder.ts.
 *
 * Key invariants:
 *   - Every global stubbed here is restored after each test.
 *   - The fake `MediaRecorder` flushes one data chunk on stop, as the browser's does.
 *
 * See Also:
 *   - tests/landing_board/client/http_audio.test.ts for sending the clip.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { realRecorder } from '../../../src/landing_board/client/platform/recorder';

class FakeMediaRecorder {
  static supported = ['audio/webm;codecs=opus', 'audio/webm'];
  static isTypeSupported = (mime: string) => FakeMediaRecorder.supported.includes(mime);
  state: 'inactive' | 'recording' = 'inactive';
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: ((event: Event) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(
    readonly stream: unknown,
    options?: { mimeType?: string },
  ) {
    this.mimeType = options?.mimeType ?? '';
  }

  start(): void {
    this.state = 'recording';
  }

  stop(): void {
    this.state = 'inactive';
    this.ondataavailable?.({
      data: new Blob([new Uint8Array([9, 8, 7])], { type: this.mimeType }),
    });
    this.onstop?.(new Event('stop'));
  }
}

let trackStops = 0;
const stream = {
  getTracks: () => [{ stop: () => (trackStops += 1) }, { stop: () => (trackStops += 1) }],
};

function stubMicrophone(getUserMedia: () => Promise<unknown>): void {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
}

beforeEach(() => {
  trackStops = 0;
  FakeMediaRecorder.supported = ['audio/webm;codecs=opus', 'audio/webm'];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  Reflect.deleteProperty(navigator, 'mediaDevices');
});

describe('availability', () => {
  it('is null without MediaRecorder or a microphone API', () => {
    expect(realRecorder()).toBeNull();
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    expect(realRecorder()).toBeNull();
  });

  it('is null when the browser records none of the formats the Hive takes', () => {
    stubMicrophone(() => Promise.resolve(stream));
    FakeMediaRecorder.supported = ['video/x-matroska'];
    expect(realRecorder()).toBeNull();
  });

  it('exists when a microphone and one served format exist', () => {
    stubMicrophone(() => Promise.resolve(stream));
    expect(realRecorder()).not.toBeNull();
  });
});

describe('recording', () => {
  it('returns the bytes under the bare type with the wall-time length, and frees the microphone', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(1_000_000);
    stubMicrophone(() => Promise.resolve(stream));
    const recording = await realRecorder()!.start();
    vi.setSystemTime(1_003_500);
    const clip = await recording.stop();

    expect(Array.from(clip.bytes)).toEqual([9, 8, 7]);
    // `audio/webm;codecs=opus` is sent as `audio/webm`, the type the document lists.
    expect(clip.mediaType).toBe('audio/webm');
    expect(clip.durationS).toBe(3.5);
    expect(trackStops).toBe(2);
  });

  it('prefers Opus in WebM and falls back to the next format', async () => {
    stubMicrophone(() => Promise.resolve(stream));
    FakeMediaRecorder.supported = ['audio/mp4'];
    const recording = await realRecorder()!.start();
    expect((await recording.stop()).mediaType).toBe('audio/mp4');
  });

  it('drops the audio and frees the microphone on cancel', async () => {
    stubMicrophone(() => Promise.resolve(stream));
    const recording = await realRecorder()!.start();
    recording.cancel();
    expect(trackStops).toBe(2);
  });

  it('refuses with the microphone code when the human says no', async () => {
    stubMicrophone(() => Promise.reject(new DOMException('denied', 'NotAllowedError')));
    const failure = await realRecorder()!
      .start()
      .catch((e: unknown) => e);
    expect((failure as { code: string }).code).toBe('hivemind.client.microphone');
  });
});
