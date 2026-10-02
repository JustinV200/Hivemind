# ADR-0059: A lease's browser stays inside its granted hosts and out of its own chrome

- Status: Accepted
- Date: 2026-10-02

## Context

ADR-0034 kept a lease's browser inside its scratch for `file://` URLs. Closing that hole turned up
five more ways a Worker's browser lease reached past what the Guard granted it (phase 6 handoff,
section 4.2, items 2 to 6):

- Only the `browser_*` tools were held to the browser's rules. On a lease-started display,
  `press`, `type` and `click` were `scratch_write`, and a chord into the browser window could open
  Save As (which writes the page anywhere the Cell user can write), Open File (whose dialog lists
  directories), Print to PDF's save dialog, or DevTools (whose console runs code with the
  browser's own privileges and whose workspace reads and writes local folders).
- `page_reach` tiered a loopback host `scratch_write`, as if loopback stayed in the lease. On a
  Virtual Cell it does: it is the container's own network namespace. On a Real Cell it is the
  operator's own services, the Hive Stand's loopback listener included.
- Only `browser_navigate`'s own URL was checked against `net:<host>`. A click was tiered by the
  page it started on, and a page's scripts, images and frames went wherever the page said.
- `FileGuard` resolved symlinks, but realpath cannot see a hard link, so a second name in scratch
  for a file outside it passed as a file inside it.
- The browser's DevTools port listens on loopback, where any local user of a shared Real Cell
  can reach it.

Checked on this host's Edge 154 (Chromium) on Windows, 2026-10-02, through Playwright:

- The profile's `Local State` setting `select_file_dialogs.allowed = false` (what the
  `AllowFileSelectionDialogs` policy sets) is read as a user-controlled value, and a page's save
  picker is then refused at once with AbortError.
- The profile setting `devtools.availability = 2` (what `DeveloperToolsAvailability` sets) also
  refuses remote debugging: Playwright could not attach at all.
- A DevTools window arrives on a browser-level CDP session as a target with a `devtools://` URL,
  and `Target.closeTarget` closes it.
- With a route over every http(s) request and only `net:loopback` granted, a page's fetch to
  another host and a clicked link to it were blocked while its fetch to loopback went through; a
  hard-linked file in scratch was blocked and an ordinary one loaded. A 302 from the granted
  loopback server to another host was followed: Playwright asks a route only about a request's
  first URL.
- `Browser.setDownloadBehavior` with `deny` on the default context, sent after Playwright's own
  setup, cancels a download a page starts.

## Decision

**The browser's own windows stay shut, three ways.**

1. Before Chromium starts, `browser.profile.seed_profile` merges the lease's settings into the
   profile in scratch: file selection dialogs refused (`Local State`), and downloads pointed into
   scratch with no prompt (`Default/Preferences`). This is per lease because the profile is;
   Chromium's machine-wide policies (root-owned files on Linux) are never written, so a Real Cell
   is left as found.
2. Over the CDP link the lease already drives the browser by, `ChromeGuard` closes every target
   whose URL is a DevTools page the moment it is reported, and denies downloads, since no tool
   reads one and a download chooses its own file name. DevTools cannot be switched off by
   setting, because the setting that does it also refuses the remote debugging the lease uses.
3. The `press` tool refuses a chord that opens DevTools, Save As, Open File or Print
   (`keys.chrome_chord`: F12; Control or Command with Shift or Alt and I, J or C; Control or
   Command with S, O or P) while the lease's browser is shown on its display, and
   `browser_press` refuses it always. Desktop input over a display showing the browser is tiered
   at least as the page it shows, as the browser's own tools are (`desktop_input_reach`): a
   click on a page off the Cell is `network_egress`.

**Loopback needs a scope of its own on a Real Cell.** A loopback host, however spelled (`127.x`,
`[::1]`, `localhost`, `*.localhost`, an IPv4-mapped address), needs `net:loopback`
(`supervision.capping.host_need`); a grant for some other spelling never covers it. `page_reach`
tiers it `scratch_write` only where the Cell's loopback is private, and `network_egress` (so the
allowlist rung runs and wants `net:loopback`) everywhere else. Whether loopback is private is a
fact about the Cell's isolation, said by the composition root (`ExoskeletonConfig.
private_loopback`): the in-Cell Warden's wiring sets it, because a Virtual Cell's container has a
network namespace of its own; the Hive Stand's does not. Nothing branches on `cell.kind`. The
Guard's Hive-state floor (ADR-0041) still refuses every `net` scope that names a loopback host by
its own spelling, so `net:loopback` is the one way to grant it and is never implied by another
grant.

**Every web request is held to the bee's `net` scopes.** `HostGuard`, a Playwright route over
every http and https request of the lease's browser context, installed before the first page
loads beside `FileGuard`, continues a request only to a host `HostScopes` allows (the bee's `net`
scopes, plus loopback where it is private) and aborts it as blocked otherwise. A request whose host
cannot be read is blocked.

**A file with a second hard link is refused.** `FileGuard` refuses a file whose link count is
above one, even inside scratch, since its other name may be anywhere. A directory (whose link count
counts its subdirectories) and a missing file (nothing to read) are judged by path alone.

**What the browser blocks is on the trail.** Each guard reports to `BrowserLaunch.on_blocked`;
attach turns that into `cell.browser_blocked` (a refusal in the policy catalogue), carrying the
rule (`file`, `net`, `devtools`) and, for a host refusal, the host; never a URL or a path. One
event per distinct rule and host per lease, at most 64, so a page retrying a blocked host cannot
flood the trail.

**The DevTools transport is unchanged.** `--remote-debugging-pipe` has Chromium speak CDP over two
inherited descriptors (3 and 4). The lease starts Chromium through `CellSession.start`, which hands
a process no descriptors beyond its log, and Playwright's driver connects to an existing browser
only over a socket (`connect_over_cdp` takes ws or http). Playwright's own `launch()` uses a pipe,
but its process then belongs to Playwright's driver, not the lease, which ADR-0031 already
rejected: detach and release could no longer guarantee it is gone, the browser would start where
the bee runs rather than through the Cell's session, and a bee could no longer leave a Real Cell as
found. Closing this needs either a `CellSession` that can hand a background process a pipe pair
and a relay that serves it to the driver on a secret-gated socket, or a decision that multi-user
Real Cells take no browser work; both are outside this change, and ADR-0031's known limits say so.

## Consequences

Positive: a bee's browser reaches no host it was not granted, by navigation, click or a page's own
request, and on a Real Cell that includes the operator's loopback services; no file dialog or
DevTools window can be used to read or write outside scratch; a hard link no longer passes as a
file in scratch; every block is auditable. The rules are pure functions (`host_need`,
`HostScopes.allows`, `chrome_chord`) unit-tested on their own, and the guards' real behaviour was
checked on a real Chromium-family browser.

Negative: a site that loads from a CDN, an analytics host or a second domain now needs each of
those hosts granted, or those requests fail; the planner and the operator must grant what a task's
pages really use. A task on a Real Cell that drives a site the operator serves on loopback needs
`net:loopback` granted explicitly. Downloads no longer work at all through the lease's browser. A
bee that pressed one of the refused chords for an ordinary reason (Control+S in a page's own
editor) is refused while the browser is on screen.

Known limits. Playwright asks a route only about a request's first URL, so a redirect a granted
host answers with is followed; WebSockets, WebRTC and a worker's own connections are not routed
requests either. On a Virtual Cell the container's egress policy (ADR-0026) bounds all of these; on
a Real Cell they reach whatever the granted host or page sends them to. DevTools is closed after it
opens, within a CDP round trip, not prevented. The DevTools port on a shared multi-user Real Cell
(above) and Chromium's crash handler running outside the lease's process list (ADR-0031) remain.
`run_command` confinement (phase 6 handoff 4.2 item 1) is a separate decision this ADR does not
make: a command run in scratch can still make a hard link or copy a file in for `read_file`.

## Alternatives considered

Chromium policies for DevTools, downloads and file dialogs: machine-wide, written as root, never
per lease; a Virtual Cell's image could ship them, a Real Cell could not. `devtools.availability`
in the profile: per lease, but it turns off the remote debugging the lease drives the browser by.
A `framenavigated` check that navigates away from an ungranted host: the request has already been
sent by then, so contact with the host is not prevented, and subresources never fire it. Driving
Chromium's Fetch domain on a second CDP session to see redirect hops: it would contend with
Playwright's own interception on the same targets. Tiering all desktop input over a browser window
`network_egress`: the allowlist rung would then run for a click on a page that stays in the lease,
for nothing; the shown page's own reach says what the input can reach.
