"""Serve a scripted OpenAI-compatible model API on loopback, for whole-Hive runs over real sockets.

The phase 10 exit criteria are proved by real processes (`hive serve`, `hive run --remote`, the
enrolment and login flows) talking over real sockets, and the Hive needs a model to plan a goal,
run a Drone and answer the human. This container has no hosted-model credentials and cannot pull
a local model, so this module stands in for the model server: an OpenAI-compatible HTTP API
(`/v1/models`, `/v1/chat/completions`, `/v1/audio/transcriptions`) whose every answer is scripted.
A manifest binds each slot to its own model name (`scripted-queen`, `scripted-worker`,
`scripted-judge`, `scripted-ears`), which is how a request is routed to its script: the Queen's
planning call (her prompt carries the planner's `<<<user>>>` goal section) gets a plan; her awake
episode gets a decision, a REPLY when the human's message is in its prompt; a Drone gets its
tool rounds, counted from the tool results already in the conversation (so a respawn restarts the
script); the judge approves; the transcriber returns the scripted words. The same app runs inside
a test's own event loop (`serve`) or as its own process (`python scripted_openai.py --port N`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 10 exit-criteria
    runs under tests/e2e. Calls into starlette and uvicorn only, as a server, never the Hive.

Key invariants:
    - Every answer is a pure function of the request and the scenario: nothing is remembered
      between calls, exactly like a stateless model server.
    - Never records or prints a request body: a scenario may carry C2-shaped test words.

See Also:
    - tests/e2e/kernel_helpers.py for the in-process FakeLLMProvider scripts this mirrors.
    - hivemind.llm.providers.openai_compat for the client this server answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

PLANNER_TAG = "<<<user>>>"  # Only the planner's rendered prompt carries the goal section.
HUMAN_FENCE = "<<<human_message untrusted>>>"  # The awake episode's fence around the human's text.
SERVER_START_S = 10.0  # A loopback uvicorn starts in milliseconds; this bound is a hang.
# The API root every route hangs under; composed, like the adapter's own paths, so no provider URL
# literal appears outside a manifest (scripts/check_no_model_ids.py, codingrules 8.6).
_API_ROOT = "/v1"

# Every ModelSlot's manifest key, and the scripted model each is bound to (the routing key).
_SLOT_MODELS = {
    "queen": "scripted-queen",
    "attendant": "scripted-queen",
    "warden": "scripted-queen",
    "worker": "scripted-worker",
    "ripener": "scripted-worker",
    "scaffolder": "scripted-worker",
    "embedder": "scripted-worker",
    "judge": "scripted-judge",
    "transcriber": "scripted-ears",
}

# The manifest `write_manifest` fills in: short cadences, the Hive Stand enabled, and one
# openai_compat provider (the scripted server) that every slot is bound to.
_MANIFEST = """[hive]
id = "{hive_id}"
node_id = "{node_id}"
name = "Scripted Hive"
db = "{db}"

[queen]
tick_interval_s = 0.05
heartbeat_interval_s = 0.5

[hive_stand]
enabled = true
scratch_root = "{scratch}"

[llm.providers.scripted]
kind = "openai_compat"
base_url = "{base_url}"
timeout_s = 30.0
seats = 4

[llm.providers.scripted.capabilities]
native_tool_calls = true
schema_output = false
json_mode = true
vision = false
streaming = true
reasoning_control = false
context_window = 32768
system_role = true
parallel_tool_calls = true
token_counting = false

{slots}
[forage.map.scripted_local]
provider = "scripted"
model = "scripted-worker"
grade = 3
context_window = 32768
seats = 4

[forage.roles.drone]
cpu_cores = 0.5
memory_bytes = 268435456
token_rate_per_minute = 20000

[supervision]
heartbeat_interval_s = 0.5
heartbeat_miss_limit = 6
"""

__all__ = ["Scenario", "build_app", "free_port", "serve", "serve_in_thread", "write_manifest"]


@dataclass(frozen=True)
class Scenario:
    """What every scripted slot answers; the defaults finish a one-file goal and reply politely.

    Attributes:
        files: The files the Drone writes (and the plan's acceptance checks), scratch-relative.
        question: When set, the Drone first asks the human this and writes only once answered.
        reply: What the Queen says when the human writes to her in the chat.
        transcript: What the transcriber hears in any clip.
        extra_tasks: More plan entries, appended after the root task (e.g. a dependant).
        writes: The files the Drone actually writes; None writes every one of `files`, and an
            empty tuple writes nothing, so the root task's acceptance check fails.
    """

    files: tuple[str, ...] = ("done.txt",)
    question: str | None = None
    reply: str = "I am on it."
    transcript: str = "Write a haiku about bees."
    extra_tasks: tuple[Mapping[str, object], ...] = field(default=())
    writes: tuple[str, ...] | None = None

    def plan(self, goal: str) -> dict[str, object]:
        """The plan the planner answers for `goal`: one root task, plus any extra tasks."""
        root = {
            "key": "root",
            "title": "Do the work",
            "objective": f"Do the work for: {goal[:200]}",
            "acceptance": [
                {"kind": "FILE_EXISTS", "subject": name, "argv": [], "expected": None}
                for name in self.files
            ],
            "needs": {},
            "clearance": "C1",
            "depends_on": [],
        }
        return {"tasks": [root, *self.extra_tasks]}


def build_app(scenario: Scenario) -> Starlette:
    """Build the scripted OpenAI-compatible application for `scenario`."""

    async def models(_request: Request) -> Response:
        names = ("scripted-queen", "scripted-worker", "scripted-judge", "scripted-ears")
        return JSONResponse(
            {"object": "list", "data": [{"id": n, "object": "model"} for n in names]}
        )

    async def chat(request: Request) -> Response:
        body = await request.json()
        message = _answer(scenario, body)
        if body.get("stream"):
            return StreamingResponse(_sse(body, message), media_type="text/event-stream")
        return JSONResponse(_completion(body, message))

    async def transcribe(request: Request) -> Response:
        # The multipart body is read (and discarded) so the client sees a normal exchange.
        await request.body()
        words = scenario.transcript
        segment = {"id": 0, "start": 0.0, "end": 1.0, "text": words}
        return JSONResponse(
            {"text": words, "language": "en", "duration": 1.0, "segments": [segment]}
        )

    routes = [
        Route(f"{_API_ROOT}/models", models, methods=["GET"]),
        Route(f"{_API_ROOT}/chat/completions", chat, methods=["POST"]),
        Route(f"{_API_ROOT}/audio/transcriptions", transcribe, methods=["POST"]),
    ]
    return Starlette(routes=routes)


def free_port() -> int:
    """Return a loopback TCP port nothing is listening on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def serve(scenario: Scenario, port: int | None = None) -> AsyncIterator[str]:
    """Run the scripted server in this event loop; yield its `/v1` base URL until the block ends."""
    chosen = port if port is not None else free_port()
    config = uvicorn.Config(
        build_app(scenario), host="127.0.0.1", port=chosen, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    try:
        async with asyncio.timeout(SERVER_START_S):
            # uvicorn exposes no start event, only this flag: poll it, bounded.
            while not server.started:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{chosen}/v1"
    finally:
        server.should_exit = True
        await task


@contextmanager
def serve_in_thread(scenario: Scenario) -> Iterator[str]:
    """Run the scripted server on its own thread and loop; yield its `/v1` base URL.

    For a test that drives the Hive through `asyncio.run` itself (`build_hive` probes the host
    with its own event loop, so the server cannot share the test's).
    """
    port = free_port()
    config = uvicorn.Config(
        build_app(scenario), host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="scripted-openai", daemon=True)
    thread.start()
    deadline = time.monotonic() + SERVER_START_S
    while not server.started:
        if time.monotonic() > deadline:
            raise TimeoutError("The scripted model server did not start.")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_START_S)


def write_manifest(directory: Path, base_url: str, hive_ids: tuple[str, str]) -> Path:
    """Write `<directory>/hive.toml`: every slot bound to the scripted server at `base_url`.

    Args:
        directory: Where the manifest, the database and the scratch root live.
        base_url: The scripted server's `/v1` base URL.
        hive_ids: The Hive's id and node id, minted by the caller's clock.

    Returns:
        The manifest's path.
    """
    (directory / "data").mkdir(parents=True, exist_ok=True)
    (directory / "scratch").mkdir(parents=True, exist_ok=True)
    slots = "\n".join(
        f'[llm.slots.{slot}]\nprovider = "scripted"\nmodel = "{model}"\n'
        for slot, model in _SLOT_MODELS.items()
    )
    text = _MANIFEST.format(
        hive_id=hive_ids[0],
        node_id=hive_ids[1],
        db=(directory / "data" / "hive.sqlite3").as_posix(),
        scratch=(directory / "scratch").as_posix(),
        base_url=base_url,
        slots=slots,
    )
    path = directory / "hive.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────────────────
# Scripts, one per slot (routed by the model name the manifest bound)
# ──────────────────────────────────────────────────────────────────────────────


def _answer(scenario: Scenario, body: Mapping[str, object]) -> dict[str, object]:
    """Return the assistant message the scenario's script gives for this request."""
    model = str(body.get("model", ""))
    messages = _messages(body)
    text = "\n".join(_text_of(message) for message in messages)
    if model.endswith("judge"):
        return _content({"outcome": "APPROVE", "reasons": [], "notes": ""})
    if model.endswith("queen"):
        return _queen(scenario, text)
    return _worker(scenario, body, messages)


def _queen(scenario: Scenario, text: str) -> dict[str, object]:
    """Plan a goal, or decide an awake episode (a REPLY when the human wrote in the chat)."""
    if PLANNER_TAG in text:
        start = text.index(PLANNER_TAG) + len(PLANNER_TAG)
        goal = text[start : text.find("<<<end user>>>", start)].strip()
        return _content(scenario.plan(goal))
    if HUMAN_FENCE in text:
        return _content(
            {"action": "REPLY", "reason": "The human wrote.", "message": scenario.reply}
        )
    return _content({"action": "ESCALATE_TO_HUMAN", "reason": "Scripted: the human decides."})


def _worker(
    scenario: Scenario, body: Mapping[str, object], messages: list[Mapping[str, object]]
) -> dict[str, object]:
    """Run the Drone's rounds: ask first when scripted to, then write every file, then stop."""
    results = [message for message in messages if message.get("role") == "tool"]
    calls: list[tuple[str, dict[str, object]]] = []
    if scenario.question is not None and not results:
        calls = [("ask", {"text": scenario.question})]
    elif len(results) == (1 if scenario.question is not None else 0):
        written = scenario.files if scenario.writes is None else scenario.writes
        calls = [("write_file", {"path": name, "content": "bees hum"}) for name in written]
    if not calls:
        return {"role": "assistant", "content": "Done."}
    if body.get("tools"):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{index}_{int(time.monotonic() * 1000)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
                for index, (name, arguments) in enumerate(calls)
            ],
        }
    blocks = "\n\n".join(
        f"```tool\n{json.dumps({'name': name, 'arguments': arguments})}\n```"
        for name, arguments in calls
    )
    return {"role": "assistant", "content": blocks}


# ──────────────────────────────────────────────────────────────────────────────
# Wire shapes
# ──────────────────────────────────────────────────────────────────────────────


def _content(value: object) -> dict[str, object]:
    """An assistant message whose content is `value` as JSON text."""
    return {"role": "assistant", "content": json.dumps(value)}


def _messages(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    """The request's messages, each a mapping (anything else is ignored)."""
    raw = body.get("messages")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


def _text_of(message: Mapping[str, object]) -> str:
    """Every text a message carries, whether its content is a string or a list of parts."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping))
    return ""


def _completion(body: Mapping[str, object], message: dict[str, object]) -> dict[str, object]:
    """A non-streaming `/chat/completions` reply carrying `message`."""
    finish = "tool_calls" if message.get("tool_calls") else "stop"
    return {
        "id": f"chatcmpl-{int(time.monotonic() * 1000)}",
        "object": "chat.completion",
        "model": body.get("model", ""),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


async def _sse(body: Mapping[str, object], message: dict[str, object]) -> AsyncIterator[bytes]:
    """The same reply as one streamed delta, then the finish chunk and `[DONE]`."""
    completion = _completion(body, message)
    finish = "tool_calls" if message.get("tool_calls") else "stop"
    delta = {key: value for key, value in message.items() if value is not None}
    calls = delta.get("tool_calls")
    # A streamed tool call carries its own position, which a whole message leaves implicit.
    if isinstance(calls, list):
        delta["tool_calls"] = [{**call, "index": index} for index, call in enumerate(calls)]
    first = {**completion, "object": "chat.completion.chunk"}
    first["choices"] = [{"index": 0, "delta": delta, "finish_reason": None}]
    last = {**completion, "object": "chat.completion.chunk"}
    last["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
    for chunk in (first, last):
        yield f"data: {json.dumps(chunk)}\n\n".encode()
    yield b"data: [DONE]\n\n"


def _main() -> None:
    """Run the server as its own process: `--port`, and an optional scenario JSON file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scenario", default=None, help="A JSON file of Scenario fields.")
    args = parser.parse_args()
    fields: dict[str, object] = {}
    if args.scenario:
        with open(args.scenario, encoding="utf-8") as handle:
            fields = json.load(handle)
    for key in ("files", "extra_tasks", "writes"):
        value = fields.get(key)
        if isinstance(value, list):
            fields[key] = tuple(value)
    scenario = Scenario(**fields)  # type: ignore[arg-type]
    uvicorn.run(build_app(scenario), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    _main()
