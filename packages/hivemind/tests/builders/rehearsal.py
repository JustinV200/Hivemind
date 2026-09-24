"""Build flight-recorder actions for rehearsal tests: the fixture login, as a bee would record it.

`recorded_action` builds one `RecordedAction` the way `FlightRecorder.end` does (typed steps
through `redact_step`, descriptions through `describe`), and `login_recording` is a whole
recording of the fixture site's login (`hivemind.exoskeleton.browser.fake.login_site`): open the
login page, one wrong click the gate rolled back, then the credentials and the submit, verified by
the welcome page's URL and heading.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the unit tests under
    packages/hivemind/tests/unit/exoskeleton/rehearsal.

Key invariants:
    - A recorded secret is the recorder's mask, exactly as a real recording keeps it.

See Also:
    - hivemind.exoskeleton.rehearsal for the code under test.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.exoskeleton.browser.fake import FIXTURE_ORIGIN, LOGIN_PASSWORD, LOGIN_USERNAME
from hivemind.exoskeleton.recorder import Evidence, RecordedAction, RecordedPostcondition
from hivemind.exoskeleton.recorder.redact import redact_step
from waggle.clock import FakeClock
from waggle.messages.capping import ElementTarget, GuiOp, GuiStep

__all__ = ["login_recording", "recorded_action"]


def recorded_action(
    steps: Sequence[GuiStep],
    postconditions: Sequence[RecordedPostcondition] = (),
    state: str = "VERIFIED",
) -> RecordedAction:
    """One action as the flight recorder keeps it: typed steps redacted, secrets as the mask."""
    now = FakeClock().now()
    return RecordedAction(
        proposal_id="proposal",
        tier="NETWORK_EGRESS",
        steps=tuple(step.describe() for step in steps),
        gui=tuple(redact_step(step) for step in steps),
        before=Evidence(),
        postconditions=tuple(postconditions),
        state=state,
        started_at=now,
        finished_at=now,
    )


def login_recording(site: str = FIXTURE_ORIGIN) -> tuple[RecordedAction, ...]:
    """A bee's recording of logging in to the fixture site at `site`, one mistake included."""
    held = {"has_held": True}
    return (
        recorded_action(
            (GuiStep(op=GuiOp.NAVIGATE, url=f"{site}/login"),),
            (
                RecordedPostcondition(
                    kind="URL_MATCHES", subject="page", expected=f"{site}/login", **held
                ),
            ),
        ),
        recorded_action(  # The bee clicked the heading first: rolled back, not part of the work.
            (GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(text="Log in")),),
            state="ROLLED_BACK",
        ),
        recorded_action(
            (
                GuiStep(
                    op=GuiOp.BROWSER_FILL,
                    target=ElementTarget(label="Username"),
                    text=LOGIN_USERNAME,
                ),
                GuiStep(
                    op=GuiOp.BROWSER_FILL,
                    target=ElementTarget(label="Password"),
                    text=LOGIN_PASSWORD,
                    secret=True,
                ),
                GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Log in")),
            ),
            (
                RecordedPostcondition(
                    kind="URL_MATCHES", subject="page", expected=f"{site}/welcome*", **held
                ),
                RecordedPostcondition(
                    kind="ELEMENT_TEXT", subject="role=heading", expected="Welcome, alice", **held
                ),
            ),
        ),
    )
