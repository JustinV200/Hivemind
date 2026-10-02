# hivemind.exoskeleton.browser

The browser fast path (roadmap step 6.11, ADR-0031): a real Chromium-family browser on the Cell,
started for one lease through the Cell's own session, driven with Playwright over the Chrome
DevTools Protocol (CDP), and read back as structure rather than pixels: the URL, the title, an
accessibility-tree snapshot, one element's text, the page's visible text. A model without vision
can do browser work with it, and the Capping gate can check `URL_MATCHES` and `ELEMENT_TEXT`
postconditions and roll a GUI action back with `checkpoint`/`restore` (ADR-0032). It is headed
in the Cell's display when the lease has one and headless otherwise, and it is the only
Exoskeleton on Windows and macOS Real Cells for 1.0.

Playwright is an optional extra (`hivemind[browser]`). Only the `playwright/` package imports it
(an import-linter contract in the root `pyproject.toml`), and `ChromiumLauncher.launch` imports
that package lazily, so `import hivemind.exoskeleton.browser` never needs the extra; a launch
without it fails with an `AttachError` naming the extra.

## Modules

| Module | What it holds |
|---|---|
| `base.py` | The protocols: `Browser`, `BrowserCheckpoint`, `BrowserLauncher`, `BrowserLaunch`, `LaunchedBrowser`. |
| `locate.py` | `ChromiumLocator`: finds a browser on the Cell and verifies it through the session; `HostPlatform`, the host facts it reads (read once, injectable). |
| `launch.py` | `ChromiumLauncher`: starts Chromium through the session, waits for its DevTools port, connects; `ChromiumSettings`; `chromium_spec`, the exact command. |
| `playwright/` | The Playwright backend: `calls` (every call bounded, every error mapped), `connect` (attach over CDP), `guard` (`FileGuard`, the route every file request passes), `hosts/` (`HostGuard`: the route every web request passes, navigations and scripts fetched one hop at a time so no redirect is followed unchecked, every page and worker answered with the lease's page policy; `SocketGuard`, the route every WebSocket passes; WebRTC switched off), `chrome` (`ChromeGuard`: DevTools closed as it opens, downloads denied), `storage` (what a checkpoint covers), `page` (`PlaywrightBrowser`). |
| `fake/` | `FakeSite` (a site as data), `FakeBrowser`, `FakeBrowserLauncher`, and `login_site()`, the fixture login site as a FakeSite. |
| `targets.py` | The rule both browsers share: a target names exactly one visible element. |
| `files.py` | `refuse_outside_roots`: the one refusal both browsers give a file URL outside `BrowserLaunch.file_roots` (the lease's scratch). |
| `keys.py` | `browser_chord`: a GUI step's chord ("ctrl+a", "Return") in the browser's spelling; `chrome_chord`: which of the browser's own windows (DevTools, Save As, Open File, Print) a chord opens. |
| `hosts.py` | `HostScopes`: the hosts a lease's browser may request (the bee's `net` scopes; loopback needs `net:loopback` unless the Cell's loopback is private); `page_policy`: the same rule as a content security policy; `redirect_target`: where a redirect leads; `BrowserBlock`, `BlockSink`: what a guard blocked, and where it is reported. |
| `profile.py` | `seed_profile`: the per-lease settings written into the profile before Chromium starts (file dialogs refused, downloads in scratch, WebRTC kept off UDP). |
| `excerpts.py` | The caps every read obeys (`MAX_SNAPSHOT_CHARS`, `MAX_PAGE_TEXT_CHARS`, `MAX_ELEMENT_TEXT_CHARS`) and one-line, URL-free error details. |
| `state.py` | `BrowserState`, the cookies and per-origin local storage a checkpoint carries, and `storage_origin`. |

## How a launch works

```text
attach ──BrowserLaunch──▶ ChromiumLauncher.launch(session, request)
   1. import browser.playwright            (AttachError naming hivemind[browser] if missing)
   2. ChromiumLocator.locate(session)      Playwright builds (newest revision first), then
                                           chromium / google-chrome / microsoft-edge on the PATH,
                                           then Edge and Chrome's install paths on Windows and the
                                           app bundles on macOS; each verified on the Cell
                                           (`--version`, or `where` on Windows)
   3. seed_profile(session, profile, ...)  Local State refuses file dialogs; Preferences keep any
                                           download in scratch and WebRTC off UDP (merged into an
                                           existing profile)
   4. session.start(chromium_spec(...))    profile, log and TMPDIR in <scratch>/exoskeleton/browser,
                                           --remote-debugging-port=0 on 127.0.0.1, headless unless
                                           headed, --no-sandbox only when the request says so,
                                           shared workers off
   5. poll <profile>/DevToolsActivePort    on the injected clock; a crash fails at once with the
                                           end of its log (one line, URLs masked)
   6. connect_over_cdp(http://127.0.0.1:<port>) ──▶ PlaywrightBrowser on the default page
   7. FileGuard(request.file_roots).install  a route on file://**, before the first page loads
      HostGuard(request.hosts).install       a route on every http(s) request and every
                                             WebSocket, likewise; WebRTC removed from every page
      ChromeGuard().install                  closes every devtools:// target; denies downloads
   any failure after 4: session.stop(process), then the AttachError (or the cancellation)
```

A file URL reads the Cell's disk without the path rules a session applies, so a browser loads one
only from its file roots (`BrowserLaunch.file_roots`, the lease's scratch; empty refuses every file
URL). Both browsers refuse a navigation outside them with the same `PeripheralError`
(`files.OUTSIDE_FILE_ROOTS`). The real one also routes every file request Chromium makes, a
link's, a frame's, an image's, through `playwright/guard.py`: continued only when the path is
inside the roots as written and once symlinks are resolved, and naming a file with no second hard
link (realpath cannot see one, so its other name could be anywhere); aborted as blocked otherwise,
so nothing outside is ever read.

Web requests are held to the bee's `net` scopes the same way (ADR-0059): `playwright/hosts/`
aborts a request to any host `BrowserLaunch.hosts` does not allow, so a clicked link or a page's
own fetch reaches no host the bee was not granted. Playwright asks a route only about a request's
first URL, so the guard fetches a navigation or a script itself, one hop at a time: a redirect to
a refused host is aborted, and a navigation's redirect to an allowed one is answered with a page
that navigates there afresh, back through the route. Every page and worker script is answered
with the lease's page policy (`hosts.page_policy`, a `default-src` naming only allowed hosts,
added beside the site's own), so the browser itself checks every other request's redirect hops
and every socket, a worker's included. Page WebSockets also pass `SocketGuard`, shared workers are
off (`--disable-shared-workers`), and WebRTC is removed from every page and kept off UDP by the
profile. Every guard reports what it blocked to `BrowserLaunch.on_blocked`, which attach turns
into `cell.browser_blocked` on the trail; a hop the page policy refuses is refused by the browser
itself and reaches the page's console, not the trail.

The browser is a lease-started process like the display and the sound server: detach stops it,
the lease's release kills it as a backstop, and `Browser.close()` only disconnects Playwright.

Everything the browser writes stays in the lease's scratch. HOME and the XDG directories come
from the request (`ScratchLayout.home_environment()`), so the profile, the crash database and
dconf's cache land in scratch. Chromium keeps its one-browser-per-profile socket and lock in its
temporary directory and leaves that directory behind even after a clean exit, so TMPDIR is the
browser directory, named relatively (`TMPDIR=.` with the working directory there): an absolute
path would not fit a Unix socket address under a realistic scratch root.

## Known limitations

- **The DevTools port is open to the Cell's other users.** Chromium listens on 127.0.0.1 only, but
  on a shared multi-user Real Cell any local user who finds the port can drive the lease's browser
  (read its pages and cookies, act as it) for as long as the lease runs. Chromium offers no
  authentication on that port. A pipe (`--remote-debugging-pipe`) needs two descriptors the
  session would have to hand Chromium at start, and Playwright's driver still connects only over
  a socket, so it waits on a `CellSession` change (ADR-0059). Borrow only single-user machines
  for browser work until then.
- **What the host guard still does not hold** (ADR-0059's known limits). A page loaded from a
  `file://` URL gets no page policy, so its subresources' redirect hops and its workers' sockets
  are unchecked. A policy cannot spell an IPv6 literal or a network range, so a grant of one
  serves navigations but not a page's own loads from it. Navigations and scripts are fetched by
  Playwright's request client (same machine, the browser's cookies), so a server sees its TLS
  handshake and headers rather than Chromium's, a streamed page arrives whole, and a large file
  opened as a page is fetched whole before the download is refused. A non-GET navigation
  redirected with its method kept (307, 308) is refused. On a Virtual Cell the container's egress
  policy still bounds everything.
- **Chromium still makes a few connections of its own.** The switches in `QUIET_FLAGS` stop most
  background traffic, but no switch stops all of it (a Google endpoint or two at start-up, seen in
  testing). The Cell's network policy is what bounds egress.
- **Chromium's crash handler runs in a process group of its own**, so stopping the browser's group
  does not kill it; it exits by itself within a second of the browser. The contract suite checks
  that nothing mentioning the lease's scratch outlives a stop.
- **A checkpoint covers the page's own origins only**: every origin a frame of the driven page has
  loaded. Tabs the site opens itself (pop-ups) are not driven and not checkpointed, and server-side
  effects are out of any checkpoint's reach (ADR-0032). Reading another origin's storage opens a
  short-lived blank tab, visible for a moment in a headed browser.
- **The Windows and macOS locator paths are unit-tested only.** They follow Playwright's and the
  vendors' published install locations; phase 6's Windows Hive Stand exit run is their first real
  test.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/exoskeleton/browser \
    packages/hivemind/tests/contracts/test_browser_contract.py -q -p no:cacheprovider
```

The contract suite runs every `Browser` and `BrowserLauncher` clause over the fake and over a real
Chromium (marked `integration`) against the fixture site in `tests/fixtures/sites/login/`; the real
cases skip, saying why, where Playwright or a Chromium-family browser is missing. `uv sync
--all-extras` installs Playwright; `playwright install chromium`, a Chromium or Chrome on the PATH,
or `PLAYWRIGHT_BROWSERS_PATH` pointing at a Playwright browser cache provides the browser.
