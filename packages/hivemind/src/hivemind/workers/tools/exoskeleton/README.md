# hivemind.workers.tools.exoskeleton

The Worker's tools for the Exoskeleton: the display, pointer and keyboard, browser and audio a
Cell is given for one task (roadmap step 6.5, with the browser fast path's tools from 6.11). A
terminal-only task is offered none of them.

Reads look and report. Actions never drive a peripheral themselves: each call becomes one typed
GUI proposal (`ProposedAction` of kind GUI, waggle 1.6) that the Capping gate checks, applies
through the attached Exoskeleton, verifies against the call's `expect`, rolls back and records
(ADR-0032).

```
model call ──> tool ──> GuiStep(s) + expect ──> proposals.cap ──> CappingGate
                                                                    │ checks (tier)
                                                                    │ before (undo point, digests)
                                                                    │ apply  ─> ExoskeletonSurface ─> peripherals
                                                                    │ verify expect
                                                                    └ roll back on failure ─> Alarm at once
```

## Tools

| Tool | Arguments | Proposes | Tier |
|---|---|---|---|
| `see` | `region?` ("x,y,w,h") | no; returns the frame as an image | read-only; offered only with `vision` |
| `click` | `x`, `y`, `button?` (left/middle/right), `double?` | CLICK or DOUBLE_CLICK | display started by the lease: `scratch_write`; the operator's running display: `device_command` |
| `move` | `x`, `y` | MOVE | as `click` |
| `type` | `text`, `secret?` | TYPE | as `click` |
| `press` | `keys` ("ctrl+s", "Return") | PRESS | as `click` |
| `scroll` | `dx?`, `dy?`, `x?`+`y?` | SCROLL | as `click` |
| `browser_navigate` | `url` (http, https, about:blank, or a file inside scratch) | NAVIGATE | the destination: a file inside the lease's scratch, about:blank or a loopback host is `scratch_write`; any other page `network_egress` (and the gate wants `net:<host>`); a file URL anywhere else is refused before it is proposed |
| `browser_click` | `target` | BROWSER_CLICK | the page shown now, by the same rule |
| `browser_fill` | `target`, `text`, `secret?` | BROWSER_FILL | the page shown now |
| `browser_press` | `keys`, `target?` | BROWSER_PRESS | the page shown now |
| `browser_snapshot` | none | no; URL, title and accessibility tree | read-only |
| `browser_read` | `target?` | no; the element's text, else the page's | read-only |
| `browser_screenshot` | none | no; returns the viewport as an image | read-only; offered only with `vision` |
| `listen` | `seconds` (0.1 to 60) | no; a transcript through `Ears`, or the recording itself for a model that declares `audio` | read-only; offered only when something can hear |
| `say` | `clip` (a WAV in scratch) | SAY | `scratch_write`: the microphone is the lease's own sound server |

Every action tool also takes `expect` and `irreversible`:

- `expect` is exactly one of `{"url": ...}` (URL_MATCHES on the page; a trailing `*` matches a
  prefix), `{"element": target, "text": ...}` (ELEMENT_TEXT: the element's text contains `text`;
  the postcondition's subject is the target in `ElementTarget.subject()` form, such as
  `role=button;name=Log in`) or `{"region": "x,y,w,h"}` (REGION_CHANGED). One the attached
  peripherals could never check (a URL with no browser, a region with no display or off the
  screen) is refused before anything is proposed.
- `irreversible: true` raises the tier to `irreversible`, which a judge reviews before the bee's
  next step. The gate's checks may raise a tier; nothing lowers one. A reach the tool cannot
  establish (an unreadable page URL, a display the lease did not start, a `file://` URL outside
  scratch or naming a remote host) takes the higher tier.

A `file://` URL reads the Cell's disk without the path rules `read_file` applies, so the browser
may open only files inside the lease's scratch (the bee's own pages). Three layers hold that line,
all asking `hivemind.guard.file_urls` the same lexical question: `browser_navigate` refuses any
other file URL before proposing it; the gate's allowlist rung refuses one that reaches it anyway
(it is tiered `outside_scratch_write`, so the rung runs); and the browser itself refuses it, the
real one through a route that also resolves symlinks and covers links, frames and every other file
request a page makes (`hivemind.exoskeleton.browser.playwright.guard`).

A `target` names one element exactly one way: `{"role", "name"?}` (preferred: what
`browser_snapshot` shows), `{"label"}`, `{"text"}` or `{"selector"}`.

When a GUI proposal is rolled back, because its expectation did not hold or a step failed,
`proposals.cap` notes a `POSTCONDITION_FAILED` Alarm at once rather than after three rollbacks
(ADR-0032). The Alarm names the proposal, whose id the flight recorder keys the recording by;
never a frame or typed text. A judge's REJECT of an applied irreversible action comes back as a
failed result naming the judge's reasons (the gate has already raised that Alarm).

## Modules

- `act.py` -- `GuiAction` and `act` (one GUI proposal at its reach's tier, through
  `proposals.cap`), the reach rules `desktop_reach`, `page_reach` and `current_page_reach`, and
  `attached`/`need` (find the handle and peripheral a tool needs).
- `arguments.py` -- the JSON schemas the tools share (`TARGET_SCHEMA`, `EXPECT_SCHEMA`,
  `IRREVERSIBLE_SCHEMA`, `action_definition`, `read_definition`) beside their parsers
  (`target_from`, `region_from`, `build_step`, the scalar readers). `build_step` never quotes a
  failing value, because a TYPE or BROWSER_FILL step's value is typed text.
- `expect.py` -- `expectations`: a call's `expect` as its proposal's postconditions.
- `desktop.py`, `browser.py`, `look.py`, `audio.py` -- the tools, grouped by what they drive.
- `offer.py` -- `exoskeleton_specs(ctx)`, which `hivemind.workers.tools.registry.build_registry`
  calls, and `ACTION_TOOL_NAMES`/`READ_TOOL_NAMES`.
- `errors.py` -- `GuiArgumentError`, `PeripheralMissingError`, `PeripheralReadError`: each a
  `ToolError`, so its message reaches the model as ordinary tool-result text.

## What never leaves a tool

Screenshots and recordings travel only as `ToolOutput.media` (and so as
`hivemind.llm.ToolResultPart.media`); an adapter sends them only to a model that declares
`vision` or `audio` and refuses the request otherwise. Typed text travels only inside its step;
a result is the gate's outcome (`proposals.tool_output`), and a secret step is redacted in the
proposal's summary and every recording.

## How to test this

```
uv run pytest packages/hivemind/tests/unit/workers/tools/exoskeleton
```

The tests mirror this package module for module. `builders.workers.make_gui_context` attaches
the fake peripherals (`FakeCompoundEye`/`FakeScreen`, `FakeAntennae`, `FakeBuzz`, and the fake
browser serving the fixture login site) behind a real gate whose GUI surface is a real
`ExoskeletonSurface`, so every assertion is about what the gate actually applied, verified or
rolled back; `listen` hears through `FakeTranscription` behind real `Ears`.
