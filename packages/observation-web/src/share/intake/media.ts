/**
 * What a shared item is called and which Hive route can take it.
 *
 * Another app declares a media type for what it shares, and it is often not the one the Hive's audio
 * route lists (`audio/x-m4a`, `audio/mp3`, `audio/x-wav`). This file maps the common spellings onto
 * the formats `POST /v1/chat/audio` takes (read off the generated document by `AudioMediaType`), and
 * writes the one-line description the confirmation card shows. It is pure.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Used by `send_item.ts` and `share_card.tsx`.
 *
 * Key invariants:
 *   - A type the Hive does not take maps to `null`, never to a guess: a clip sent under the wrong
 *     format would be transcribed as noise.
 *   - Parameters (`; codecs=opus`) and case are ignored when matching.
 *
 * See Also:
 *   - src/landing_board/client/platform/recorder.ts for the formats the recorder produces.
 */
import type { AudioMediaType, ImageMediaType, SharedItem } from '../../landing_board/client';

const AUDIO_TYPES: Readonly<Record<string, AudioMediaType>> = {
  'audio/webm': 'audio/webm',
  'audio/ogg': 'audio/ogg',
  'audio/opus': 'audio/ogg',
  'audio/mp4': 'audio/mp4',
  'audio/m4a': 'audio/mp4',
  'audio/x-m4a': 'audio/mp4',
  'audio/mpeg': 'audio/mpeg',
  'audio/mp3': 'audio/mpeg',
  'audio/wav': 'audio/wav',
  'audio/x-wav': 'audio/wav',
  'audio/wave': 'audio/wav',
};

export function audioTypeFor(mediaType: string): AudioMediaType | null {
  const bare = (mediaType.split(';')[0] ?? '').trim().toLowerCase();
  return AUDIO_TYPES[bare] ?? null;
}

const IMAGE_TYPES: Readonly<Record<string, ImageMediaType>> = {
  'image/jpeg': 'image/jpeg',
  'image/jpg': 'image/jpeg',
  'image/png': 'image/png',
  'image/webp': 'image/webp',
};

export function imageTypeFor(mediaType: string): ImageMediaType | null {
  return IMAGE_TYPES[(mediaType.split(';')[0] ?? '').trim().toLowerCase()] ?? null;
}

function sizeWords(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  return bytes < 1024 * 1024
    ? `${Math.round(bytes / 1024)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The longest stretch of shared text drawn on the card; the whole text is still what is sent.
export const PREVIEW_CHARS = 600;

// One line saying what will be sent, in the words of the README.
export function describeItem(item: SharedItem): string {
  switch (item.kind) {
    case 'text':
      return `A message of ${item.text.length} characters`;
    case 'image':
      return `An image (${item.mediaType}, ${sizeWords(item.bytes.length)}), kept as Nectar`;
    case 'audio':
      return `An audio clip (${item.mediaType}, ${sizeWords(item.bytes.length)}), transcribed on the Hive Stand`;
    case 'refused':
      return 'Something the phone could not take';
  }
}
