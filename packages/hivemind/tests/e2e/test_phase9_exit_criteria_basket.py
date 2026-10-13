"""Prove phase 9's fifth exit criterion: a tool reaches a second Cell, and a blob is inert.

Three whole-Hive scenarios over the Basket (the Hive's store of inert, signed, content-addressed
blobs; ADR-0048). A tool promoted at hive scope reaches a Virtual Cell attached after the promotion
as a blob verified by hash: `basket.offered` then `basket.received` on that Cell's Warden's trail,
the bytes resident and hashing to the registry's `package_sha256`. A blob deposited from a Real Cell
(the Hive Stand: borrowed, so labelled C2) is refused by a Night Veil Cell with `CLEARANCE`, on that
Cell's trail and as the Queen's shipment result, while a C0 blob is taken. And a blob with no Comb
entry cannot be invoked: a dataset on the Hive Stand's Basket gives a Worker no tool, a tool it
names is refused, and a promotion whose package is not resident is refused as `tool.load_refused`.

Fits into the Hive:
    Test infrastructure (codingrules section 14), not shipped. Exercises `hivemind.basket`,
    `hivemind.queen.ticks.tools` (announce, ship), `hivemind.wardens.ticks.basket` and `.tools`.

Key invariants:
    - The receiving Warden is the real thing (an in-process in-Cell Warden over a real loopback
      link); only the Cell's container and the models are faked.
    - Shipping is driven by the real Queen: a catch-up on attach, or `ship_blob` by hand.

See Also:
    - tests.e2e.test_phase9_exit_criteria for the other four criteria.
    - tests.e2e.royal_jelly_runs for the scripts; tests.e2e.night_veil_hive for the Night Veil Hive.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.comb_tools import make_comb_package, make_promoted
from builders.virtual_cells import VirtualCellsTuning, virtual_cells_manifest
from e2e.kernel_helpers import (
    assert_kinds_in_order,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
)
from e2e.night_veil_hive import night_veil_hive, request, running
from e2e.royal_jelly_runs import (
    ANSWER_FILE,
    LabScript,
    ProbeTurns,
    append_lab,
    build_lab_hive,
    count_lines_reply,
    install_cell_script,
    lab_manifest,
    pin_host_platform,
    probe_plan,
    result_texts,
    run_lab,
    write_arguments,
)

from hivemind.basket import DepositRequest, MediaKind
from hivemind.cell import CombShieldLevel, HoneyClearance, OsFamily
from hivemind.cli.compose import GoalReport, Hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import WardenLink
from hivemind.queen.ticks.tools.transfer import ShipResult, ship
from hivemind.royal_jelly.spec import sha256_of
from hivemind.wardens import WardenDeps
from hivemind.wardens.ticks.tools import install_tool
from waggle.ids import CellId

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # A shipment is a few loopback round trips; this bounds a hang.
_DATASET = b"station,reading\nalpha,1\nbeta,2\n" * 64
_DATASET_NAME = "dataset_loader"


@dataclass(frozen=True, slots=True)
class _Received:
    """What the Queen sent a Virtual Cell's own Warden, and what that Warden then holds."""

    offered: PheromoneEvent  # The Queen's `basket.offered`, about the second Cell.
    received: PheromoneEvent  # The Cell's own `basket.received`.
    resident: bool  # Whether the promoted package is resident there.
    digest: str  # The sha256 of the resident bytes, recomputed.
    package_sha256: str  # What the registry says the package hashes to.


async def _await_blob(deps: list[WardenDeps], hive: Hive) -> _Received:
    """Wait for the second Cell's Warden to receive the tool's package, then read what it holds."""
    access = hive.royal_jelly
    assert access is not None
    (row,) = await access.registry.list_tools()
    (cell,) = deps  # The Hive Stand ran the first goal; the one Virtual Cell is the second.
    stand = hive.warden_link.cell.id
    link = next(link for link in hive.queen.wardens if link.cell.id != stand)

    async def received() -> bool:
        return bool(await cell.trail.query(TrailQuery(kind="basket.received")))

    await wait_until(received, timeout_s=_TIMEOUT_S)
    resident = cell.resident_basket
    assert resident is not None
    held = await resident.bytes_for(row.package_sha256)
    offers = await hive.stores.trail.query(
        TrailQuery(kind="basket.offered", subject_id=link.cell.id)
    )
    offered = offers[0]
    (arrived,) = await cell.trail.query(TrailQuery(kind="basket.received"))
    return _Received(
        offered=offered,
        received=arrived,
        resident=await resident.is_resident(row.package_sha256),
        digest=sha256_of(held),
        package_sha256=row.package_sha256,
    )


def test_a_hive_tool_reaches_a_second_cell_as_a_basket_blob_verified_by_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5, first half: promote on the Hive Stand, then attach a second Cell."""
    # Arrange: the first goal runs on the Hive Stand and promotes; the second needs a Virtual Cell.
    pin_host_platform(monkeypatch)
    manifest = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    append_lab(manifest, role="drone")
    probes = ProbeTurns("hive")
    script = LabScript(
        probes.turn, [single_task_plan(ANSWER_FILE), probe_plan()], [count_lines_reply()]
    )
    cells: list[WardenDeps] = []
    install_cell_script(monkeypatch, script, cells)
    hive = build_lab_hive(manifest, script)

    async def after(running_hive: Hive, _reports: tuple[GoalReport, ...]) -> _Received:
        return await _await_blob(cells, running_hive)

    # Act
    run = asyncio.run(run_lab(hive, ["Count the lines.", "Probe another Cell."], after=after))

    # Assert: promoted first, then offered and received on the second Cell, bytes hashing true.
    assert all(report.succeeded for report in run.reports)
    assert_kinds_in_order(run.tool_kinds, ["tool.promoted", "tool.announced"])
    received: _Received = run.extra
    assert received.offered.at <= received.received.at
    assert received.offered.payload["sha256"] == received.received.payload["sha256"]
    assert received.resident and received.digest == received.package_sha256
    assert received.package_sha256 == received.received.payload["sha256"]


def test_a_blob_deposited_from_a_real_cell_is_refused_by_a_night_veil_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5, second half: C2 from the Hive Stand is refused; a C0 blob is kept."""

    async def scenario() -> tuple[ShipResult, ShipResult, list[PheromoneEvent], list[bool]]:
        async with night_veil_hive(tmp_path, monkeypatch, hold_after=1) as run, running(run):
            await request(run)
            hive = run.hive
            await wait_until(lambda: _veil_link(hive) is not None, timeout_s=_TIMEOUT_S)
            link = _veil_link(hive)
            assert link is not None and run.in_cell
            access = hive.royal_jelly
            assert access is not None
            stand = hive.warden_link.cell.id
            real = await access.deposits.deposit(_dataset(stand, b"from the laptop", borrowed=True))
            clean = await access.deposits.deposit(
                _dataset(stand, b"a public table", borrowed=False)
            )
            refused = await ship(hive.queen_deps, link, real.blob.sha256, "offered to the veil")
            taken = await ship(hive.queen_deps, link, clean.blob.sha256, "offered to the veil")
            cell = run.in_cell[0]
            events = list(await cell.trail.query(TrailQuery(family="basket")))
            assert cell.resident_basket is not None
            held = [
                await cell.resident_basket.is_resident(real.blob.sha256),
                await cell.resident_basket.is_resident(clean.blob.sha256),
            ]
            assert real.blob.clearance is HoneyClearance.C2
            assert clean.blob.clearance is HoneyClearance.C0
            return refused, taken, events, held

    refused, taken, events, held = asyncio.run(scenario())

    assert (refused, taken) == (ShipResult.REFUSED, ShipResult.SHIPPED)
    (rejection,) = [event for event in events if event.kind == "basket.refused"]
    assert rejection.payload["code"] == "CLEARANCE"
    assert held == [False, True]


def _veil_link(hive: Hive) -> WardenLink | None:
    """The attached Warden link whose Cell is Night Veil, or None until one attaches."""
    return next(
        (
            link
            for link in hive.queen.wardens
            if link.cell.comb_shield is CombShieldLevel.NIGHT_VEIL
        ),
        None,
    )


def _dataset(origin: CellId, content: bytes, *, borrowed: bool) -> DepositRequest:
    """A dataset deposit from `origin`; `borrowed` says it came from a Real Cell (floor C2)."""
    return DepositRequest(
        content=content,
        title="A table of readings",
        summary="Readings a task kept; data only, it runs nothing.",
        media_kind=MediaKind.DATASET,
        media_type="application/octet-stream",
        platform_tags=(OsFamily.LINUX,),
        declared=HoneyClearance.C0,
        origin_cell_id=origin,
        origin_bee=None,
        from_borrowed_cell=borrowed,
        tier=CombShieldLevel.MEADOW,
    )


@dataclass(frozen=True, slots=True)
class _Inert:
    """What a Hive holds after a dataset and a forged promotion: the refusals and the blobs."""

    installed: bool  # Whether the Warden installed a promotion whose package is not resident.
    refusals: list[str]  # The `code` of every `tool.load_refused` on the trail.
    datasets: list[str]  # The hash of every dataset the Basket keeps.


def _dataset_turn(request: LLMRequest) -> LLMResponse:
    """The Worker script: call a tool named after the dataset, write the answer, close."""
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (("use", _DATASET_NAME, {}),))
    if rounds == 1:
        return tool_response(
            request, (("answer", "write_file", write_arguments(ANSWER_FILE, "no")),)
        )
    return text_response(result_texts(request)[0])


def test_a_blob_with_no_comb_entry_cannot_be_invoked_and_a_promotion_without_its_blob_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5, third part: a dataset gives no tool; an absent package is refused."""
    # Arrange: a dataset in the Basket, never promoted; a Worker calls a tool named after it.
    pin_host_platform(monkeypatch)
    script = LabScript(_dataset_turn, single_task_plan(ANSWER_FILE), [count_lines_reply()])
    hive = build_lab_hive(lab_manifest(tmp_path), script)
    absent = make_comb_package(name=_DATASET_NAME)  # A loadable tool whose bytes were never stored.

    async def before(started: Hive) -> None:
        access = started.royal_jelly
        assert access is not None
        await access.deposits.deposit(
            _dataset(started.warden_link.cell.id, _DATASET, borrowed=True)
        )

    async def after(started: Hive, _reports: tuple[GoalReport, ...]) -> _Inert:
        access = started.royal_jelly
        assert access is not None
        installed = await install_tool(started.warden, make_promoted(absent, started.clock))
        refusals = await started.stores.trail.query(TrailQuery(kind="tool.load_refused"))
        kept = await access.basket.list_blobs(media_kind=MediaKind.DATASET)
        return _Inert(
            installed=installed,
            refusals=[str(event.payload["code"]) for event in refusals],
            datasets=[blob.sha256 for blob in kept],
        )

    # Act
    run = asyncio.run(run_lab(hive, ["Use the dataset."], before=before, after=after))

    # Assert: the Worker was told no such tool is offered, nothing was promoted, the forged
    # promotion was refused on the trail, and the dataset is still only data in the Basket.
    (report,) = run.reports
    assert report.succeeded and report.tasks[0].outcome is not None
    assert report.tasks[0].outcome.summary == f"no tool named {_DATASET_NAME!r} was offered."
    assert run.rows == () and "tool.promoted" not in run.tool_kinds
    inert: _Inert = run.extra
    assert inert.installed is False and inert.refusals == ["not_resident"]
    assert inert.datasets == [sha256_of(_DATASET)]
