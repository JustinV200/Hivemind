"""Check the GUI postcondition kinds on the attached peripherals, eventually and within a bound.

A page reacts after a click returns, so every GUI postcondition (waggle 1.6, ADR-0032) is an
eventual assertion: it is observed, and observed again after a short pause, until it holds or its
settle time runs out. `URL_MATCHES` reads the browser's URL (exact, or a prefix when `expected`
ends in `*`); `ELEMENT_TEXT` reads the text of the element its subject names (the subject grammar
`ElementTarget.from_subject` parses) and holds when it contains `expected`; `REGION_CHANGED`
fingerprints the display rectangle its subject names and holds when the digest differs from the
one taken before the action. A peripheral that fails while observing makes the observation fail,
never the gate: the postcondition simply does not hold, with the reason as what was observed.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.surface`. Called by `surface.core.ExoskeletonSurface.check`. Calls into
    `hivemind.exoskeleton.attach` (Peripherals, Deadline), `.errors`, `.geometry`,
    `.recorder.redact` and waggle only.

Key invariants:
    - What is returned as observed never holds a frame and never more than a bounded, scrubbed
      slice of page text.
    - A REGION_CHANGED with no before-digest never holds: there is nothing to compare.

See Also:
    - docs/waggle/spec.md section 8.3 for the three kinds' semantics.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.attach.ready import Deadline
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Region
from hivemind.exoskeleton.recorder.redact import scrub_text, scrub_url
from waggle.messages.capping import ElementTarget
from waggle.messages.labels import Postcondition, PostconditionKind

MAX_OBSERVED_CHARS = 200  # What a failed ELEMENT_TEXT reports of the text it did find.

__all__ = ["MAX_OBSERVED_CHARS", "Observation", "observe_until", "url_matches"]


@dataclass(frozen=True, slots=True)
class Observation:
    """One look at a postcondition: whether it held, and what was seen."""

    held: bool
    observed: str


async def observe_until(
    peripherals: Peripherals,
    postcondition: Postcondition,
    digests: Mapping[str, str],
    deadline: Deadline,
) -> Observation:
    """Observe `postcondition` until it holds or `deadline` passes; return the last observation.

    Args:
        peripherals: What the attach provided.
        postcondition: A URL_MATCHES, ELEMENT_TEXT or REGION_CHANGED assertion.
        digests: Region digests taken before the action, keyed by REGION_CHANGED subject.
        deadline: The settle time.

    Returns:
        The first observation that held, else the last one taken.
    """
    while True:
        seen = await _observe(peripherals, postcondition, digests)
        if seen.held or deadline.expired():
            return seen
        await deadline.pause()


def url_matches(url: str, expected: str) -> bool:
    """Return whether `url` is `expected`, or starts with it when `expected` ends in `*`."""
    if expected.endswith("*"):
        return url.startswith(expected[:-1])
    return url == expected


async def _observe(
    peripherals: Peripherals, postcondition: Postcondition, digests: Mapping[str, str]
) -> Observation:
    """Take one look, turning a peripheral failure into a failed observation."""
    observer = _OBSERVERS.get(postcondition.kind)
    if observer is None:
        return Observation(False, f"{postcondition.kind.value} is not a GUI kind")
    try:
        return await observer(peripherals, postcondition, digests)
    except PeripheralError as error:
        return Observation(False, f"could not check: {error.reason}")


async def _url(
    peripherals: Peripherals, postcondition: Postcondition, _digests: Mapping[str, str]
) -> Observation:
    """URL_MATCHES: the page's URL, exactly or as a prefix."""
    if peripherals.browser is None:
        return Observation(False, "no browser is attached")
    url = await peripherals.browser.url()
    return Observation(url_matches(url, postcondition.expected or ""), scrub_url(url))


async def _element(
    peripherals: Peripherals, postcondition: Postcondition, _digests: Mapping[str, str]
) -> Observation:
    """ELEMENT_TEXT: the text of the element the subject names contains `expected`."""
    if peripherals.browser is None:
        return Observation(False, "no browser is attached")
    target = ElementTarget.from_subject(postcondition.subject)
    text = await peripherals.browser.element_text(target)
    if text is None:
        return Observation(False, f"no element matches {target.describe()}")
    held = (postcondition.expected or "") in text
    return Observation(held, scrub_text(text, MAX_OBSERVED_CHARS))


async def _region(
    peripherals: Peripherals, postcondition: Postcondition, digests: Mapping[str, str]
) -> Observation:
    """REGION_CHANGED: the rectangle's pixels differ from before the action."""
    if peripherals.compound_eye is None:
        return Observation(False, "no display is attached")
    subject = postcondition.subject
    before = digests.get(subject)
    if before is None:
        return Observation(False, "no before-digest was taken for this region")
    after = await peripherals.compound_eye.region_digest(Region.parse(subject))
    return Observation(after != before, "changed" if after != before else "unchanged")


# One observer per GUI kind, looked up rather than matched: every GUI kind is one entry here.
_Observer = Callable[[Peripherals, Postcondition, Mapping[str, str]], Awaitable[Observation]]
_OBSERVERS: dict[PostconditionKind, _Observer] = {
    PostconditionKind.URL_MATCHES: _url,
    PostconditionKind.ELEMENT_TEXT: _element,
    PostconditionKind.REGION_CHANGED: _region,
}
