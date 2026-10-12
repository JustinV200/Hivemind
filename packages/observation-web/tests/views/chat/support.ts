/**
 * Test builders and a harness for the Chat view: lines, inbox items, goals, a fake recorder, a mount.
 *
 * The generated types have many required members that a test about one answer does not care about.
 * These builders fill them with plain defaults and let a test override only what it is about, so
 * each test reads as its point and still type-checks against the generated schema. `openChat` makes
 * the store (the app's table plus the Chat slice, which `src/app.tsx` does not hold until the
 * orchestrator registers it), a signed-in client on the fake browser, and routes for the two reads.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by every test under tests/views/chat/.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *   - The fake recorder holds no real audio: a clip is whatever bytes the test gives it.
 *
 * See Also:
 *   - src/landing_board/types.ts, generated.
 *   - tests/support/fake_browser.ts for the fake the harness uses.
 */
import { APP_SLICES } from '../../../src/app';
import { createLandingBoard } from '../../../src/landing_board/client';
import type {
  ActiveRecording,
  AudioRecorder,
  RecordedClip,
} from '../../../src/landing_board/client/browser';
import type { components } from '../../../src/landing_board/types';
import { createHiveStore } from '../../../src/store';
import type { Hive } from '../../../src/store';
import { chatSlice } from '../../../src/views/chat/slice';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];
export type ChatLine = Schemas['ChatLine'];

export function chatLine(over: Partial<ChatLine> = {}): ChatLine {
  return {
    at: '2026-01-01T00:00:00Z',
    author: 'queen',
    id: 'chat_1',
    kind: 'reply',
    ref: null,
    seq: 1,
    task_id: null,
    text: 'Hello from the Queen',
    ...over,
  };
}

// A run of lines numbered from `from`, each with its own id and a body saying its number.
export function chatLines(count: number, from = 1): ChatLine[] {
  return Array.from({ length: count }, (_, i) =>
    chatLine({ id: `chat_${from + i}`, seq: from + i, text: `line ${from + i}` }),
  );
}

export function chatPage(entries: ChatLine[]): Schemas['ChatPage'] {
  return { entries, newest_seq: entries.length ? entries[entries.length - 1]!.seq : null };
}

export function chatFrame(over: Partial<ChatLine> = {}): Schemas['ChatFrame'] {
  return { type: 'chat', entry: chatLine(over) };
}

export function questionView(over: Partial<Schemas['QuestionView']> = {}): Schemas['QuestionView'] {
  return {
    asked_at: '2026-01-01T00:00:05Z',
    clearance: 'C1',
    id: 'msg_01M3AN9216QQX5JVNQHHWBFJR9',
    options: [],
    task_id: 'task_1',
    text: 'Which season?',
    ...over,
  };
}

export function alarmView(over: Partial<Schemas['AlarmView']> = {}): Schemas['AlarmView'] {
  return {
    detail: 'The Cell stopped answering',
    id: 'alarm_1',
    kind: 'CELL_UNREACHABLE',
    raised_at: '2026-01-01T00:00:06Z',
    severity: 'CRITICAL',
    task_id: 'task_2',
    ...over,
  };
}

export function inboxView(over: Partial<Schemas['InboxView']> = {}): Schemas['InboxView'] {
  return { alarms: [], questions: [], ...over };
}

export function goalView(over: Partial<Schemas['GoalView']> = {}): Schemas['GoalView'] {
  return {
    budget_usd: null,
    clearance: 'C1',
    comb_shield: null,
    confirmed_at: null,
    finished_at: null,
    goal_id: null,
    id: 'goalreq_1',
    needs_confirmation: true,
    received_at: '2026-01-01T00:00:10Z',
    refused: false,
    source: 'spoken',
    state: 'AWAITING_CONFIRMATION',
    updated_at: '2026-01-01T00:00:10Z',
    ...over,
  };
}

export function voiceAccepted(
  over: Partial<Schemas['VoiceAccepted']> = {},
): Schemas['VoiceAccepted'] {
  return {
    duration_s: 2.5,
    goal: goalView(),
    intent: 'goal',
    language: 'en',
    transcript: 'write three haiku about bees',
    ...over,
  };
}

// A microphone for tests: counts what the view asked of it and hands back a clip the test chose.
export class FakeRecorder implements AudioRecorder {
  starts = 0;
  stops = 0;
  cancels = 0;
  // Set to make `start` wait, so a test can release the button before the microphone opens.
  gate: Promise<void> | null = null;
  // Set to make `start` fail, as a refused microphone does.
  failWith: Error | null = null;
  clip: RecordedClip = {
    bytes: new Uint8Array([1, 2, 3, 4, 5]),
    mediaType: 'audio/webm',
    durationS: 2.5,
  };

  async start(): Promise<ActiveRecording> {
    this.starts += 1;
    if (this.gate) await this.gate;
    if (this.failWith) throw this.failWith;
    return {
      stop: () => {
        this.stops += 1;
        return Promise.resolve(this.clip);
      },
      cancel: () => {
        this.cancels += 1;
      },
    };
  }
}

export interface OpenedChat {
  fake: FakeBrowser;
  hive: Hive;
  recorder: FakeRecorder | null;
  // `METHOD /path` of every request so far, queries removed, in order.
  paths: () => string[];
}

export interface ChatSetup {
  page?: Schemas['ChatPage'];
  inbox?: Schemas['InboxView'];
  // A refusal for the chat read, e.g. 403.
  chatStatus?: number;
  chatBody?: unknown;
  recorder?: boolean;
}

// A signed-in client on the fake browser and a store holding the app's slices plus the Chat's.
export async function openChat(setup: ChatSetup = {}): Promise<OpenedChat> {
  const recorder = setup.recorder ? new FakeRecorder() : null;
  const fake = createFakeBrowser(undefined, recorder ? { recorder } : {});
  await storeSession(fake.browser);
  fake.respond(
    'GET /v1/chat',
    setup.chatStatus ?? 200,
    setup.chatBody ?? setup.page ?? chatPage([]),
  );
  fake.respond('GET /v1/inbox', 200, setup.inbox ?? inboxView());
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const store = createHiveStore({ ...APP_SLICES, chat: chatSlice });
  const paths = () => fake.requests.map((r) => `${r.method} ${r.target.split('?')[0]}`);
  return { fake, hive: { store, client }, recorder, paths };
}
