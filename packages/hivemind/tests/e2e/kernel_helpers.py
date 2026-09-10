"""Script a FakeLLMProvider for the whole-Hive kernel suite: plans, tool calls, waits, snapshots.

Roadmap step 3.22's own scripting primitives: `HaikuScript` answers a `ModelSlot.QUEEN` planning
call with a fixed plan and a `ModelSlot.WORKER` call with a scenario's own `worker_turn` closure,
on whichever tool-call protocol (native or the prompted `` ```tool ``` `` blocks) the request is
actually on -- the same native-vs-prompted split `hivemind.workers.roles.drone.Drone`'s own unit
test exercises, reused here so the same responder finishes a goal at both `capabilities="full"` and
`capabilities="none"`. `tool_round_count` is what lets a `worker_turn` closure tell how far *this*
attempt has gotten without a raw call counter: a fresh attempt (a first spawn, a Warden respawn, a
Queen retry, or a resume from Handoff) always assembles a brand-new prompt (codingrules 8.8), so a
call counter shared across attempts would misread how many rounds the *current* one has made,
where counting tool results already present in `request.messages` reads correctly every time.
`wait_until` is this suite's one polling primitive on the real clock (most scenarios run their Hive
on a real `SystemClock`, never a fixed `asyncio.sleep`); `snapshot_tree` and `pid_alive` are what
scenario (h)'s left-as-found assertion checks against.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used only by
    `tests/e2e/test_kernel_on_hive_stand.py`.

Key invariants:
    - `HaikuScript.responder` is the one `hivemind.llm.Responder` this whole suite ever installs;
      every scenario varies only the `worker_turn` closure it is built with.
    - `tool_round_count` counts results already present in `request.messages`, never a mutable
      counter carried across separate `LLMRequest`s, so it reads correctly across a respawn,
      rebind or checkpoint-and-resume, each of which starts a brand-new attempt's own conversation.
    - `wait_until` never sleeps a fixed span: it re-checks `condition` every `poll_s` and raises
      once `timeout_s` has elapsed, so a scenario that finishes early never pays for the timeout.

See Also:
    - .claude/roadmap.md step 3.22 for the eight scenarios this module scripts.
    - tests.unit.cli.test_compose for the three-haiku responder shape this module's
      `default_worker_turn`/`tool_response`/`plan_response` generalise (a unit-test-private
      helper, not a builder, so it is lifted here rather than imported).
    - tests.unit.workers.roles.drone.test_role for the native-vs-prompted tool-call shapes at
      `ProviderCapabilities.none()` this module's `tool_response` mirrors.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    LLMRequest,
    LLMResponse,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
    Usage,
)

_FAKE_MODEL_ID = "test-model"  # Neutral (codingrules 8.6); never a real vendor id.
_DEFAULT_FILES: tuple[str, ...] = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
_FINAL_TEXT = "Three haiku written."

# One WORKER-slot LLMRequest in, one LLMResponse out: every scenario's own script shape.
WorkerTurn = Callable[[LLMRequest], LLMResponse]

__all__ = [
    "HaikuScript",
    "WorkerTurn",
    "assert_kinds_in_order",
    "default_worker_turn",
    "pid_alive",
    "plan_response",
    "single_task_plan",
    "snapshot_tree",
    "text_response",
    "tool_response",
    "tool_round_count",
    "wait_until",
    "write_call",
]


def text_response(text: str) -> LLMResponse:
    """Build a plain-text LLMResponse, mirroring `hivemind.llm.fake.text_response`'s own shape."""
    return LLMResponse(
        parts=(TextPart(text=text),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=0, output_tokens=0),
        model=_FAKE_MODEL_ID,
    )


def write_call(
    path: str, content: str = "bees hum softly"
) -> tuple[str, str, Mapping[str, object]]:
    """Build one `write_file` call triple, for `tool_response`."""
    return (f"write_{path}", "write_file", {"path": path, "content": content})


def tool_response(
    request: LLMRequest, calls: Sequence[tuple[str, str, Mapping[str, object]]]
) -> LLMResponse:
    """Build a tool-call LLMResponse on whichever protocol `request` is actually on.

    Args:
        request: The request being answered; a non-empty `request.tools` means native.
        calls: `(call_id, tool_name, arguments)` triples, one per call this round makes.

    Returns:
        Real `ToolCallPart`s on the native protocol; one fenced ` ```tool ``` ` block per call
        (`hivemind.llm.ladders.tools`'s own prompted shape) otherwise.
    """
    if request.tools:
        return LLMResponse(
            parts=tuple(
                ToolCallPart(call=ToolCall(id=cid, name=name, arguments=args))
                for cid, name, args in calls
            ),
            stop_reason=StopReason.TOOL_USE,
            usage=Usage(input_tokens=0, output_tokens=0),
            model=_FAKE_MODEL_ID,
        )
    blocks = "\n\n".join(
        f"```tool\n{json.dumps({'name': name, 'arguments': args})}\n```"
        for _cid, name, args in calls
    )
    return text_response(blocks)


def tool_round_count(request: LLMRequest) -> int:
    """Count tool-call results already present in `request`'s own (attempt-scoped) conversation.

    See the module docstring's "Key invariants" for why this, not a raw call counter, is what a
    `worker_turn` closure should key its next round on.

    Args:
        request: The LLMRequest to inspect.

    Returns:
        How many tool results (native `ToolResultPart`s, or prompted `"Result for "` lines)
        `request.messages` already carries.
    """
    count = 0
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                count += 1
            elif isinstance(part, TextPart):
                count += part.text.count("Result for ")
    return count


def default_worker_turn(
    request: LLMRequest, filenames: Sequence[str] = _DEFAULT_FILES
) -> LLMResponse:
    """The ordinary Drone script every plain scenario reuses: write every file, then stop.

    Args:
        request: The Worker-slot LLMRequest this call answers.
        filenames: Scratch-relative paths, one `write_file` call each, all in the attempt's own
            first round.

    Returns:
        One round of `write_file` calls on a fresh attempt; a closing line once every call's own
        result is already in the conversation.
    """
    if tool_round_count(request) == 0:
        return tool_response(request, tuple(write_call(name) for name in filenames))
    return text_response(_FINAL_TEXT)


def plan_response(request: LLMRequest, plan: Mapping[str, object]) -> LLMResponse:
    """Answer a QUEEN-slot planning call with `plan`, on whichever rung `request` is on."""
    plan_json = json.dumps(plan)
    if request.response_schema is not None:
        return text_response(plan_json)  # NATIVE or JSON_MODE: the schema travelled with it.
    return text_response(f"```json\n{plan_json}\n```")  # PROMPTED: one fenced block expected.


def single_task_plan(
    *filenames: str, key: str = "haikus", title: str = "Write three haiku about bees"
) -> dict[str, object]:
    """Build the one-task, `FILE_EXISTS`-per-file plan every plain scenario submits."""
    return {
        "tasks": [
            {
                "key": key,
                "title": title,
                "objective": f"Write to {', '.join(filenames)} under scratch.",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": name, "argv": [], "expected": None}
                    for name in filenames
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


class HaikuScript:
    """Script a QUEEN-slot plan call and a WORKER-slot Drone script, over one FakeLLMProvider.

    Every scenario in this suite is one HaikuScript: the plan is fixed (module docstring), only
    `worker_turn` -- a plain function of the Worker-slot LLMRequest -- varies what the Drone's
    tool loop does from one scenario to the next.
    """

    def __init__(self, worker_turn: WorkerTurn, plan: Mapping[str, object] | None = None) -> None:
        """Build a HaikuScript.

        Args:
            worker_turn: Answers every `ModelSlot.WORKER` call.
            plan: The plan JSON the QUEEN-slot call answers with; a three-haiku
                `single_task_plan()` when omitted.
        """
        self._worker_turn = worker_turn
        self._plan = plan if plan is not None else single_task_plan(*_DEFAULT_FILES)

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Route one LLMRequest by its own `slot`; the one Responder this suite ever installs."""
        if request.slot is ModelSlot.QUEEN:
            return plan_response(request, self._plan)
        if request.slot is ModelSlot.WORKER:
            return self._worker_turn(request)
        # A Warden/Queen awake episode is never expected to fire in this suite's own scripted
        # scenarios (autopilot alone resolves every one); an empty object is a harmless answer
        # if one ever were routed here.
        return text_response("{}")


async def wait_until(
    condition: Callable[[], bool | Awaitable[bool]], *, timeout_s: float = 5.0, poll_s: float = 0.02
) -> None:
    """Poll `condition` on the real clock until it is true, or raise past `timeout_s`.

    Args:
        condition: Checked repeatedly; a plain bool or an awaitable one (many scenarios need to
            check async chamber/trail state), True ends the wait.
        timeout_s: The longest real time to wait before giving up.
        poll_s: How long to sleep between checks.

    Raises:
        AssertionError: `condition` was still False once `timeout_s` had elapsed.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        result = condition()
        if isinstance(result, Awaitable):
            result = await result
        if result:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"wait_until: condition never held within {timeout_s}s.")
        await asyncio.sleep(poll_s)


def snapshot_tree(root: Path, *, exclude: tuple[Path, ...] = ()) -> dict[str, int]:
    """Map every file under `root` (skipping `exclude`d subtrees) to its own byte size.

    Args:
        root: The directory to snapshot; an absent directory snapshots as empty.
        exclude: Subtrees never to descend into (roadmap step 3.22 scenario (h): the manifest's
            own `[hive] db` directory, whose SQLite writes legitimately change during a run).

    Returns:
        `{relative_posix_path: size_bytes}`, so two snapshots compare with a plain `==`.
    """
    if not root.exists():
        return {}
    resolved_root = root.resolve()
    resolved_exclude = tuple(path.resolve() for path in exclude)
    snapshot: dict[str, int] = {}
    for path in sorted(resolved_root.rglob("*")):
        if not path.is_file() or any(_is_within(path, skip) for skip in resolved_exclude):
            continue
        snapshot[path.relative_to(resolved_root).as_posix()] = path.stat().st_size
    return snapshot


def _is_within(path: Path, root: Path) -> bool:
    """Return whether `path` equals `root` or is somewhere underneath it."""
    return path == root or root in path.parents


def assert_kinds_in_order(kinds: Sequence[str], required: Sequence[str]) -> None:
    """Assert every kind in `required` appears in `kinds`, in that relative first-occurrence order.

    Args:
        kinds: Every trail event's own `kind`, in trail order.
        required: The kinds that must appear, in the relative order they must appear in.

    Raises:
        AssertionError: A required kind never appeared, or appeared out of order.
    """
    first_index = [_first_index(kinds, kind) for kind in required]
    assert first_index == sorted(first_index), (
        f"expected {list(required)} in order, got first-occurrence indexes {first_index} in "
        f"{list(kinds)}"
    )


def _first_index(kinds: Sequence[str], kind: str) -> int:
    """Return `kind`'s first index in `kinds`, asserting it appears at all."""
    assert kind in kinds, f"{kind!r} never appeared on the trail: {list(kinds)}"
    return list(kinds).index(kind)


def pid_alive(pid: int) -> bool:
    """Return whether a process id is still alive, Windows and POSIX alike.

    Test-only process inspection for scenario (h)'s "every started pid is dead" assertion;
    `subprocess`/`os.kill` here are test infrastructure, not `hivemind` src (CLAUDE.md's
    subprocess-homes rule governs `hivemind/`, never `tests/`).

    Args:
        pid: The process id to check.

    Returns:
        True if the process still exists.
    """
    if sys.platform == "win32":
        # SAFETY: an argument list, never shell=True; tasklist is a read-only, well-known system
        # query (mirrors tests.unit.cell.local.test_left_as_found's own `_is_process_alive`).
        result = subprocess.run(  # noqa: S603 -- fixed argv, a well-known system command.
            ["tasklist", "/FI", f"PID eq {pid}"],  # noqa: S607 -- well-known system command.
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
