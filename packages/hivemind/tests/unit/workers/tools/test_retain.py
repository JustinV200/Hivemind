"""Tests for hivemind.workers.tools.comb.retain: keeping a scratch file for the Basket.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/comb/retain.py (codingrules section 3). The Capping gate is
    the real one `make_context` wires, over a `FakeSession`; the sink is a recording fake.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.comb.retain for the module under test.
    - tests/unit/workers/tools/test_keep.py for the COPY proposal this follows.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import pytest
from builders.comb_tools import RecordingRetainSink
from builders.workers import make_assignment, make_context, proposals_of, run_tool

from hivemind.llm import JsonObject
from hivemind.supervision.capping import GateOutcome, Proposal, ProposalState
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import build_registry
from hivemind.workers.tools.comb.retain import RetainDeposit, retain
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput
from waggle.messages.capping import ActionKind

# The module itself, for monkeypatching: the package face re-exports the function `retain` under
# the module's own name, so an attribute import would find the function instead.
retain_module = importlib.import_module("hivemind.workers.tools.comb.retain")

_BYTES = b"PK\x03\x04 a pretend wheel"
_ARGS: JsonObject = {
    "path": "build/pkg.whl",
    "title": "The pkg wheel",
    "summary": "A wheel of pkg built for Python 3.12, useful for offline installs.",
    "media_kind": "wheel",
}


async def _ctx_with_file(
    path: str = "build/pkg.whl", data: bytes = _BYTES
) -> tuple[WorkerContext, RecordingRetainSink]:
    sink = RecordingRetainSink()
    ctx = make_context(retain_sink=sink)
    await ctx.session.put_file(Path(path), data)
    return ctx, sink


async def test_a_verified_proposal_hands_the_warden_a_deposit_with_the_digest() -> None:
    ctx, sink = await _ctx_with_file()
    digest = hashlib.sha256(_BYTES).hexdigest()

    output = await run_tool(ctx, "retain", _ARGS)

    assert output.is_error is False and digest in output.text
    (deposit,) = sink.deposits
    assert deposit == RetainDeposit(
        path=f"retained/{digest[:16]}-pkg.whl",
        title="The pkg wheel",
        summary=_ARGS["summary"],
        media_kind="wheel",
        media_type="application/octet-stream",
        sha256=digest,
        size_bytes=len(_BYTES),
    )


async def test_the_file_moves_to_staging_and_the_warden_can_read_it_there() -> None:
    ctx, sink = await _ctx_with_file()

    await run_tool(ctx, "retain", _ARGS)

    staged = await ctx.session.get_file(Path(sink.deposits[0].path))
    assert staged == _BYTES
    with pytest.raises(FileNotFoundError):
        await ctx.session.get_file(Path("build/pkg.whl"))


async def test_the_proposal_is_a_copy_by_digest_and_never_carries_the_bytes() -> None:
    ctx, _sink = await _ctx_with_file()
    digest = hashlib.sha256(_BYTES).hexdigest()

    await run_tool(ctx, "retain", _ARGS)

    (proposal,) = await proposals_of(ctx)
    action = proposal.action
    assert action.kind is ActionKind.COPY
    assert (action.copy_sha256, action.copy_size) == (digest, len(_BYTES))
    assert action.paths[0] == "build/pkg.whl" and action.paths[1].startswith("retained/")
    assert action.diff is None and _BYTES.decode("latin-1") not in proposal.model_dump_json()


async def test_the_media_type_is_guessed_from_the_file_name() -> None:
    ctx, sink = await _ctx_with_file("bundle.zip")

    await run_tool(ctx, "retain", {**_ARGS, "path": "bundle.zip", "media_kind": "archive"})

    assert sink.deposits[0].media_type == "application/zip"


@pytest.mark.parametrize("path", ["../outside.bin", "/etc/hostname", "scratch/../../x"])
async def test_a_path_outside_scratch_is_refused_unproposed(path: str) -> None:
    ctx, sink = await _ctx_with_file()

    output = await run_tool(ctx, "retain", {**_ARGS, "path": path})

    assert output.is_error is True and "inside this Worker's scratch" in output.text
    assert sink.deposits == [] and await proposals_of(ctx) == []


async def test_a_missing_file_is_a_readable_error() -> None:
    ctx, sink = await _ctx_with_file()

    output = await run_tool(ctx, "retain", {**_ARGS, "path": "nothing.bin"})

    assert output.is_error is True and output.text == "no file at 'nothing.bin'."
    assert sink.deposits == []


async def test_a_file_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, sink = await _ctx_with_file()
    monkeypatch.setattr(retain_module, "MAX_RETAIN_BYTES", 4)

    output = await run_tool(ctx, "retain", _ARGS)

    assert output.is_error is True and "at most 4" in output.text
    assert sink.deposits == [] and await proposals_of(ctx) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"media_kind": "Wheel File"}, "lowercase tag"),
        ({"title": ""}, "non-empty"),
        ({"summary": "s" * 5_000}, "at most"),
        ({"title": "t" * 5_000}, "at most"),
    ],
)
async def test_malformed_arguments_are_refused_before_any_read(
    overrides: JsonObject, fragment: str
) -> None:
    ctx, sink = await _ctx_with_file()
    invocation = ToolInvocation(ctx, make_assignment(ctx.clock))

    output = await retain(invocation, {**_ARGS, **overrides})

    assert isinstance(output, ToolOutput)
    assert output.is_error is True and fragment in output.text
    assert sink.deposits == []


async def test_a_proposal_the_gate_does_not_verify_deposits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, sink = await _ctx_with_file()

    async def rejecting(invocation: ToolInvocation, proposal: Proposal) -> GateOutcome:
        return GateOutcome(
            proposal_id=proposal.id,
            state=ProposalState.REJECTED,
            checks=(),
            postconditions=(),
            reason="a check refused it",
        )

    monkeypatch.setattr(retain_module, "cap", rejecting)

    output = await run_tool(ctx, "retain", _ARGS)

    assert output.is_error is True and "REJECTED" in output.text
    assert sink.deposits == []


def test_the_tool_is_offered_only_when_the_warden_wired_a_sink() -> None:
    with_sink = make_context(retain_sink=RecordingRetainSink())
    without = make_context()

    assert "retain" in [d.name for d in build_registry(with_sink).definitions()]
    assert "retain" not in [d.name for d in build_registry(without).definitions()]


async def test_retain_without_a_sink_says_the_basket_is_unreachable() -> None:
    ctx = make_context()

    output = await retain(ToolInvocation(ctx, make_assignment(ctx.clock)), _ARGS)

    assert isinstance(output, ToolOutput) and "not reachable" in output.text
