/**
 * The tour's steps: what each outlines, what it says, and the screen it needs open.
 *
 * A step names a `data-tour` attribute on a real element rather than a class or a position, so
 * a restyle cannot break it; the shell carries the attributes on the strip, the connection word,
 * the nav and the theme toggle, every view's title row carries `view-title`, and the chat's
 * composer `composer`. A step on another screen names its route, which the tour opens before it
 * looks for the element; a target that is not on the page (the composer on a device that may not
 * write) is skipped. The words are short, since the panel is small on a phone, and plain, since
 * the Help pages hold the detail.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/help/tour/`. Read by `tour.tsx`; the tests render a
 *   table of their own through the same component.
 *
 * Key invariants:
 *   - Every `target` is a `data-tour` value that exists in `src/shell/` or `src/views/`.
 *   - A step with a `route` names a hash the router resolves on every listener.
 *
 * See Also:
 *   - src/views/help/content/screens.md for the long form of each step.
 */

export interface TourStep {
  // The `data-tour` value of the element to outline.
  target: string;
  title: string;
  text: string;
  // The hash route whose screen holds the element, when it is not on every screen.
  route?: string;
}

export const TOUR_STEPS: readonly TourStep[] = [
  {
    target: 'strip',
    title: 'The summary strip',
    text:
      'Four live counters that never scroll away: bees working, Cells active and inactive, ' +
      'Real and Virtual Cells, and bees thinking by model against by rule. A number that just ' +
      'moved is underlined for a moment.',
  },
  {
    target: 'connection',
    title: 'The connection word',
    text:
      'Says whether the live streams are connected: live, connecting, reconnecting or offline. ' +
      'Nothing here polls; a view redraws as frames arrive, and a dropped stream is said in words.',
  },
  {
    target: 'nav',
    title: 'The nav',
    text:
      'Fleet, Forage, Thoughts, Attendant and Chat are the main five. On a phone, More holds ' +
      'Honey, Cost, Capping, Devices, Enrol (on the Hive Stand only) and Help; a wide screen ' +
      'lists them all in the rail.',
  },
  {
    target: 'view-title',
    route: '#/fleet',
    title: 'The Fleet',
    text:
      'Every machine (Cell) the Hive works on: its Comb Shield pill, its mask tag, status and ' +
      "current task. A Cell's name opens its page, with the live diagram of its bees.",
  },
  {
    target: 'view-title',
    route: '#/forage',
    title: 'Forage',
    text:
      "Capacity and who holds it: the Queen's shared pool, each Warden's grant and each bee's " +
      'spend, then per-host capacity, pending requests and the Forage map.',
  },
  {
    target: 'view-title',
    route: '#/thoughts/queen',
    title: 'Thoughts',
    text:
      'What a bee decided and why, one episode at a time, newest first. The Bee select reaches ' +
      'any bee, and Follow keeps new episodes arriving at the top.',
  },
  {
    target: 'view-title',
    route: '#/attendant/queen',
    title: 'The Attendant',
    text:
      'What is waiting on the Queen or a Warden, in priority order, and the task graph it is ' +
      'working through.',
  },
  {
    target: 'composer',
    route: '#/chat',
    title: 'The chat',
    text:
      'The one place you write. A message talks to the Queen; Request as a goal asks her to ' +
      'plan and run work. Her questions come back here with their answer buttons.',
  },
  {
    target: 'theme',
    title: 'Light or dark',
    text:
      'The theme toggle in the top bar switches the palette and remembers your choice on this ' +
      'browser.',
  },
  {
    target: 'view-title',
    route: '#/help',
    title: 'Help',
    text:
      'Every word, how the Hive works and how to read each screen, with a search. You can take ' +
      'this tour again from here.',
  },
];
