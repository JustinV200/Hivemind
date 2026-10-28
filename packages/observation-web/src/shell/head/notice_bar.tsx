/**
 * `NoticeBar`: what the push stream says is waiting, shown under the strip whatever view is open.
 *
 * A question or an Alarm that reaches the human while they are reading the Forage view must not
 * wait until they happen to open the chat. The bar lists each kind of notice once, with how many
 * and a link to the view that holds the item (the chat, or the Devices view for a security
 * event), and a Dismiss button. Opening the chat clears the chat kinds, since the chat shows the
 * items themselves; a security event stays until dismissed.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/head/`. Rendered by `shell.tsx` in the sticky head; reads
 *   the notices slice that `feed.ts` fills from `/v1/push/stream`.
 *
 * Key invariants:
 *   - Announced once, politely: the bar is a `role="status"` region.
 *   - It names kinds and counts only; the item is fetched by the view the link opens.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6 (push shows a waiting question in any view).
 *   - src/store/slices/notices.ts for the fold.
 */
import { useEffect } from 'react';
import type { ReactElement } from 'react';
import { useRoute } from '../../router';
import { useHive, useSlice } from '../../store';
import type { NoticeKind } from '../../store';

interface Words {
  one: string;
  many: string;
  href: string;
  link: string;
}

// What each kind says, and where the item it points at can be seen.
const WORDS: Readonly<Record<Exclude<NoticeKind, 'withdrawn'>, Words>> = {
  question_waiting: {
    one: 'A question is waiting for you',
    many: 'questions are waiting for you',
    href: '#/chat',
    link: 'Answer in the chat',
  },
  alarm_waiting: {
    one: 'An Alarm reached you',
    many: 'Alarms reached you',
    href: '#/chat',
    link: 'See it in the chat',
  },
  reply_waiting: {
    one: 'The Queen replied',
    many: 'replies from the Queen',
    href: '#/chat',
    link: 'Read the chat',
  },
  goal_completed: {
    one: 'A goal finished',
    many: 'goals finished',
    href: '#/chat',
    link: 'Read the chat',
  },
  security_event: {
    one: 'A security event happened',
    many: 'security events happened',
    href: '#/devices',
    link: 'See the devices',
  },
};

const CHAT_KINDS: readonly NoticeKind[] = (Object.keys(WORDS) as NoticeKind[]).filter(
  (kind) => kind !== 'security_event',
);

export function NoticeBar(): ReactElement | null {
  const { store } = useHive();
  const { items } = useSlice('notices');
  const route = useRoute();

  // The chat shows the questions, Alarms and replies itself, so opening it clears their notices.
  useEffect(() => {
    if (route.name === 'chat' && items.some((item) => CHAT_KINDS.includes(item.kind))) {
      store.dispatch({ slice: 'notices', event: { type: 'dismiss_kinds', kinds: CHAT_KINDS } });
    }
  }, [route.name, items, store]);

  if (items.length === 0) return null;
  const counts = new Map<NoticeKind, number>();
  for (const item of items) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1);
  return (
    <div className="notice-bar" role="status">
      <ul>
        {[...counts.entries()].map(([kind, count]) => {
          const words = WORDS[kind as keyof typeof WORDS];
          return (
            <li key={kind}>
              <span>{count === 1 ? words.one : `${count} ${words.many}`}.</span>{' '}
              <a href={words.href}>{words.link}</a>
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        className="button notice-bar-dismiss"
        onClick={() =>
          store.dispatch({
            slice: 'notices',
            event: { type: 'dismiss_kinds', kinds: [...counts.keys()] },
          })
        }
      >
        Dismiss
      </button>
    </div>
  );
}
