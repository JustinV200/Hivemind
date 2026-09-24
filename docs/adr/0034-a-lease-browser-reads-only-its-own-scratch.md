# ADR-0034: A lease's browser reads only its own scratch, checked by the tool, the gate and the browser

- Status: Accepted
- Date: 2026-09-24

## Context

ADR-0032 tiers a GUI action by what it reaches and put every local `file://` page at
`scratch_write`, the tier whose checks are only SCHEMA and SIZE_CAP. A `file://` URL reads the
Cell's disk directly: it never passes the path rules a `CellSession` enforces for `read_file`
(scratch, or an allowed path plus an `fs:read` capability). So a Worker could `browser_navigate` to
`file:///home/<operator>/.ssh/id_rsa` on the Hive Stand and `browser_read` it, and a page the bee
wrote into scratch could reach the same file through a link it clicks, a script, or an iframe,
none of which is a navigation the tool ever sees. The allowlist rung never ran for any of it, and
`GuiAllowlistCheck` checked only `exoskeleton:browser` and `net:<host>`, never a path.

Checked on this repository's Chromium (2026-09-24): once a Playwright route covers `file://**`,
Chromium asks it about every file request (a direct navigation, a percent-encoded `..`, an iframe,
a clicked link) with the URL already canonicalised, and an aborted request leaves Chromium's own
error page with none of the file's content. A symlink in scratch arrives as its scratch path.

## Decision

**A browser may load a file URL only from its file roots, and a lease's only root is its
scratch.** `BrowserLaunch.file_roots` names them; attach fills it with the session's scratch;
empty refuses every file URL. Reading anything else stays `read_file`'s job, with its capability
and lease checks.

**One lexical rule, asked at three layers.** `hivemind.guard.file_urls.file_url_escapes` decides
whether a file URL stays inside the roots the way Chromium canonicalises it: backslashes read as
separators, percent-escapes decoded, dot segments collapsed; a relative, remote-host or
unreadable file URL always escapes; any other scheme never does. It lives in `guard` (Layer 2) so
every layer can ask it:

1. The tool: `browser_navigate` refuses an escaping file URL before any proposal exists.
2. The gate: `page_reach` tiers an escaping file URL `outside_scratch_write`, so the allowlist
   rung runs, and `GuiAllowlistCheck` fails any NAVIGATE step whose file URL escapes scratch,
   whatever capabilities the bee holds.
3. The browser: both browsers refuse such a navigation with one `PeripheralError`
   (`browser.files.OUTSIDE_FILE_ROOTS`), and the Playwright backend installs `FileGuard`, a
   route on `file://**`, before the first page loads. It continues a request only when the path
   is inside the roots as written and again once symlinks are resolved (the Playwright driver
   runs beside the bee on the same machine as the Cell's browser, since CDP listens on loopback
   only), and aborts it as blocked otherwise.

No refusal or log line names the URL or the path.

## Consequences

Positive: no file outside scratch reaches a read, a screenshot or a recording through the browser,
whether the bee asked for it or a page it loaded did; a symlink planted in scratch cannot lead out.
The rule is one pure function, tested with property-based cases, shared by all three layers.

Negative: a hard link in scratch is indistinguishable from a file there; making one needs a
command. That points at a wider, older gap this ADR does not close: `run_command` is tiered by its
working directory, so a command run in scratch is `scratch_write`, the allowlist rung (and with it
the `exec:` capability check) never runs, and the command can copy any file the Cell user can read
into scratch for `read_file`. It needs its own decision about what a scratch command may touch on
a Real Cell. A test harness that serves fixture pages as `file://` must name their directory as a
root, as the browser contract suite's does. A bee cannot open a page from an allowed path outside
scratch in the browser; it can copy the file into scratch first.

## Alternatives considered

A `framenavigated` guard that resets the page after the fact: the file would already be rendered,
visible to a desktop screenshot, and an iframe never fires a main-frame navigation. Chromium
policies (`URLBlocklist`): machine-wide, written as root, never per lease. Refusing every file URL:
the bee's own pages in scratch, and the rehearsal of browser procedures on fixture sites, need
them. Allowing read-allowed paths outside scratch too: every layer would need the lease's allowed
paths and the bee's capabilities, for a case `read_file` already serves.
