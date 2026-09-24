# hivemind.exoskeleton

The Exoskeleton is the Hive's optional set of peripherals for a Cell, attached for one task's lease
and only when the task asks (codingrules 8.7, ADR-0031): a display to see (`compound_eye`), a
pointer and keyboard to drive (`antennae`), audio to hear and speak (`buzz`), and a browser driven
through its accessibility tree (`browser`, the fast path). It is for GUI automation of applications
that have no API; it is not a stealth layer (codingrules 15; the Pheromone Mask tactics in
`tactics/` are policy-gated overlays, roadmap step 6.13).

## Layout

| Module / package | What it holds |
|---|---|
| `attach/` | `attach()` and `ExoskeletonHandle.detach()`: a pure plan from capabilities (`plan`), a private Xvfb + openbox display (`display`), a private PulseAudio server (`audio`), readiness deadlines (`ready`), the handle and its trail events (`handle`), the entry point (`core`). |
| `compound_eye/` | `CompoundEye` protocol, the ImageMagick `import` backend (`x11`), an in-memory `FakeScreen` (`fake`). |
| `antennae/` | `Antennae` protocol, the `xdotool` backend, an input-recording fake. |
| `buzz/` | `Buzz` protocol and `Recording`, the shared clip check (`clips`), the PulseAudio backend, a scripted fake. |
| `browser/` | `Browser` and `BrowserLauncher` protocols, the Playwright-over-CDP implementation, a fake site. |
| `commands.py` | `run_peripheral`: one command on the Cell, every failure a `PeripheralError` quoting only a sanitised stderr tail. |
| `errors.py` | `ExoskeletonError`, `AttachError`, `PeripheralError`, `ElementNotFoundError`. |
| `frames.py` | `Frame` (PNG bytes that never reach a log), `png_size`, `solid_png`. |
| `geometry.py` | `Point`, `Region` (and its REGION_CHANGED text form), `ScreenSize`. |
| `scratch.py` | `ScratchLayout`: where every lease-started process keeps its files, all inside scratch. |
| `x11.py` | `X11Display` and the wildcard authority file attach writes. |

## How attach behaves

- **Plans from capabilities, never from the kind of Cell.** A desktop uses the operator's own
  display only when the Cell reports it allowed and the bee holds `exoskeleton:real_display`;
  otherwise a private display when the Cell can start one and the bee holds `exoskeleton:display`.
  A browser-only need starts the browser alone, headless.
- **Private per lease.** Xvfb picks its own display number (`-displayfd`), listens on no TCP port
  and admits only holders of a fresh cookie in scratch. PulseAudio runs with no default config, a
  socket in scratch, rewinds disabled on its null sinks (otherwise a short `listen` hears
  nothing), and refuses later module loads or exit requests.
- **Left as found.** HOME and the XDG directories of every started process point into scratch, so
  no dotfile, cache or cookie lands in the operator's home. A failed attach stops everything it
  started before raising; `detach` stops exactly those processes and verifies they are gone.
- **On the trail.** `cell.exoskeleton_attached` and `cell.exoskeleton_detached`, with peripheral
  names and counts, never a frame.

## Testing

Unit tests run against `FakeSession` and the fakes. The contract suites under
`tests/contracts/test_exoskeleton_*` run the same clauses over the fakes and the real X11 and
PulseAudio backends on a real `LocalProcessSession` wherever Xvfb, openbox, xdotool, ImageMagick
and PulseAudio are installed (they skip, with the reason, where they are not).
`tests/integration/test_exoskeleton_docker.py` does the same inside a `desktop-ubuntu` container.
