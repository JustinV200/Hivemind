"""Compose a Hive from a manifest, run it, and run one goal: build_hive, run_hive, run_goal.

Roadmap step 3.21 (second half)'s own composition root, grown over phases 4 to 10 into three
modules that each need a file: `build` is the *only* place a loaded `hivemind.manifest.
HiveManifest` becomes a running Hive's every collaborator (codingrules section 13), `run` leases
the Hive Stand's one Cell, runs the Queen, the Warden and the House Bee's ripening loop for as long
as its `async with` block is open and tears them down on exit, and `goals` submits one goal (or a
goal request) and follows it until every one of its tasks is terminal or its timeout passes.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.run`, `hivemind.cli.compose.entrance` (`hive serve`) and by every test that
    drives the kernel end to end against a `hivemind.llm.FakeLLMProvider`. Calls into this
    package's `build`, `run` and `goals` only.

Key invariants:
    - `build_hive` never touches the network, and `run_hive` always stops and closes everything
      it started, in order, whether its `async with` block exits cleanly or raises (`build`'s and
      `run`'s own docstrings hold the whole of each rule).

See Also:
    - .claude/codingrules.md section 13 for "the composition root is the only place a HiveManifest
      is converted".
    - hivemind.cli.compose.deps for every manifest-to-deps builder `build_hive` composes.

Public API:
    - Hive, build_hive: every collaborator a running Hive has, and the one way to build them.
    - run_hive: run a built Hive for the length of an `async with` block.
    - GoalReport, run_goal, run_requested_goal: run one goal to its end and report how it went.
"""

from hivemind.cli.compose.hive.build import Hive, build_hive
from hivemind.cli.compose.hive.goals import GoalReport, run_goal, run_requested_goal
from hivemind.cli.compose.hive.run import run_hive

__all__ = ["GoalReport", "Hive", "build_hive", "run_goal", "run_hive", "run_requested_goal"]
