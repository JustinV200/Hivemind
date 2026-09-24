# desktop-ubuntu image

The Exoskeleton-ready Virtual Cell image (roadmap step 6.1, ADR-0031): `base-ubuntu` plus
everything the Exoskeleton (the optional display, input, audio and browser attachment of a Cell)
needs to start on demand. The in-Cell Warden's probe (`hivemind.cell.local.probe`) sees the tools
and reports `can_start_display`, `has_audio` and `has_browser`, so placement sends Exoskeleton work
here (roadmap step 6.12).

**Nothing runs at boot.** `has_display` is false until a task attaches: `hivemind.exoskeleton.
attach` starts a fresh Xvfb, window manager, sound server and browser for that task's lease and
stops exactly those on detach. A display started at boot would carry one task's windows, cookies
and half-typed forms into the next task on an Overwintered Cell, and would give Virtual and Real
Cells two different code paths; starting per lease gives them one (ADR-0031).

## Layers, in order

| # | Package or step | Why it is here |
|---|---|---|
| 1 | builder: `ca-certificates`, `python3`, `python3-venv`, `uv` from PyPI | The same frozen, dev-free `uv sync` as `base-ubuntu`'s builder, with the `browser` extra added so the venv carries Playwright, the browser fast path's driver. `uv` is bootstrapped from PyPI for the reason `images/base-ubuntu/README.md` gives. |
| 2 | `xvfb` | The virtual framebuffer X server attach starts per lease: `-displayfd` lets it pick a free display itself, `-nolisten tcp` keeps it off the network, and a per-lease MIT cookie (`-auth`) keeps other local users off it. Pulls in Mesa and LLVM, most of this layer's size. |
| 3 | `openbox` | The light window manager. Required, not cosmetic: on Xvfb 21.1 `xdotool mousemove` returns success and the pointer never moves until a window manager runs (found on a real Xvfb while building phase 6). |
| 4 | `xdotool` | Pointer and keyboard input for `hivemind.exoskeleton.antennae.xdotool` (the Antennae backend): move, click, type, press, scroll. |
| 5 | `xauth` | Writes the per-lease authority file whose cookie the display requires. |
| 6 | `x11-utils` | `xdpyinfo` and `xprop`, for diagnosing a display from `hive cells inspect` or a shell; nothing in the Hive depends on them. |
| 7 | `imagemagick` | `import`, the CompoundEye backend's screen capture: PNG for a frame, raw RGB for a region digest, so comparing a region before and after an action never decodes an image. |
| 8 | `pulseaudio`, `pulseaudio-utils` | The per-lease sound server Buzz starts (a null sink as the speaker, a virtual source as the microphone) and its clients: `pactl` to configure it, `parec` for `listen`, `paplay` for `say`. |
| 9 | `fonts-dejavu-core`, `fonts-liberation` | Legible, metric-compatible text for what a vision model reads off the screen. |
| 10 | `/tmp/.X11-unix`, mode 1777 | The X socket directory, world-writable and sticky as on any desktop, so the unprivileged Xvfb a lease starts can create its socket. |
| 11 | the browser-capable venv | Replaces `base-ubuntu`'s venv at the same path, so every console script's absolute shebang still resolves. |
| 12 | Chromium via `playwright install --with-deps chromium`, in `/opt/playwright-browsers` | Playwright's own Chromium build plus the system libraries and CJK fonts it needs, at a path every user can read (`PLAYWRIGHT_BROWSERS_PATH`), which the Exoskeleton's browser locator scans. Ubuntu 24.04's `chromium` package only installs a snap, which cannot run in a container. The Exoskeleton starts it through the Cell's session with `--no-sandbox` here: the container drops every Linux capability Chromium's own sandbox needs, and the Cell itself is the sandbox (ADR-0031). |

The image runs as `hive`, like `base-ubuntu`; its ENTRYPOINT is `base-ubuntu`'s own
`hivemind-in-cell`.

## Size

About 2.2 GB unpacked (0.5 GB compressed): Mesa and LLVM for Xvfb, Chromium itself, and the fonts
and codecs Playwright's dependency list installs. A Cell built from it is provisioned only for
tasks that need an Exoskeleton; everything else boots `base-ubuntu`.

## Building and testing this image

Build `base-ubuntu` first, then this image, both from the repository root:

```
docker build -t hivemind/base-ubuntu:dev -f images/base-ubuntu/Dockerfile .
docker build -t hivemind/desktop-ubuntu:dev -f images/desktop-ubuntu/Dockerfile .
```

`BASE_IMAGE` (a build argument) names a different `base-ubuntu` tag. The integration workflow
builds both and runs `packages/hivemind/tests/integration/test_exoskeleton_docker.py` against a
real container: the probe inside reports the three capabilities, and a lease-started display,
input, capture, sound server and browser all work as the unprivileged `hive` user and all stop on
detach.

Rebuild after any source change: a container runs the package as built, not the checkout.
