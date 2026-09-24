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
| `playwright/` | The Playwright backend: `calls` (every call bounded, every error mapped), `connect` (attach over CDP), `guard` (`FileGuard`, the route every file request passes), `storage` (what a checkpoint covers), `page` (`PlaywrightBrowser`). |
| `fake/` | `FakeSite` (a site as data), `FakeBrowser`, `FakeBrowserLauncher`, and `login_site()`, the fixture login site as a FakeSite. |
| `targets.py` | The rule both browsers share: a target names exactly one visible element. |
| `files.py` | `refuse_outside_roots`: the one refusal both browsers give a file URL outside `BrowserLaunch.file_roots` (the lease's scratch). |
| `keys.py` | `browser_chord`: a GUI step's chord ("ctrl+a", "Return") in the browser's spelling. |
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
   3. session.start(chromium_spec(...))    profile, log and TMPDIR in <scratch>/exoskeleton/browser,
                                           --remote-debugging-port=0 on 127.0.0.1, headless unless
                                           headed, --no-sandbox only when the request says so
   4. poll <profile>/DevToolsActivePort    on the injected clock; a crash fails at once with the
                                           end of its log (one line, URLs masked)
   5. connect_over_cdp(http://127.0.0.1:<port>) ──▶ PlaywrightBrowser on the default page
   6. FileGuard(request.file_roots).install  a route on file://**, before the first page loads
   any failure after 3: session.stop(process), then the AttachError (or the cancellation)
```

A file URL reads the Cell's disk without the path rules a session applies, so a browser loads one
only from its file roots (`BrowserLaunch.file_roots`, the lease's scratch; empty refuses every file
URL). Both browsers refuse a navigation outside them with the same `PeripheralError`
(`files.OUTSIDE_FILE_ROOTS`). The real one also routes every file request Chromium makes, a
link's, a frame's, an image's, through `playwright/guard.py`: continued only when the path is
inside the roots as written and once symlinks are resolved, aborted as blocked otherwise, so
nothing outside is ever read. A hard link planted in scratch is indistinguishable from a file
there; making one needs a command, which the Capping gate tiers by its own rules.

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
  authentication on that port; a pipe-based transport would close this and is post-1.0. Borrow
  only single-user machines for browser work until then.
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
