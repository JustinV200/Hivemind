"""Test harness for hivemind.cli.honey: run `hive honey` against a real manifest and SQLite file.

Every `hive honey` test drives the real typer app through `CliRunner` against a
`builders.cli.fake_manifest` Hive (every model slot on the scripted `fake` provider, so the
EMBEDDER is a `FakeEmbedding` and short deposits ripen with no model call at all), seeding Nectar
through the real `NectarIntake` and reading results back through the real stores, the way a
separate `hive run` process would have left them. `invoke` keeps structlog's own log lines off
stdout: the CLI configures no logging, and structlog's default prints to stdout, which would
otherwise sit in front of every `--json` document these tests parse. For label lowering
(ADR-0034), `seed_eligible` stores a ripened Hive Stand deposit only the Real Cell floor holds at
C2, `file_proposals` files its proposal the way a House Bee with no judge would, and
`script_judge` answers the JUDGE slot through the real registry, so `ModelClearanceJudge` itself
runs.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. Used
    by every test module under tests/unit/cli/honey/.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.cli.honey for the command group under test.
    - builders.cli.fake_manifest and builders.honey for the manifest and Nectar builders reused.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.honey import (
    make_honey_identity,
    make_lowering_deps,
    make_nectar_submission,
    make_stand_nectar_draft,
    store_ripened,
)
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

import hivemind.cli.honey.context as context_module
from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.compose.deps import build_provider_registry
from hivemind.cli.stores import open_honey_store, open_trail
from hivemind.forage import ForageMap, ModelSlot
from hivemind.honey_store import (
    Honey,
    LabelLowering,
    LoweringProposal,
    LoweringState,
    Nectar,
    NectarIntake,
    ReadFilter,
    RipenerReading,
)
from hivemind.llm import LLMRequest, LLMResponse, ProviderRegistry, Responder, text_response
from hivemind.manifest import HiveManifest, HoneyStoreSection
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import Clock, SystemClock

__all__ = [
    "READING_REASON",
    "append_toml",
    "bind_embedder",
    "db_path",
    "file_proposals",
    "invoke",
    "make_hive",
    "proposals",
    "rows",
    "script_judge",
    "seed_eligible",
    "seed_nectar",
    "trail_events",
]

_runner = CliRunner()
_ALL_ROWS = 1_000  # More Honey rows than any one test ripens.
_EVENT_LIMIT = 200  # More trail events than any one test records.
_ALL_PROPOSALS = 100  # More lowering proposals than any one test files.
_HOSTED_PROVIDER = '\n[llm.providers.hosted]\nkind = "anthropic"\n'
READING_REASON = "Build output only; nothing personal."  # The Ripener's reason on every seed.


def make_hive(tmp_path: Path) -> Path:
    """Write a fake-provider Hive Manifest under `tmp_path` and return its path."""
    return fake_manifest(tmp_path)


def db_path(manifest: Path) -> Path:
    """Return the SQLite file `fake_manifest` names as `[hive] db`."""
    return manifest.parent / "data" / "hive.sqlite3"


def invoke(manifest: Path, *args: str) -> Result:
    """Run `hive honey --manifest <manifest> <args...>` with structlog's lines captured."""
    with capture_logs():
        return _runner.invoke(app, ["honey", "--manifest", str(manifest), *args])


def seed_nectar(manifest: Path, content: str, **overrides: object) -> Nectar:
    """Deposit one Nectar through the real intake, declared C1 unless overridden."""
    store = open_honey_store(db_path(manifest))
    clock = SystemClock()
    intake = NectarIntake(
        store, make_honey_identity(clock), clock, HoneyStoreSection(), HoneyClearance.C1
    )
    submission = make_nectar_submission(clock, content=content.encode(), **overrides)
    return asyncio.run(intake.submit(submission)).nectar


def seed_eligible(manifest: Path, content: str, title: str = "Build log") -> Nectar:
    """Store one ripened Hive Stand deposit (C2 by the floor, declared C1, read as C1).

    Only the Real Cell floor holds its label up and the Ripener read it lower, so the next
    `file_proposals` (or a House Bee pass) files a C2 -> C1 proposal for it (ADR-0034).
    """
    store = open_honey_store(db_path(manifest))
    clock = SystemClock()
    draft = make_stand_nectar_draft(clock, content=content.encode(), title=title)
    reading = RipenerReading(clearance=HoneyClearance.C1, reason=READING_REASON)
    return asyncio.run(store_ripened(store, clock, draft, reading))


def file_proposals(manifest: Path) -> tuple[LoweringProposal, ...]:
    """File proposals for every eligible Nectar, as a House Bee with no judge would; return them."""
    store = open_honey_store(db_path(manifest))
    lowering = LabelLowering(make_lowering_deps(store, SystemClock()))
    asyncio.run(lowering.file_proposals())
    return proposals(manifest, LoweringState.PROPOSED)


def proposals(manifest: Path, state: LoweringState) -> tuple[LoweringProposal, ...]:
    """Return every lowering proposal in `state`, oldest first."""
    store = open_honey_store(db_path(manifest))
    return asyncio.run(store.list_lowerings(state, _ALL_PROPOSALS))


def rows(manifest: Path) -> tuple[Honey, ...]:
    """Return every live Honey row in the Hive's store, newest first."""
    store = open_honey_store(db_path(manifest))
    every = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)
    return asyncio.run(store.list_honey(every, scope_prefix=None, limit=_ALL_ROWS, offset=0))


def trail_events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """Return every event of `kind` on the Hive's trail, oldest first."""
    trail = open_trail(db_path(manifest))
    return list(asyncio.run(trail.query(TrailQuery(kind=kind, limit=_EVENT_LIMIT))))


def bind_embedder(manifest: Path, *, model: str | None = None, hosted: bool = False) -> None:
    """Rebind `[llm.slots.embedder]`: to another fake model, or to a kind that cannot embed."""
    text = manifest.read_text(encoding="utf-8")
    old = '[llm.slots.embedder]\nprovider = "fake"\nmodel = "test-model"'
    provider = "hosted" if hosted else "fake"
    new = f'[llm.slots.embedder]\nprovider = "{provider}"\nmodel = "{model or "test-model"}"'
    text = text.replace(old, new)
    manifest.write_text(text + (_HOSTED_PROVIDER if hosted else ""), encoding="utf-8")


def append_toml(manifest: Path, toml: str) -> None:
    """Append a table to the manifest (`[honey.lowering]` overrides), as an operator's edit."""
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(f"{text}\n{toml}\n", encoding="utf-8")


def script_judge(monkeypatch: pytest.MonkeyPatch, outcome: str) -> list[LLMRequest]:
    """Answer every JUDGE-slot call `hive honey` makes with `outcome`; return the requests seen.

    The real registry is built with a responder on the `fake` provider (the seam
    `hivemind.cli.compose.deps.build_provider_registry` exists for), so the real
    `ModelClearanceJudge` renders its prompt and parses this verdict through the ladder. Any other
    slot gets an empty reply, which no command here depends on.
    """
    seen: list[LLMRequest] = []

    def respond(request: LLMRequest) -> LLMResponse:
        if request.slot is not ModelSlot.JUDGE:
            return text_response("{}")
        seen.append(request)
        return text_response(json.dumps({"outcome": outcome, "reasons": ["Scripted verdict."]}))

    def scripted(
        manifest: HiveManifest,
        environ: Mapping[str, str],
        clock: Clock,
        forage_map: ForageMap,
        _responders: Mapping[str, Responder] | None,
    ) -> ProviderRegistry:
        return build_provider_registry(manifest, environ, clock, forage_map, {"fake": respond})

    monkeypatch.setattr(context_module, "build_provider_registry", scripted)
    return seen
