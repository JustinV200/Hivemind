"""Provide `hive doctor`: check this machine and its Hive Manifest before the Hive runs.

Roadmap 14.2. With no option it loads `--manifest` (exit 2 when that fails, which is itself the
first check) and runs every check in the order a newcomer would fix them: the Hive Stand's scratch
root, Docker when the Virtual Cell backend is Docker, every configured provider reachable, every
slot's model present on its server, Forage sanity, sqlite-vec, and the escalation policy file. Each
prints PASSED, WARNED, FAILED or SKIPPED with one line of why, and the command exits 1 only when
something FAILED. `--supersedure NODE` instead runs roadmap 13.2a's candidate test as the console
over the running serve's loopback listener (`hivemind.cli.supersede`), which is why the command
carries the console's `--manifest` and `--password-stdin` options.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.doctor`. Registered as `hive doctor` by
    `hivemind.cli.app`. Calls into this package's check modules, `hivemind.cli.supersede`, the
    console's carried options and `hivemind.cli.stores` (the manifest and the registry).

Key invariants:
    - Read-only: nothing on the Hive or the host changes, and no model is asked to generate.
    - The registry's connections are closed in the loop that opened them.
    - The seams a test replaces (the Docker probe, the environment) arrive on `ctx.obj` as a
      `DoctorDeps`; production uses the defaults.

See Also:
    - hivemind.cli.doctor.report for the outcomes.
    - hivemind.cli.supersede.doctor for the `--supersedure` path.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated

import typer

from hivemind.cli.doctor.config import check_escalation_policy, check_forage
from hivemind.cli.doctor.host import (
    DockerProbe,
    check_docker,
    check_scratch_root,
    check_vector_extension,
    probe_docker,
)
from hivemind.cli.doctor.models import check_providers, check_slot_models
from hivemind.cli.doctor.report import Check, Outcome, print_checks, verdict
from hivemind.cli.entrance.console import MANIFEST
from hivemind.cli.landing import carried_path
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    build_registry,
    closing_registry,
    load_manifest_or_exit,
)
from hivemind.cli.supersede import supersedure_doctor
from hivemind.manifest import HiveManifest
from waggle.clock import SystemClock

SupersedureOption = Annotated[
    str | None,
    typer.Option("--supersedure", help="Run the Supersedure candidate test for this node instead."),
]

__all__ = ["DoctorDeps", "doctor_command", "run_checks"]


@dataclass(frozen=True, slots=True)
class DoctorDeps:
    """The seams `hive doctor` reaches the outside through; a test passes its own on `ctx.obj`.

    Attributes:
        docker_probe: Raises when the Docker daemon cannot be reached.
        environ: Where provider API keys are read from.
    """

    docker_probe: DockerProbe = probe_docker
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))


def doctor_command(
    ctx: typer.Context, supersedure: SupersedureOption = None, as_json: JsonOption = False
) -> None:
    """Check the Hive Manifest and this machine; or, with --supersedure NODE, a candidate."""
    if supersedure is not None:
        supersedure_doctor(ctx, supersedure, as_json)
        return
    path = carried_path(ctx, MANIFEST) or DEFAULT_MANIFEST
    manifest = load_manifest_or_exit(path)
    deps = ctx.obj if isinstance(ctx.obj, DoctorDeps) else DoctorDeps()
    checks = asyncio.run(run_checks(manifest, deps))
    passed = verdict(checks)
    if as_json:
        payload = {"passed": passed, "checks": [check.model_dump(mode="json") for check in checks]}
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"hive doctor: {path}")
        print_checks(checks)
        typer.echo(_summary(checks))
    if not passed:
        raise typer.Exit(code=1)


async def run_checks(manifest: HiveManifest, deps: DoctorDeps) -> tuple[Check, ...]:
    """Run every check in fixing order and return the results.

    Args:
        manifest: The loaded manifest.
        deps: The Docker probe and the environment.

    Returns:
        One `Check` per host check, per provider and per `[llm.slots]` row.
    """
    registry = build_registry(manifest, deps.environ, SystemClock())

    async def model_checks() -> tuple[Check, ...]:
        """Probe the providers, then the models on them, through one registry."""
        reachable = await check_providers(manifest, registry)
        return reachable + await check_slot_models(manifest, registry)

    return (
        check_scratch_root(manifest),
        await check_docker(manifest, deps.docker_probe),
        *await closing_registry(registry, model_checks()),
        check_forage(manifest),
        check_vector_extension(),
        check_escalation_policy(manifest),
    )


def _summary(checks: tuple[Check, ...]) -> str:
    """Return the closing line: how many failed and warned, or that all is well."""
    failed = sum(check.outcome is Outcome.FAILED for check in checks)
    warned = sum(check.outcome is Outcome.WARNED for check in checks)
    if failed:
        return f"{failed} failed, {warned} warned: fix the FAILED lines before hive run."
    return f"Ready to run ({warned} warned)." if warned else "Ready to run."
