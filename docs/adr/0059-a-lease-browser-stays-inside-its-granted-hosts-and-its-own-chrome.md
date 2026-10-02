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

**Redirects, sockets, workers and WebRTC are held to the same hosts.** Added the same day, after
the route alone was measured on Edge 154 (Playwright 1.63, over CDP) with a local server answering
on a granted loopback address and on an ungranted LAN address of the same host, counting every
connection each address received. Playwright continues a redirect's next hop unasked even when the
route answered the first hop itself (a fulfilled 302 to a granted address that redirected again
reached the ungranted one); a page's, a same-origin frame's and a dedicated worker's WebSockets, a
shared worker's fetches, and a TURN server over TCP all reached it; a dedicated or service
worker's fetches were routed. So, in `browser.playwright.hosts`:

1. A navigation or a script (resource types `document` and `script`) is fetched by the route
   itself, one hop (`route.fetch(max_redirects=0)`). A redirect to a refused host is aborted and
   reported with that host. A navigation's redirect to an allowed host is answered with a small
   page that calls `location.replace` on the target, a fresh navigation that comes back through
   the route, so every hop of a chain is judged; a followed redirect's single history entry and
   the hops' cookies (Playwright's request client stores them in the context) are kept. A non-GET
   navigation redirected with its method kept (307, 308) is refused rather than replayed as GET.
2. Every page and script the route fetched is answered with the lease's page policy
   (`browser.hosts.page_policy`): a `default-src` naming each allowed host the grammar can spell
   on http, https, ws and wss, any port, plus data:, blob: and the page's own inline and evaluated
   code, joined to the site's own policy so both are enforced. The browser then checks every other
   request a page or worker makes, each redirect hop of it, and every WebSocket, a worker's
   included, before sending it. Other requests (images, fetches, streams) are continued in the
   browser as before, so they still stream. `page_policy` names only hosts `HostScopes.allows`
   (unit-tested), and is omitted when `net:*` is held.
3. `SocketGuard` (`context.route_web_socket`) closes a page's or frame's WebSocket to a refused
   host with code 1008 and reports it, and connects the rest to their server.
4. Shared workers are off (`--disable-shared-workers`): Playwright routes none of their requests.
5. WebRTC: the profile sets `webrtc.ip_handling_policy = disable_non_proxied_udp` (no UDP, no
   local addresses shown), and since that still let a page reach a TURN server over TCP, an init
   script removes `RTCPeerConnection` and its companions from every page and frame.
6. Chromium's local network access check is off (`--disable-features=LocalNetworkAccessChecks`).
   A page the route answered has no address Chromium can see, so it counts as public and its
   granted socket to loopback was refused (`ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`); the
   host rule is stricter than that check, since such a host is reached only when granted.

Checked end to end through `connect_browser` on Edge 154, granted `net:loopback`: a direct
redirect and a granted-then-ungranted chain were blocked (both reported); a three-hop granted chain
landed on its final page with every hop's cookie and one history entry; a fetch, an image, an
EventSource and a dedicated worker's fetch through the same chain, and a worker's WebSocket, were
refused by the page policy; a page's WebSocket to the ungranted address was closed with 1008 and
reported, and one to a granted loopback echo server echoed; a granted EventSource streamed;
`SharedWorker` and `RTCPeerConnection` (in the page and a fresh frame) were undefined; a site's own
`img-src 'none'` still held; a POST answered with 307 was refused. The ungranted address received
no connection at all.

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

Positive: a bee's browser reaches no host it was not granted, by navigation, click, a page's own
request, a redirect, a WebSocket, a worker or WebRTC, and on a Real Cell that includes the
operator's loopback services; no file dialog or
DevTools window can be used to read or write outside scratch; a hard link no longer passes as a
file in scratch; every block is auditable. The rules are pure functions (`host_need`,
`HostScopes.allows`, `chrome_chord`) unit-tested on their own, and the guards' real behaviour was
checked on a real Chromium-family browser.

Negative: a site that loads from a CDN, an analytics host or a second domain now needs each of
those hosts granted, or those requests fail; the planner and the operator must grant what a task's
pages really use. A task on a Real Cell that drives a site the operator serves on loopback needs
`net:loopback` granted explicitly. Downloads no longer work at all through the lease's browser. A
bee that pressed one of the refused chords for an ordinary reason (Control+S in a page's own
editor) is refused while the browser is on screen. Navigations and scripts are fetched by
Playwright's request client rather than Chromium's network stack (the same machine, since the
driver attaches over loopback, and the browser's cookies): a server sees that client's TLS
handshake and headers (no `Sec-Fetch-*`), which bot checks may refuse; a streamed page arrives
whole; a large file opened as a page is fetched whole, bounded by the navigation timeout, before
the download is refused. WebRTC and shared workers do not work at all, and a non-GET navigation
redirected with 307 or 308 fails.

Known limits. What the page policy cannot cover: a page loaded from a `file://` URL is not answered
by the host route, so it has no policy, and its subresources' redirect hops and its workers'
WebSockets are unchecked; a grant for an IPv6 literal or a network range has no spelling in a
source list, so it serves navigations but a page cannot load from it (stricter, never looser); a
hop the policy refuses is refused in the browser and shown on the page's console, not recorded on
the trail. The WebRTC removal is a script, so it holds for pages and frames but relies on running
before the page's own code (Playwright's init script does; the profile setting is the second layer
and stops UDP regardless). Service worker registration cannot be blocked on the default context
Playwright attaches to over CDP; their fetches are routed and their scripts carry the policy. On a
Virtual Cell the container's egress policy (ADR-0026) still bounds all of these. DevTools is closed after it
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
Playwright's own interception on the same targets. Answering an allowed redirect with the 3xx itself
(what Playwright's examples do): the browser follows the next hop unrouted, so a chain through a
granted open redirector leaves. Following the whole chain in the guard and answering with the final
response: a navigation would show one site's page under another's URL, and a page's fetch would
read a second granted host's authenticated response as same-origin. Probing the chain and then
redirecting the browser straight to its end: the end is requested twice, which spends a login's
one-time code. A filtering forward proxy (`--proxy-server`) would see every hop and socket, workers'
and WebRTC's included, but is a listener of its own to run and secure per lease; it remains the
next step if the page policy's gaps matter. `route_web_socket` alone: it hooks sockets from inside
the page, so a worker's sockets pass it. Tiering all desktop input over a browser window
`network_egress`: the allowlist rung would then run for a click on a page that stays in the lease,
for nothing; the shown page's own reach says what the input can reach.
