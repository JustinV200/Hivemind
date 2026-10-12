/**
 * Measure a shared audio clip's length in seconds.
 *
 * `POST /v1/chat/audio` wants the length of a compressed clip (a WAV states its own). The recorder
 * knows how long the human held the button; a clip shared from another app arrives with no length.
 * The WebView can read it: an `<audio>` element loads the bytes, and its metadata names the
 * duration. Some containers (WebM from a live recorder) report `Infinity` until the end is
 * sought, so the element is nudged to the end once to make it compute the real value.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Called by `send_item.ts` for shared audio only; a test
 *   passes its own measure, as jsdom has no media decoder.
 *
 * Key invariants:
 *   - The object URL is revoked and the element released on every path.
 *   - A clip whose length cannot be read in `LIMIT_MS` is an error; a length is never guessed.
 *
 * See Also:
 *   - src/landing_board/client/http.ts `postAudio` for how the length is used.
 */
import type { Bytes } from '../../landing_board/client';

const LIMIT_MS = 8000;

export function measureClip(bytes: Bytes, mediaType: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([bytes], { type: mediaType }));
    const audio = new Audio();
    const finish = (outcome: () => void): void => {
      window.clearTimeout(timer);
      audio.removeAttribute('src');
      URL.revokeObjectURL(url);
      outcome();
    };
    const timer = window.setTimeout(
      () => finish(() => reject(new Error("The clip's length could not be read."))),
      LIMIT_MS,
    );
    audio.preload = 'metadata';
    audio.onerror = () => finish(() => reject(new Error('The clip could not be played.')));
    audio.onloadedmetadata = () => {
      if (Number.isFinite(audio.duration)) return finish(() => resolve(audio.duration));
      // Seeking past the end forces the element to work the real duration out.
      audio.ontimeupdate = () => {
        if (Number.isFinite(audio.duration)) finish(() => resolve(audio.duration));
      };
      audio.currentTime = 1e101;
    };
    audio.src = url;
  });
}
