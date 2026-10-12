"""Provide `hive tools request`: ask the Royal Jelly Lab for a tool, as the operator.

`request NAME --description TEXT [--capability CAP ...] [--cell ID]` builds the same `ToolRequest`
(`tool.request`) a Worker's Warden would send for the Hive Stand's platform: this machine's
operating system, architecture and Python, probed the way the Hive Stand probes itself. It then
checks the request the way the Lab will, with `validate_spec` against the operator's own
capability set (the Guard policy's `operator` role, the root every set is cut from): a capability
outside it, or a `tool:` capability (a tool never grants itself other tools), is refused here,
exit 1, with every reason. `--cell ID` makes the tool CELL scope for that Cell; the platform is
still this machine's, because a command line cannot see another Cell's.

Submitting is a seam. `RequestSubmitter` is the one thing the command hands a valid request to;
`in_process_submitter(manifest)` builds the default, `HiveSubmitter` (`hivemind.cli.tools.submit`):
it builds and runs a Hive from the manifest and hands the request to the Queen as the Hive Stand
Warden's own, where the Royal Jelly flow (scaffold, quarantine, promote) runs as for any Worker's
request. A test passes its scripted models through `in_process_submitter`'s `responders`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Mounted by
    `hivemind.cli.tools` as `request`. Calls into `hivemind.cell` (the Hive Stand's identity and
    probe), `hivemind.cli.compose.guard` (the policy), `hivemind.guard`, `hivemind.royal_jelly`
    and this group's own `context` only.

Key invariants:
    - A request that fails validation is never submitted.
    - The command submits through `RequestSubmitter` only; nothing here scaffolds, quarantines or
      promotes.
    - The Warden id on the request is a placeholder: the submitter replaces it with the Hive
      Stand Warden's own, since the Queen accepts a request only from the Warden it names.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for validate_spec and the scopes.
    - waggle.messages.tool.authoring for ToolRequest and ToolPromoted.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Protocol

import typer

from hivemind.cell import ProbeError, hive_stand_cell_id
from hivemind.cell.local import HiveStandConfig, probe_host
from hivemind.cli.compose.guard import guard_policy
from hivemind.cli.tools.context import (
    EXIT_REFUSED,
    CellOption,
    cell_id_of,
    cli_context,
    input_or_exit,
    run_or_exit,
)
from hivemind.cli.tools.submit import HiveSubmitter
from hivemind.guard import CapabilitySet, role_set
from hivemind.llm import Responder
from hivemind.manifest import HiveManifest
from hivemind.royal_jelly import SpecValidation, ToolSpec, validate_spec
from waggle.clock import SystemClock
from waggle.ids import new_tool_id, new_warden_id
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest, ToolScope

__all__ = [
    "RequestSubmitter",
    "build_request",
    "check_request",
    "in_process_submitter",
    "request_command",
]

OPERATOR_ROLE = "operator"  # The Guard policy role of the human: the root every set is cut from.
_REASON = "Requested by the operator with `hive tools request`."
# The request names no schema or version (the scaffolder writes both), so the check borrows the
# smallest spec that satisfies validate_spec's structural rules and leaves the rest to the Lab.
_PROVISIONAL_VERSION = "0.1.0"
_CLOSED_OBJECT_SCHEMA = '{"type": "object", "additionalProperties": false}'
_HASH_CHARS = 12


class RequestSubmitter(Protocol):
    """Hand a valid `tool.request` to the Hive's promotion flow."""

    async def submit(self, request: ToolRequest) -> ToolPromoted | None:
        """Run the request through the Lab and report the outcome.

        Args:
            request: A request that already passed `check_request`.

        Returns:
            The promotion announcement when the tool was promoted; None when the flow ended
            without one (the Comb rejected it, or the retry budget ran out).
        """
        ...


def in_process_submitter(
    manifest: HiveManifest,
    *,
    environ: Mapping[str, str] | None = None,
    responders: Mapping[str, Responder] | None = None,
) -> RequestSubmitter:
    """Return the submitter `hive tools request` hands its request to.

    Args:
        manifest: The loaded Hive Manifest the Hive is built from.
        environ: The environment provider keys are read from; this process's own when omitted
            (the one place this command reads it, as `hive run` does).
        responders: Scripted models installed on every `kind = "fake"` provider (tests only).

    Returns:
        A `HiveSubmitter`: it builds and runs the Hive when asked to submit, never before.
    """
    return HiveSubmitter(
        manifest, environ=environ if environ is not None else os.environ, responders=responders
    )


def request_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="The tool's name (a-z, 0-9 and _, starting a-z).")],
    description: Annotated[str, typer.Option("--description", help="What the tool must do.")],
    capability: Annotated[
        list[str] | None,
        typer.Option("--capability", help="A capability the tool needs; repeat for several."),
    ] = None,
    cell: CellOption = None,
) -> None:
    """Ask the Royal Jelly Lab for a tool for this machine, checked against the operator's set."""
    cli_ctx = cli_context(ctx)
    manifest = cli_ctx.manifest
    request = _build_or_exit(manifest, name, description, tuple(capability or ()), cell)
    validation = check_request(request, role_set(guard_policy(manifest), OPERATOR_ROLE))
    # Refused by the same rules the Lab applies: nothing is submitted, and every reason is shown.
    if not validation.ok:
        for reason in validation.reasons:
            typer.echo(f"refused: {reason}", err=True)
        raise typer.Exit(code=EXIT_REFUSED)
    where = f"{request.scope.value} {request.cell_id}"
    typer.echo(f"requesting {request.name} ({where}) on {request.platform.os.value}")
    promoted = run_or_exit(in_process_submitter(manifest).submit(request))
    if promoted is None:
        typer.echo(f"{request.name} was not promoted", err=True)
        raise typer.Exit(code=EXIT_REFUSED)
    typer.echo(
        f"promoted {promoted.name} {promoted.version} ({promoted.scope.value}); "
        f"package {promoted.package_sha256[:_HASH_CHARS]}"
    )


def build_request(
    manifest: HiveManifest,
    name: str,
    description: str,
    capabilities: tuple[str, ...],
    cell: str | None,
) -> ToolRequest:
    """Build the `tool.request` the Hive Stand would send for this machine's platform.

    Args:
        manifest: The loaded Hive Manifest; `[hive]` names the node, `[hive_stand]` the probe.
        name: The desired tool name.
        description: What the tool must do: the scaffolder's brief.
        capabilities: Capability strings the tool will need.
        cell: A Cell id for a CELL-scope tool; None asks for a HIVE-scope tool for the Hive Stand.

    Returns:
        A validated request.

    Raises:
        typer.Exit: Code 2 when `cell` is not a Cell id.
        ProbeError: This machine's cores could not be determined.
        ValueError: A field is out of bounds (pydantic's ValidationError).
    """
    clock = SystemClock()
    manifest_dir = manifest.source_path.parent if manifest.source_path is not None else Path.cwd()
    config = HiveStandConfig.from_section(manifest.hive_stand, manifest_dir)
    # The platform is the Hive Stand's own: the one machine this command line can see.
    platform, _ = probe_host(config).capabilities.to_wire()
    cell_id = cell_id_of(cell)
    return ToolRequest(
        tool_id=new_tool_id(clock),
        warden_id=new_warden_id(clock),
        worker_id=None,
        task_id=None,
        cell_id=cell_id if cell_id is not None else hive_stand_cell_id(manifest.hive.node_id),
        scope=ToolScope.HIVE if cell_id is None else ToolScope.CELL,
        name=name,
        description=description,
        required_capabilities=capabilities,
        platform=platform,
        reason=_REASON,
    )


def check_request(request: ToolRequest, operator: CapabilitySet) -> SpecValidation:
    """Check a request the way the Lab will: `validate_spec` against the operator's set.

    Args:
        request: The request to check.
        operator: The capabilities the operator holds (the Guard policy's `operator` role).

    Returns:
        Every reason the request would be refused; none when it is acceptable.
    """
    # The request is a spec without its schema and version; those two are the scaffolder's to
    # write, so a provisional closed-object schema stands in and the rules about capabilities,
    # scope and the Cell are checked exactly as they will be on the real spec.
    spec = ToolSpec(
        tool_id=request.tool_id,
        requested_by=request.warden_id,
        name=request.name,
        version=_PROVISIONAL_VERSION,
        description=request.description,
        input_schema_json=_CLOSED_OBJECT_SCHEMA,
        required_capabilities=request.required_capabilities,
        supported_os=(request.platform.os,),
        scope=request.scope,
        cell_id=request.cell_id if request.scope is ToolScope.CELL else None,
    )
    return validate_spec(spec, operator)


def _build_or_exit(
    manifest: HiveManifest,
    name: str,
    description: str,
    capabilities: tuple[str, ...],
    cell: str | None,
) -> ToolRequest:
    """Build the request, turning a probe failure or an out-of-bounds field into an exit."""
    try:
        return input_or_exit(lambda: build_request(manifest, name, description, capabilities, cell))
    except ProbeError as exc:
        typer.echo(f"{exc.code}: {exc}", err=True)
        raise typer.Exit(code=EXIT_REFUSED) from exc
