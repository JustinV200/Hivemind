# hivemind.exoskeleton.antennae

The Exoskeleton's touch: drive a Cell's pointer and keyboard.

- `base.py`: the `Antennae` protocol (`move`, `click`, `type_text`, `press`, `scroll`, `pointer`).
- `xdotool.py`: `XdotoolAntennae`, `xdotool` run through the Cell's session. A window manager must
  be running on the display: without one, pointer moves succeed and do nothing (ADR-0031).
- `fake.py`: `FakeAntennae`, which records every input as an `InputEvent` (typed text never in its
  repr) and can tell a fake scenario about each one.
