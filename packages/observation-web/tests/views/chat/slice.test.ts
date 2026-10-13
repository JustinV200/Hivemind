/**
 * Tests for the Chat reducer: the seed, frames, pending messages, the inbox and the voice result.
 *
 * The reducer is pure, so each rule is one event or two and an assertion on the state. The rules
 * under test: lines keep log order with each id once, a pending message settles by the id the
 * Entrance returned (in either order of arrival), a live question or Alarm asks for an inbox
 * read and a repeat does not, the stream's resume cursor is the line's `seq`, and the slice is
 * bounded at 500 lines.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/chat/slice/ and reads/chat_stream.ts.
 *
 * Key invariants:
 *   - No store, no network: state in, state out.
 *
 * See Also:
 *   - tests/views/chat/chat_view.test.tsx for the same rules through the view.
 */
import { describe, expect, it } from 'vitest';
import { CHAT_RESUME } from '../../../src/views/chat/reads/chat_stream';
import { initialChat, MAX_GOALS, MAX_LINES, reduceChat } from '../../../src/views/chat/slice';
import type { ChatEvent, ChatState } from '../../../src/views/chat/slice';
import {
  alarmView,
  chatFrame,
  chatLine,
  chatLines,
  chatPage,
  goalView,
  inboxView,
  questionView,
  voiceAccepted,
} from './support';

const apply = (events: ChatEvent[], from: ChatState = initialChat): ChatState =>
  events.reduce(reduceChat, from);

describe('seed', () => {
  it('holds the page in log order and marks the slice seeded', () => {
    const state = apply([
      {
        type: 'seeded',
        page: chatPage([chatLine({ id: 'b', seq: 2 }), chatLine({ id: 'a', seq: 1 })]),
      },
    ]);
    expect(state.lines.map((l) => l.id)).toEqual(['a', 'b']);
    expect(state.seeded).toBe(true);
    // The seed reads the inbox itself, so it never asks for another read.
    expect(state.inboxWanted).toBe(0);
  });

  it('keeps a frame that arrived before the page, and each id once', () => {
    const state = apply([
      { type: 'frame', frame: chatFrame({ id: 'chat_2', seq: 2 }) },
      { type: 'seeded', page: chatPage(chatLines(2)) },
    ]);
    expect(state.lines.map((l) => l.id)).toEqual(['chat_1', 'chat_2']);
  });

  it('records a refused read and still counts as answered', () => {
    const error = new Error('no');
    const state = apply([{ type: 'failed', error }]);
    expect(state.error).toBe(error);
    expect(state.seeded).toBe(true);
  });
});

describe('frames', () => {
  it('appends a new line, and a repeat changes nothing', () => {
    const once = apply([{ type: 'frame', frame: chatFrame({ id: 'chat_9', seq: 9 }) }]);
    expect(once.lines).toHaveLength(1);
    expect(reduceChat(once, { type: 'frame', frame: chatFrame({ id: 'chat_9', seq: 9 }) })).toBe(
      once,
    );
  });

  it('ignores a frame that carries no line', () => {
    const odd = { type: 'voice' } as unknown as ReturnType<typeof chatFrame>;
    expect(reduceChat(initialChat, { type: 'frame', frame: odd })).toBe(initialChat);
  });

  it('asks for one inbox read per live question or Alarm, not for a reply', () => {
    const state = apply([
      { type: 'frame', frame: chatFrame({ id: 'r', seq: 1, kind: 'reply' }) },
      { type: 'frame', frame: chatFrame({ id: 'q', seq: 2, kind: 'question', ref: 'msg_q' }) },
      { type: 'frame', frame: chatFrame({ id: 'q', seq: 2, kind: 'question', ref: 'msg_q' }) },
      { type: 'frame', frame: chatFrame({ id: 'a', seq: 3, kind: 'alarm', ref: 'alarm_1' }) },
    ]);
    expect(state.inboxWanted).toBe(2);
    expect(state.announce?.id).toBe('a');
  });

  it('is bounded at 500 lines, dropping the oldest', () => {
    const seeded = apply([{ type: 'seeded', page: chatPage(chatLines(400)) }]);
    const full = apply(
      chatLines(200, 401).map(
        (entry) => ({ type: 'frame', frame: { type: 'chat', entry } }) as ChatEvent,
      ),
      seeded,
    );
    expect(full.lines).toHaveLength(MAX_LINES);
    expect(full.lines[0]!.id).toBe('chat_101');
    expect(full.lines[MAX_LINES - 1]!.id).toBe('chat_600');
  });
});

describe('resume cursor', () => {
  it('is the line seq, from chat frames only', () => {
    expect(CHAT_RESUME.cursorOf(chatFrame({ seq: 12 }))).toBe('12');
    expect(
      CHAT_RESUME.cursorOf({ type: 'voice' } as unknown as ReturnType<typeof chatFrame>),
    ).toBeNull();
    expect(CHAT_RESUME.query('12')).toBe('?after=12');
  });
});

describe('pending messages', () => {
  const compose: ChatEvent = {
    type: 'compose',
    key: 'k1',
    text: 'tidy the garden',
    at: '2026-01-01T00:00:00Z',
  };

  it('shows at once, then settles when the line with the returned id arrives', () => {
    const sending = apply([compose]);
    expect(sending.pending[0]).toMatchObject({ status: 'sending', lineId: null });
    const sent = apply([{ type: 'sent', key: 'k1', id: 'chat_7' }], sending);
    expect(sent.pending[0]).toMatchObject({ status: 'sent', lineId: 'chat_7' });
    const settled = apply(
      [
        {
          type: 'frame',
          frame: chatFrame({ id: 'chat_7', seq: 7, author: 'human', kind: 'message' }),
        },
      ],
      sent,
    );
    expect(settled.pending).toEqual([]);
    expect(settled.lines.map((l) => l.id)).toEqual(['chat_7']);
  });

  it('settles at once when the line arrived before the answer did', () => {
    const state = apply([
      compose,
      {
        type: 'frame',
        frame: chatFrame({ id: 'chat_7', seq: 7, author: 'human', kind: 'message' }),
      },
      { type: 'sent', key: 'k1', id: 'chat_7' },
    ]);
    expect(state.pending).toEqual([]);
  });

  it('keeps a refused message with its error until it is dismissed', () => {
    const error = new Error('refused');
    const failed = apply([compose, { type: 'send_failed', key: 'k1', error }]);
    expect(failed.pending[0]).toMatchObject({ status: 'failed', error });
    expect(apply([{ type: 'dismiss_pending', key: 'k1' }], failed).pending).toEqual([]);
  });
});

describe('inbox', () => {
  const inbox = inboxView({
    questions: [questionView({ id: 'q1' }), questionView({ id: 'q2' })],
    alarms: [alarmView({ id: 'a1' })],
  });

  it('drops an answered question and an acknowledged Alarm, and only those', () => {
    const state = apply([
      { type: 'inbox', inbox },
      { type: 'answered', questionId: 'q1' },
      { type: 'acknowledged', alarmId: 'a1' },
    ]);
    expect(state.inbox?.questions.map((q) => q.id)).toEqual(['q2']);
    expect(state.inbox?.alarms).toEqual([]);
  });

  it('changes nothing for an item it does not hold', () => {
    const held = apply([{ type: 'inbox', inbox }]);
    expect(reduceChat(held, { type: 'answered', questionId: 'zzz' })).toBe(held);
  });
});

describe('spoken results', () => {
  it('keeps a held goal and the transcript', () => {
    const state = apply([{ type: 'spoken', result: voiceAccepted(), at: '2026-01-01T00:00:11Z' }]);
    expect(state.spoken?.result.transcript).toBe('write three haiku about bees');
    expect(state.goals.map((g) => g.id)).toEqual(['goalreq_1']);
  });

  it('updates the goal when it is confirmed', () => {
    const state = apply([
      { type: 'spoken', result: voiceAccepted(), at: 'x' },
      { type: 'goal', goal: goalView({ state: 'PLANNING', needs_confirmation: false }) },
    ]);
    expect(state.goals).toHaveLength(1);
    expect(state.goals[0]!.state).toBe('PLANNING');
  });

  it('removes the question a spoken answer settled', () => {
    const state = apply([
      { type: 'inbox', inbox: inboxView({ questions: [questionView({ id: 'q1' })] }) },
      {
        type: 'spoken',
        result: voiceAccepted({
          intent: 'answer',
          goal: null,
          answered: {
            question_id: 'q1',
            question_status: 'ANSWERED',
            task_id: 'task_1',
            task_status: 'RUNNING',
          },
        }),
        at: 'x',
      },
    ]);
    expect(state.inbox?.questions).toEqual([]);
  });

  it('shows a spoken chat line as a sent message until its line arrives', () => {
    const state = apply([
      {
        type: 'spoken',
        result: voiceAccepted({ intent: 'chat', goal: null, chat: { id: 'chat_5' } }),
        at: 'x',
      },
    ]);
    expect(state.pending[0]).toMatchObject({
      text: 'write three haiku about bees',
      lineId: 'chat_5',
    });
  });
});

describe('goals asked for from the chatbox', () => {
  const asked = (id: string, at = '2026-01-01T00:00:20Z'): ChatEvent => ({
    type: 'goal_asked',
    goal: goalView({ id, source: 'typed', state: 'RECEIVED', needs_confirmation: false }),
    text: `words for ${id}`,
    at,
  });

  it('keeps the words as a line and the request as a goal', () => {
    const state = apply([asked('goalreq_1')]);
    expect(state.asked).toEqual([
      { id: 'goalreq_1', text: 'words for goalreq_1', at: '2026-01-01T00:00:20Z' },
    ]);
    expect(state.goals.map((g) => g.id)).toEqual(['goalreq_1']);
  });

  it('shows a request once, and updates its goal when it is confirmed', () => {
    const held: ChatEvent = {
      type: 'goal_asked',
      goal: goalView({ id: 'goalreq_1', source: 'typed' }),
      text: 'words',
      at: '2026-01-01T00:00:20Z',
    };
    const confirmed = goalView({ id: 'goalreq_1', needs_confirmation: false, state: 'PLANNING' });
    const state = apply([held, held, { type: 'goal', goal: confirmed }]);
    expect(state.asked).toHaveLength(1);
    expect(state.goals[0]?.state).toBe('PLANNING');
  });

  it('is bounded like the goals themselves', () => {
    const events = Array.from({ length: MAX_GOALS + 5 }, (_, i) => asked(`goalreq_${i}`));
    const state = apply(events);
    expect(state.asked).toHaveLength(MAX_GOALS);
    expect(state.asked.at(-1)?.id).toBe(`goalreq_${MAX_GOALS + 4}`);
  });
});
