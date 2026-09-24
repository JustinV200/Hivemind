"""Unit tests for hivemind.exoskeleton.surface: ExoskeletonSurface over the fake peripherals."""

from __future__ import annotations

from pathlib import Path

from builders.capping import make_action, make_postcondition, make_proposal

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.antennae import FakeAntennae, InputEvent, InputKind
from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.browser import BrowserCheckpoint
from hivemind.exoskeleton.buzz import FakeBuzz
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.exoskeleton.errors import ElementNotFoundError
from hivemind.exoskeleton.frames import Frame, solid_png
from hivemind.exoskeleton.geometry import Region, ScreenSize
from hivemind.exoskeleton.recorder import FlightRecorder, InMemoryRecordingStore, RecordingInfo
from hivemind.exoskeleton.surface import ExoskeletonSurface
from hivemind.exoskeleton.surface.core import KEPT_FOR_REVIEW
from hivemind.supervision.capping import Proposal, ProposalState
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind, ElementTarget, GuiOp, GuiStep, RollbackMethod
from waggle.messages.labels import PostconditionKind

_SIZE = ScreenSize(200, 100)
_BUTTON = Region(x=10, y=10, width=40, height=20)


class _Page:
    """Just enough of a Browser for the surface: a URL, one heading, and checkpoints."""

    def __init__(self) -> None:
        self.current = "file:///site/login.html"
        self.heading: str | None = None
        self.restored: list[str] = []

    async def navigate(self, url: str) -> None:
        self.current = url

    async def click(self, target: ElementTarget) -> None:
        if target.name != "Log in":
            raise ElementNotFoundError(target.describe(), "click")
        self.current, self.heading = "file:///site/welcome.html", "Welcome, alice"

    async def url(self) -> str:
        return self.current

    async def element_text(self, target: ElementTarget) -> str | None:
        return self.heading if target.role == "heading" else None

    async def snapshot(self) -> str:
        return f'- heading "{self.heading}"'

    async def screenshot(self) -> Frame:
        return Frame.from_png(solid_png(4, 4, (1, 1, 1)), FakeClock().now())

    async def checkpoint(self) -> BrowserCheckpoint:
        return BrowserCheckpoint(url=self.current, state="{}")

    async def restore(self, checkpoint: BrowserCheckpoint) -> None:
        self.restored.append(checkpoint.url)
        self.current, self.heading = checkpoint.url, None


async def _surface(
    tmp_path: Path, page: _Page | None = None
) -> tuple[ExoskeletonSurface, FakeScreen, InMemoryRecordingStore]:
    screen = FakeScreen(_SIZE)
    clock = FakeClock()

    def react(event: InputEvent) -> None:  # A click on the button turns it green.
        if event.kind is InputKind.CLICK:
            screen.paint(_BUTTON, (0, 200, 0))

    peripherals = Peripherals(
        compound_eye=FakeCompoundEye(screen, clock),
        antennae=FakeAntennae(_SIZE, on_input=react),
        buzz=FakeBuzz(FakeSession(tmp_path, clock)),
        browser=page,  # type: ignore[arg-type]  # _Page is a structural stand-in.
    )
    store = InMemoryRecordingStore()
    recorder = FlightRecorder(
        store,
        RecordingInfo(recording_id="r1", cell_id="c", clearance="C1", started_at=clock.now()),
        clock,
    )
    await recorder.open()
    return ExoskeletonSurface(peripherals, clock, recorder, settle_s=0.0), screen, store


def _proposal(*steps: GuiStep, pc_kind: PostconditionKind, **pc: str) -> Proposal:
    action = make_action(ActionKind.GUI, gui=steps, steps=())
    return make_proposal(action=action, postconditions=(make_postcondition(pc_kind, **pc),))


async def _walk(surface: ExoskeletonSurface, proposal: Proposal) -> tuple[bool, bool]:
    await surface.before(proposal)
    applied = (await surface.apply(proposal)).succeeded
    held = (await surface.check(proposal, 0, proposal.postconditions[0])).has_held
    return applied, held


async def test_a_desktop_click_that_changes_its_region_holds(tmp_path: Path) -> None:
    surface, _, store = await _surface(tmp_path)
    await store.open(
        RecordingInfo(recording_id="r1", cell_id="c", clearance="C1", started_at=FakeClock().now())
    )
    proposal = _proposal(
        GuiStep(op=GuiOp.CLICK, x=20, y=15),
        pc_kind=PostconditionKind.REGION_CHANGED,
        subject=_BUTTON.spec(),
    )

    applied, held = await _walk(surface, proposal)
    await surface.finish(proposal.model_copy(update={"state": ProposalState.VERIFIED}), None)

    assert (applied, held) == (True, True)
    (action,) = await store.actions("r1")
    assert action.before.frame is not None and action.after is not None
    assert action.postconditions[0].has_held is True


async def test_a_click_elsewhere_leaves_the_region_unchanged_and_fails(tmp_path: Path) -> None:
    surface, screen, _ = await _surface(tmp_path)
    screen.paint(_BUTTON, (0, 200, 0))  # Already green: the click changes nothing there.
    proposal = _proposal(
        GuiStep(op=GuiOp.CLICK, x=150, y=80),
        pc_kind=PostconditionKind.REGION_CHANGED,
        subject=_BUTTON.spec(),
    )

    assert await _walk(surface, proposal) == (True, False)


async def test_browser_steps_and_structural_postconditions(tmp_path: Path) -> None:
    page = _Page()
    surface, _, _ = await _surface(tmp_path, page)
    log_in = GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Log in"))
    by_url = _proposal(
        log_in,
        pc_kind=PostconditionKind.URL_MATCHES,
        subject="page",
        expected="file:///site/welcome*",
    )
    by_text = _proposal(
        log_in, pc_kind=PostconditionKind.ELEMENT_TEXT, subject="role=heading", expected="Welcome"
    )

    assert await _walk(surface, by_url) == (True, True)
    assert await _walk(surface, by_text) == (True, True)


async def test_a_wrong_click_fails_its_step_and_restore_puts_the_page_back(tmp_path: Path) -> None:
    page = _Page()
    surface, _, _ = await _surface(tmp_path, page)
    wrong = GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Sign up"))
    proposal = _proposal(
        wrong,
        pc_kind=PostconditionKind.URL_MATCHES,
        subject="page",
        expected="file:///site/welcome*",
    )

    await surface.before(proposal)
    result = await surface.apply(proposal)
    restored = await surface.restore(proposal)
    await surface.finish(
        proposal.model_copy(update={"state": ProposalState.ROLLED_BACK}), RollbackMethod.GUI_STATE
    )

    assert not result.succeeded
    assert result.failure_reason is not None and "no element matches" in result.failure_reason
    assert restored and page.restored == ["file:///site/login.html"]


async def test_a_step_whose_peripheral_is_missing_fails_cleanly(tmp_path: Path) -> None:
    surface, _, _ = await _surface(tmp_path)  # No browser attached.
    proposal = _proposal(
        GuiStep(op=GuiOp.NAVIGATE, url="about:blank"),
        pc_kind=PostconditionKind.URL_MATCHES,
        subject="page",
        expected="about:blank",
    )

    await surface.before(proposal)
    result = await surface.apply(proposal)

    assert not result.succeeded
    assert "no browser is attached" in (result.failure_reason or "")
    assert not await surface.restore(proposal)  # Nothing to put back without a browser.


async def test_evidence_is_what_was_recorded_and_only_the_latest_are_kept(tmp_path: Path) -> None:
    surface, screen, _ = await _surface(tmp_path)
    proposals = [
        _proposal(
            GuiStep(op=GuiOp.CLICK, x=20, y=15),
            pc_kind=PostconditionKind.REGION_CHANGED,
            subject=_BUTTON.spec(),
        )
        for _ in range(KEPT_FOR_REVIEW + 1)
    ]
    assert await surface.evidence(proposals[0]) is None  # Nothing recorded yet.

    for proposal in proposals:
        screen.clear()  # So every click turns the button green again.
        await _walk(surface, proposal)
        await surface.finish(proposal.model_copy(update={"state": ProposalState.VERIFIED}), None)

    latest = await surface.evidence(proposals[-1])
    assert latest is not None and len(latest.frames) == 2  # The before and after screens.
    assert "REGION_CHANGED" in latest.text and ": held" in latest.text
    assert await surface.evidence(proposals[0]) is None  # Evicted: the store still has it.
