"""Define the Exoskeleton's vision: the compound_eye package.

The CompoundEye (named for a bee's compound eyes) sees a Cell's display: a whole-screen or cropped
PNG `Frame`, or a digest of one region's raw pixels, which is how the Capping gate checks a
REGION_CHANGED postcondition without decoding or storing an image. `base` is the protocol, `x11`
the backend over ImageMagick's `import` (run through the Cell's session), `fake` an in-memory
screen for tests and demos.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Built by `hivemind.exoskeleton.attach`; read by the `see` tool, the flight recorder and the
    Capping gate's GUI surface. Calls into `hivemind.cell` and the exoskeleton's own `frames`,
    `geometry`, `commands`, `errors` and `x11` modules.

Key invariants:
    - A Frame never reaches a log or the trail; its own repr hides the image bytes.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the X11 backend.

Public API:
    - CompoundEye (base): the protocol.
    - X11CompoundEye, CAPTURE_TIMEOUT_S (x11): the backend.
    - FakeCompoundEye, FakeScreen, Rgb, WHITE (fake): the in-memory display.
"""

from hivemind.exoskeleton.compound_eye.base import CompoundEye
from hivemind.exoskeleton.compound_eye.fake import WHITE, FakeCompoundEye, FakeScreen, Rgb
from hivemind.exoskeleton.compound_eye.x11 import CAPTURE_TIMEOUT_S, X11CompoundEye

__all__ = [
    "CAPTURE_TIMEOUT_S",
    "WHITE",
    "CompoundEye",
    "FakeCompoundEye",
    "FakeScreen",
    "Rgb",
    "X11CompoundEye",
]
