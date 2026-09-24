"""Define the Exoskeleton's touch: the antennae package.

The Antennae (a bee's organs of touch) drive a Cell's pointer and keyboard: move, click, type,
press a chord, scroll, and read the pointer back. `base` is the protocol, `xdotool` the X11 backend
(run through the Cell's session), `fake` an in-memory recorder of inputs for tests and demos.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Built by `hivemind.exoskeleton.attach`; called by the Capping gate's GUI surface applying a
    checked GUI proposal, and by attach proving a display takes input. Calls into `hivemind.cell`
    and the exoskeleton's own `geometry`, `commands`, `errors` and `x11` modules.

Key invariants:
    - Typed text is one argument, never shell-interpreted, and never logged or rendered.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the xdotool backend.

Public API:
    - Antennae (base): the protocol.
    - XdotoolAntennae, INPUT_TIMEOUT_S, TYPE_DELAY_MS (xdotool): the backend.
    - FakeAntennae, InputEvent, InputKind (fake): the in-memory recorder.
"""

from hivemind.exoskeleton.antennae.base import Antennae
from hivemind.exoskeleton.antennae.fake import FakeAntennae, InputEvent, InputKind
from hivemind.exoskeleton.antennae.xdotool import INPUT_TIMEOUT_S, TYPE_DELAY_MS, XdotoolAntennae

__all__ = [
    "INPUT_TIMEOUT_S",
    "TYPE_DELAY_MS",
    "Antennae",
    "FakeAntennae",
    "InputEvent",
    "InputKind",
    "XdotoolAntennae",
]
