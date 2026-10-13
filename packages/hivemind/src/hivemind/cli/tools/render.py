"""Print what `hive tools` returns: the registry's rows, one tool's detail, a change, a report.

Output formatting only (codingrules section 2's CLI row): every value printed here was produced
by `hivemind.royal_jelly` -- a `CombToolRow`, a `RegistryChange`, a `QuarantineReport` -- and this
module decides only how it looks on a terminal, or as JSON for `--json`. A tool's description is
text a model wrote; it is printed as reference data and never run (codingrules section 15).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Called by
    `hivemind.cli.tools.listing`, `.lifecycle` and `.quarantine`. Calls into
    `hivemind.royal_jelly` (the shapes it prints) only.

Key invariants:
    - Nothing here reads a store or decides anything; it prints what it is given.
    - Every `--json` document is one JSON value on stdout.
    - A package hash is shown in short form in a table and in full in a detail view.

See Also:
    - hivemind.royal_jelly.registry.rows for CombToolRow and RegistryChange.
    - hivemind.royal_jelly.spec.report for QuarantineReport.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import typer

from hivemind.royal_jelly import ChangeKind, CombToolRow, QuarantineReport, RegistryChange

SHORT_HASH_CHARS = 12  # Enough of a sha256 to tell packages apart in a table.
_NONE = "-"  # A column with nothing to say.
_DETAIL_LINES = 20  # The most lines of a failed gate's detail (a traceback tail) printed.
_HEADERS = ("NAME", "VERSION", "SCOPE", "CELL", "STATE", "CURRENT", "PACKAGE", "PROMOTED AT")

__all__ = [
    "SHORT_HASH_CHARS",
    "print_change",
    "print_report",
    "print_row_detail",
    "print_rows",
    "row_detail_json",
    "rows_json",
]


def print_rows(rows: Sequence[CombToolRow]) -> None:
    """Print the registry's rows as an aligned table, or say there are none.

    Args:
        rows: The rows to show, in the registry's order.
    """
    # Nothing promoted yet: say so rather than print a bare header.
    if not rows:
        typer.echo("no tools in the Comb Registry")
        return
    body = [_row_cells(row) for row in rows]
    widths = [max(len(cell) for cell in column) for column in zip(_HEADERS, *body, strict=True)]
    for cells in (_HEADERS, *body):
        typer.echo(
            "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)).rstrip()
        )


def rows_json(rows: Sequence[CombToolRow]) -> str:
    """Return the rows as one JSON object: each row's table fields, hash in full.

    Args:
        rows: The rows to show.

    Returns:
        `{"tools": [...]}`, one object per row.
    """
    return json.dumps({"tools": [_row_fields(row) for row in rows]}, indent=2)


def print_row_detail(row: CombToolRow) -> None:
    """Print one version in full: its spec, its report's summary, its hash and size.

    Args:
        row: The stored version to describe.
    """
    spec = row.spec
    for name, value in _row_fields(row, full_hash=True).items():
        typer.echo(f"{name}: {value}")
    typer.echo(f"description: {spec.description}")
    typer.echo(f"entry point: {spec.entry_point}")
    typer.echo(f"input schema: {spec.input_schema_json}")
    typer.echo(f"capabilities: {', '.join(spec.required_capabilities) or _NONE}")
    typer.echo(f"dependencies: {', '.join(spec.dependencies) or _NONE}")
    typer.echo(f"supported os: {', '.join(os.value for os in spec.supported_os)}")
    typer.echo(f"report: {_report_summary(row.report)}")


def row_detail_json(row: CombToolRow) -> str:
    """Return one version as JSON: the table fields, its spec and its report's summary.

    Args:
        row: The stored version to describe.

    Returns:
        One JSON object; the spec and report are the stored documents, parsed.
    """
    document = {
        **_row_fields(row, full_hash=True),
        "spec": json.loads(row.spec_json),
        "report": json.loads(row.report_json),
    }
    return json.dumps(document, indent=2)


def print_change(change: RegistryChange) -> None:
    """Print what a registry write did: the version, who did it and why.

    Args:
        change: The change the registry returned.
    """
    row = change.row
    where = f"{row.scope.value} {row.cell_id}" if row.cell_id is not None else row.scope.value
    typer.echo(f"{_VERB[change.kind]} {row.name} {row.version} ({where}) as {change.by}")
    if change.supersedes_version is not None:
        typer.echo(f"  displaced version: {change.supersedes_version}")
    typer.echo(f"  reason: {change.reason}")


def print_report(report: QuarantineReport) -> None:
    """Print a Quarantine Comb report: the verdict, each gate in order, then what it spent.

    Args:
        report: The Comb's report for one package.
    """
    verdict = "PASSED" if report.passed else "FAILED"
    typer.echo(
        f"quarantine {report.name} {report.version}: {verdict} "
        f"(package {report.package_sha256[:SHORT_HASH_CHARS]}, {report.package_size_bytes} bytes)"
    )
    for gate in report.gates:
        mark = "pass" if gate.passed else "FAIL"
        typer.echo(f"  {gate.name:<12} {mark}" + (f"  {gate.reason}" if gate.reason else ""))
        # The failing gate's detail (a test traceback's tail) is what the dev loop needs next.
        if not gate.passed and gate.detail and gate.detail.strip() != gate.reason.strip():
            for line in gate.detail.splitlines()[-_DETAIL_LINES:]:
                typer.echo(f"      {line}")
    usage = report.usage
    typer.echo(
        f"usage: cpu {usage.cpu_s:.2f} s, peak memory {usage.peak_rss_bytes / 1_048_576:.1f} MiB, "
        f"wall {usage.wall_s:.2f} s, output {usage.output_bytes} bytes"
    )


_VERB = {
    ChangeKind.PROMOTED: "promoted",
    ChangeKind.ROLLED_BACK: "rolled back to",
    ChangeKind.RETIRED: "retired",
}


def _row_cells(row: CombToolRow) -> tuple[str, ...]:
    """Return one row's table cells, in `_HEADERS` order."""
    return (
        row.name,
        row.version,
        row.scope.value,
        row.cell_id or _NONE,
        row.state.value,
        "yes" if row.is_current else "no",
        row.package_sha256[:SHORT_HASH_CHARS],
        row.promoted_at.isoformat(timespec="seconds"),
    )


def _row_fields(row: CombToolRow, *, full_hash: bool = False) -> dict[str, str | int | bool | None]:
    """Return the facts of one row as ordered name/value pairs, shared by detail and JSON."""
    return {
        "name": row.name,
        "version": row.version,
        "scope": row.scope.value,
        "cell": row.cell_id,
        "state": row.state.value,
        "current": row.is_current,
        "tool_id": row.tool_id,
        "package_sha256": row.package_sha256
        if full_hash
        else row.package_sha256[:SHORT_HASH_CHARS],
        "package_size": row.package_size,
        "quarantine_path": row.quarantine_path.value,
        "reviewed": row.reviewed,
        "promoted_by": row.promoted_by,
        "promoted_at": row.promoted_at.isoformat(timespec="seconds"),
        "supersedes_version": row.supersedes_version,
        "retired_at": row.retired_at.isoformat(timespec="seconds") if row.retired_at else None,
        "retired_reason": row.retired_reason,
    }


def _report_summary(report: QuarantineReport) -> str:
    """Summarise a stored report in a line: path, gates passed and the failing gate if any."""
    passed = sum(1 for gate in report.gates if gate.passed)
    failing = report.failing_gate or _NONE
    gates = f"{passed}/{len(report.gates)} gates passed"
    return f"path {report.path.value}, {gates}, failing gate {failing}"
