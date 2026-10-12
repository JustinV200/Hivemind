/**
 * The real `AudioRecorder`: the browser's `MediaRecorder` over the microphone.
 *
 * Push-to-talk records while the button is held. `start` asks for the microphone (the browser
 * prompts once), picks the first container format this browser can record from a short list, and
 * collects the encoded chunks. `stop` joins them into one clip and hands back its format and how
 * long it ran; `cancel` drops the audio. The microphone is released either way, so the browser's
 * recording indicator never outlives the hold.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Called by `real_browser.ts`,
 *   which offers it as `Browser.recorder`; used by the Chat view through `HttpClient.recorder`.
 *
 * Key invariants:
 *   - Only formats `POST /v1/chat/audio` takes are offered (`AudioMediaType`); a browser that can
 *     record none of them has no recorder at all, so the button is hidden rather than failing.
 *   - The media type has its codec parameters removed (`audio/webm;codecs=opus` is `audio/webm`),
 *     because the document lists bare types.
 *   - Globals are read when the function runs, not at import.
 *
 * See Also:
 *   - src/landing_board/client/browser.ts for the interface.
 *   - docs/entrance/landing-board.md section 8.4.
 */
import type { ActiveRecording, AudioMediaType, AudioRecorder, RecordedClip } from '../browser';
import { CLIENT_CODES, clientError } from '../errors';

// In order of preference: Chrome and Firefox record WebM or Ogg Opus, Safari records MP4.
const CANDIDATES: readonly string[] = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/ogg',
  'audio/mp4',
];

const SERVED: readonly string[] = [
  'audio/webm',
  'audio/ogg',
  'audio/mp4',
  'audio/mpeg',
  'audio/wav',
];

// The bare type of a MIME string, or null when the Hive does not take it.
function bareType(mime: string): AudioMediaType | null {
  const bare = (mime.split(';')[0] ?? '').trim().toLowerCase();
  return SERVED.includes(bare) ? (bare as AudioMediaType) : null;
}

function pickMime(): string | null {
  return CANDIDATES.find((mime) => MediaRecorder.isTypeSupported(mime)) ?? null;
}

function release(stream: MediaStream): void {
  // Stopping every track turns the browser's microphone indicator off.
  for (const track of stream.getTracks()) track.stop();
}

function finish(
  recorder: MediaRecorder,
  stream: MediaStream,
  chunks: Blob[],
  startedAt: number,
): Promise<RecordedClip> {
  return new Promise((resolve, reject) => {
    recorder.onerror = () => {
      release(stream);
      reject(clientError(CLIENT_CODES.microphone, 'The recorder failed.'));
    };
    recorder.onstop = () => {
      release(stream);
      const blob = new Blob(chunks, { type: recorder.mimeType });
      const mediaType = bareType(recorder.mimeType);
      if (!mediaType)
        return reject(new Error('The recorder made a format the Hive does not take.'));
      // Reading the blob is local and fast; the duration is wall time between start and stop.
      void blob.arrayBuffer().then((buffer) =>
        resolve({
          bytes: new Uint8Array(buffer),
          mediaType,
          durationS: (Date.now() - startedAt) / 1000,
        }),
      );
    };
    // A recorder already inactive (the track ended) has nothing more to flush.
    if (recorder.state === 'inactive') recorder.onstop?.(new Event('stop'));
    else recorder.stop();
  });
}

async function start(): Promise<ActiveRecording> {
  // Latency: a permission prompt the first time, so as long as the human takes; a refusal rejects.
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => {
    // Whatever the browser said, the human's next step is the same, so it is one local refusal.
    throw clientError(CLIENT_CODES.microphone, 'The microphone could not be used.');
  });
  const mime = pickMime();
  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  } catch {
    // The microphone is open but unusable here; turn it off before reporting.
    release(stream);
    throw clientError(CLIENT_CODES.microphone, 'The recorder could not start.');
  }
  const chunks: Blob[] = [];
  recorder.ondataavailable = (event) => {
    // An empty slice carries nothing; keeping it would only grow the array.
    if (event.data.size > 0) chunks.push(event.data);
  };
  const startedAt = Date.now();
  recorder.start();
  return {
    stop: () => finish(recorder, stream, chunks, startedAt),
    cancel: () => {
      recorder.onstop = null;
      if (recorder.state !== 'inactive') recorder.stop();
      release(stream);
    },
  };
}

// Null when this browser has no microphone API or can record none of the formats the Hive takes.
export function realRecorder(): AudioRecorder | null {
  if (typeof MediaRecorder === 'undefined') return null;
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) return null;
  return pickMime() ? { start } : null;
}
