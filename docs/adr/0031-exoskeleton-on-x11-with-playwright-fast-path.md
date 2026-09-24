# ADR-0031: The Exoskeleton is X11 peripherals and a Playwright fast path, attached per lease through the Cell's own session

- Status: Accepted
- Date: 2026-09-24

## Context

Phase 6 gives a Worker a desktop on whichever Cell it is bound to: a framebuffer to see, a
keyboard and mouse to drive, audio in and out, and a browser. Codingrules 8.7 says the Exoskeleton
is an attachment, never an assumption, and that a Worker only ever touches its Cell through its
`CellSession`, so the same backend has to work inside a Virtual Cell (an `InCellSession` inside a
container), on a Linux Real Cell (the Hive Stand's `LocalProcessSession`) and later on a Swarm
device (a `PollenSession` over Waggle). The Windows Hive Stand, the development host, has no X
server to start, but it always has a Chromium-family browser.

Four facts from running the tools for real shaped this decision:

- On Xvfb 21.1 with no window manager, `xdotool mousemove` returns success and the pointer does
  not move. With `openbox` running it works. The window manager is load-bearing, not cosmetic.
- A display, a sound server and a browser are long-running processes. `CellSession.exec` runs a
  command to completion, so there was no way to start one through the session and have the lease
  know about it.
- The Warden rebuilt a task's `TaskNeeds` from `TaskAssign.tempo` alone
  (`wardens/spawn/spawn.py`), so an Exoskeleton need (and every network scope) set by the planner
  never reached the Cell that had to honour it.
- Chromium's own sandbox cannot start in a container with every Linux capability dropped, which
  is how every Virtual Cell runs (ADR-0026).

## Decision

**Four peripherals, each a Protocol over a `CellSession`.** `CompoundEye` (see: capture a frame, a
region, a region digest), `Antennae` (act: move, click, type, press, scroll), `Buzz` (hear and
speak: record a clip from the Cell's speaker, play a clip into its microphone) and `Browser` (the
fast path: navigate, act on an accessible element, read the page as an accessibility tree). Every
backend reaches its Cell only through `exec`, `start`, `stop`, `get_file` and `put_file` on the
session it is given, so one backend serves every Cell kind. Each has a fake beside it and one
contract suite parametrised over the real backend and the fake.

**X11 backends.** `compound_eye/x11.py` captures with ImageMagick's `import` (PNG for a frame, raw
RGB for a region digest, so a region comparison never decodes an image), `antennae/xdotool.py`
drives `xdotool`, and `buzz/pulseaudio.py` records from a null sink's monitor with `parec` and plays
into a virtual source with `paplay`. A display is always Xvfb plus a light window manager
(`openbox`); readiness is functional (the pointer is moved and read back), not a sleep.

**The browser fast path is Chromium started through the session and driven by Playwright over
CDP.** Attach starts Chromium with a profile in scratch and `--remote-debugging-port=0`, reads the
port Chromium writes to `DevToolsActivePort`, and connects with `connect_over_cdp`. The browser is
therefore a lease-started process like any other, killed by `detach()` and, as a backstop, by the
lease's release; Playwright's own driver runs beside the bee, never on the Cell. It is headed in
the attached display when there is one and headless otherwise, and it works without vision through
Playwright's aria snapshots and role/name locators. Playwright is an optional extra
(`hivemind[browser]`) imported only by `exoskeleton/browser/playwright.py`, enforced by an
import-linter contract, like the Docker SDK. It is the only Exoskeleton on Windows and macOS Real
Cells for 1.0, which on Windows means the always-present Edge or Chrome, found by the browser
locator. Chromium's sandbox stays on unless the composition root says the Cell is itself the
sandbox (a Virtual Cell) or the process runs as root.

**`CellSession` gains background processes.** `start(BackgroundSpec) -> BackgroundProcess` spawns
a command in its own process group (a new session on POSIX, a new process group on Windows) with
its output going to a log file in scratch or nowhere, reports the pid to the lease before
returning, and returns at once; `stop(process)` kills its whole tree and is idempotent;
`is_running(process)` answers liveness. `close()` and the lease's release still kill everything as
the backstop. Codingrules 8.7 is amended to list the three methods. Model hosting on a Nuc
(codingrules 8.10) needs the same primitive for its server.

**Attach is a pure plan plus a handle.** `exoskeleton.attach(cell, session, needs, capabilities,
config) -> ExoskeletonHandle`. A pure `plan_attach` reads only capabilities, never `cell.kind`:
use the display already running when the Cell has one and the bee holds
`exoskeleton:real_display`; otherwise start Xvfb and a window manager owned by the lease when the
Cell can start a display and the bee holds `exoskeleton:display`; start a per-lease PulseAudio
only when the task needs audio; start a browser when the task needs one. The handle records every
process it started, and `detach()` stops exactly those, newest first, then verifies each is gone
and removes the display's own lock and socket files if a process had to be killed hard. It never
stops a process it did not start.

**Displays are private to the lease.** Xvfb picks a free display number itself (`-displayfd`),
listens on no TCP port, and admits only clients holding a random MIT cookie written to an
authority file in scratch. PulseAudio runs with its runtime and home directories in scratch, so it
never touches the operator's own sound configuration.

**The `desktop-ubuntu` image carries the tools, not a running desktop.** It adds Xvfb, openbox,
xdotool, ImageMagick, xauth, PulseAudio and its utilities, Chromium and fonts to `base-ubuntu`, and
reports `can_start_display`, `has_audio` and `has_browser`. It starts nothing at boot: attach
starts a fresh display per lease, so an Overwintered Cell never carries a previous task's windows
into the next one, and the Virtual and Real paths run the same code.

**A new capability family, `exoskeleton`.** Scopes are `display`, `audio`, `browser` and
`real_display`. The FULL ceiling grants the first three, SCRATCH grants `browser` only (its profile
and home live in scratch), READ_ONLY grants none. `real_display` is never part of any ceiling: it
is issued only when the Cell's own capability report says the operator allows the Hive to drive
the display that is already running there (`CellCapabilities.real_display_allowed`, set on the
Hive Stand by `[hive_stand] exoskeleton_real_display = true`, default false), which is what
codingrules 15 means by "never issued implicitly".

**Needs travel with the task.** `TaskNeeds` gains `browser_only` and `audio`, both requiring
`exoskeleton`. Waggle 1.6 carries the Exoskeleton need and the task's network scopes on
`TaskAssign`, so the Warden attaches exactly what the plan asked for and grants exactly those
scopes. Placement (6.12) lets a Real Cell take a desktop need when it can start a display or its
running display is allowed, a browser-only need when it has a browser, and an audio need when it
has audio; otherwise the task goes to a `desktop-ubuntu` Virtual Cell.

**Every attach and detach is on the trail** as `cell.exoskeleton_attached` and
`cell.exoskeleton_detached`, carrying peripheral names and process counts, never a frame. Neither
kind survives a Night Veil teardown.

## Consequences

Positive: one backend per peripheral for every Cell kind; nothing about a desktop exists until a
task asks, and nothing a task started survives it. The browser path needs no vision, which is what
keeps weak local models useful for GUI work. Background processes are now a supported session
operation rather than a shell trick, which phase 8's model servers reuse.

Negative: `CellSession` is wider, and every future session (`PollenSession`) must implement three
more methods. ImageMagick and Chromium make the desktop image several hundred megabytes larger than
`base-ubuntu`. Connecting over CDP is lower fidelity than Playwright's own protocol for a few
features (tracing, some download hooks) that phase 6 does not use. The Windows Real Cell gets the
browser only; a native Windows desktop backend is post-1.0.

Known limits, found while building the fast path (2026-09-24). The DevTools port listens on
loopback only, but on a Real Cell shared by several local users any of them can reach it for the
life of the lease; a pipe transport (`--remote-debugging-pipe`) would close that and is the next
step if multi-user Real Cells matter. Chromium's crash handler runs in its own process group,
outside the lease's recorded processes; it exits within about a second of the browser, so release
leaves nothing behind in practice, but release does not kill it by name. The Windows and macOS
locator paths (Edge first on Windows, the app bundles on macOS) are written and unit-tested but
have not run on those systems. Even with every quieting switch, Chromium still contacts a few
Google endpoints at start; a Cell whose egress policy forbids them simply sees those requests fail.

## Alternatives considered

A display started at image boot: simpler attach, but it leaks GUI state across Overwintered reuse
and gives Virtual and Real Cells different code paths. Launching the browser with Playwright's own
`launch()`: its process would belong to the driver, not the lease, so neither `detach()` nor a
release could guarantee it is gone. Backgrounding through `sh -c 'cmd & echo $!'`: POSIX only, a
shell script in the middle of every start, and no answer for Windows or a model server. A raw CDP
client instead of Playwright: no dependency, but locators, auto-waiting and aria snapshots would be
rewritten by hand. Wayland: no headless compositor as simple as Xvfb, and `xdotool`'s equivalents
are compositor-specific.
