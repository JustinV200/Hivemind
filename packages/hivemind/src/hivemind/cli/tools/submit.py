"""Submit `hive tools request` to a Hive it builds and runs: the real `RequestSubmitter`.

`HiveSubmitter` is what `in_process_submitter` returns. On `submit` it builds a Hive from the
manifest (`build_hive`, off the event loop, as every composition root must), runs it (`run_hive`:
the Queen ticking, the Hive Stand's Warden leased) and hands the request to the Queen the way the
Hive Stand's Warden would relay a Worker's: as the Warden's own `tool.request`, from its own
link, naming its own Cell. The Queen validates it at the door, scaffolds, proves, promotes and
announces exactly as for a Worker's request; nothing here reimplements any of that. The answer
comes back on the Warden's link, so the Warden's link is wrapped (`_ReplyTap`) to read the one
frame that answers this request, a `tool.promoted` or a `control.error`, while every other frame
(blob offers, the announcement itself) still reaches the real Warden, which installs the tool.
Waiting is bounded by `[royal_jelly] request_timeout_s`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Used by
    `hivemind.cli.tools.request`. Calls into `hivemind.cli.compose` (build and run a Hive),
    `hivemind.queen` (`WardenLink`, the inbox item) and `hivemind.queen.ticks.tools` (the Queen's
    door for tool messages), waggle and typer.

Key invariants:
    - Only a hive-scope request is submitted: a cell-scope tool is built by the Warden of its own
      Cell for a Worker's request, and the Queen refuses any other source of one.
    - The request is the Hive Stand Warden's own (its id, its Cell); nothing of the operator's
      authority is added beyond what that Warden holds.
    - The Hive is started once and stopped on the way out, whatever the outcome.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for the scopes and who promotes.
    - hivemind.queen.ticks.tools.requests for the door this request enters.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import typer

from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.cli.tools.context import EXIT_BAD_INPUT
from hivemind.llm import Responder
from hivemind.manifest import HiveManifest
from hivemind.queen import to_inbox_item
from hivemind.queen.deps import WardenLink
from hivemind.queen.ticks.tools import handle_tool_item
from waggle.clock import Clock, SystemClock
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import MessageId
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest, ToolScope

CELL_SCOPE_MESSAGE = (
    "a cell-scope tool is built by the Warden of its own Cell for a Worker's request; this "
    "command can request a hive-scope tool"
)

__all__ = ["CELL_SCOPE_MESSAGE", "HiveSubmitter"]


@dataclass(frozen=True, slots=True)
class _ReplyTap(WardenLink):
    """The Hive Stand Warden's link, reading the one answer to the request it was told about.

    A `tool.promoted` or `control.error` correlated to `asked` resolves `answer`; the promotion is
    still sent on to the real Warden (it installs the tool), an error is not (the Warden would only
    log that nobody asked for it).
    """

    answer: asyncio.Future[ToolPromoted | ErrorMessage] | None = None
    asked: MessageId | None = None

    async def send(self, envelope: Envelope) -> bool:
        """Capture the answer to the request, then send the frame on unless it was an error."""
        payload = envelope.payload
        waiting = self.answer is not None and not self.answer.done()
        if waiting and envelope.correlation_id == self.asked:
            if isinstance(payload, ErrorMessage) and self.answer is not None:
                self.answer.set_result(payload)
                return True
            if isinstance(payload, ToolPromoted) and self.answer is not None:
                self.answer.set_result(payload)
        return await WardenLink.send(self, envelope)


class HiveSubmitter:
    """Build and run a Hive from a manifest, and hand it one `tool.request` (`RequestSubmitter`)."""

    def __init__(
        self,
        manifest: HiveManifest,
        *,
        environ: Mapping[str, str],
        clock: Clock | None = None,
        responders: Mapping[str, Responder] | None = None,
    ) -> None:
        """Remember how to build the Hive; nothing is built until `submit`.

        Args:
            manifest: The loaded Hive Manifest.
            environ: The environment provider keys are read from (the composition root's own).
            clock: The Hive's time source; the system clock when omitted.
            responders: Installed on every `kind = "fake"` provider (a test's scripted models).
        """
        self._manifest = manifest
        self._environ = environ
        self._clock = clock if clock is not None else SystemClock()
        self._responders = responders

    async def submit(self, request: ToolRequest) -> ToolPromoted | None:
        """Run the request through a Hive's Queen and report how it ended.

        Args:
            request: A request that already passed `check_request`.

        Returns:
            The promotion announcement; None, with the reason on stderr, when the Queen refused
            the request, the Comb rejected the tool or no verdict came within
            `[royal_jelly] request_timeout_s`.

        Raises:
            typer.Exit: Code 2 for a cell-scope request, which this command cannot make.
        """
        if request.scope is not ToolScope.HIVE:
            typer.echo(f"cannot submit {request.name}: {CELL_SCOPE_MESSAGE}", err=True)
            raise typer.Exit(code=EXIT_BAD_INPUT)
        # build_hive runs its own event loops for its stores, so it cannot run on this one.
        hive = await asyncio.to_thread(
            build_hive,
            self._manifest,
            environ=self._environ,
            clock=self._clock,
            responders=self._responders,
        )
        timeout_s = self._manifest.royal_jelly.request_timeout_s
        async with run_hive(hive):
            try:
                # External await: the Queen scaffolds on a model and the Comb runs in a sandbox;
                # a verdict that never comes is reported, not waited for forever.
                async with asyncio.timeout(timeout_s):
                    answer = await _ask(hive, request)
            except TimeoutError:
                typer.echo(f"no verdict on {request.name} within {timeout_s:g}s", err=True)
                return None
        if isinstance(answer, ErrorMessage):
            typer.echo(f"{answer.code}: {answer.message}", err=True)
            return None
        return answer


async def _ask(hive: Hive, request: ToolRequest) -> ToolPromoted | ErrorMessage:
    """Deliver `request` to the Queen as the Hive Stand Warden's own and await her answer."""
    link = hive.warden_link
    # The door requires the request to name the Warden that sent it and that Warden's own Cell.
    own = request.model_copy(update={"warden_id": link.warden_id, "cell_id": link.cell.id})
    hop = Hop(sender=link.warden_id, recipient=link.hop.sender, node_id=link.hop.node_id)
    envelope = wrap(own, hop, clock=hive.clock)
    answer: asyncio.Future[ToolPromoted | ErrorMessage] = asyncio.get_running_loop().create_future()
    tap = _ReplyTap(
        warden_id=link.warden_id,
        cell=link.cell,
        transport=link.transport,
        hop=link.hop,
        live_capacity=link.live_capacity,
        node_id=link.node_id,
        answer=answer,
        asked=MessageId(envelope.id),
    )
    # The Queen's live table with the Hive Stand's link swapped for the tap, so her job answers
    # (and announces) through it.
    wardens = {other.warden_id: other for other in hive.queen.wardens}
    wardens[link.warden_id] = tap
    handled = await handle_tool_item(
        hive.queen_deps, wardens, to_inbox_item(envelope, link.warden_id)
    )
    assert handled  # noqa: S101 - a ToolRequest is always handled by the tool door.
    return await answer
