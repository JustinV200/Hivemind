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
script); the judge approves; the transcriber returns the scripted words, chosen by a byte run the
test planted in the clip's samples when the scenario maps one (`heard`), so one run can speak a goal
and then an answer. The same app runs on a thread of a test's own process (`serve_in_thread`) or
as its own process (`python scripted_openai.py --port N`).

The offline proof (roadmap 8.6, `tests/e2e/offline/`) widens the same server without changing its
invariants: `/v1/embeddings` answers deterministic hashed bag-of-words vectors (a paraphrase that
shares words with a finding lands near it), the ripener's model name (`...ripener`) gets a
scripted structured summary, `Scenario.steps` scripts any tool round by round (skipping a write
the prompt's Handoff says is already done, and a tool the request did not offer), and `Switches`
is the in-process control panel: an outage that answers 503, a hold that parks one Drone call, and
a recorder that keeps only the shape of each chat request (never a body). `Scenario.closing` is
what a Drone says when its rounds are done, so a diagnostic Drone (roadmap 11.9a) can end with the
`CAUSE:` line its Warden reads; a scenario file scripts `steps` as JSON objects. A step can name the
words a conversation must hold (`when`), which is how one server answers the task's Drone and the
Drone its Warden sends to look into a lost link, both asking as the same model. A Warden's
Patrol (roadmap 11.10), the review of what it saw while it watched an idle machine, is answered
from the facts its prompt carries: an ALARM only when the process `Scenario.alarm_on` names is among
them, so a Warden that was shown nothing notable cannot be made to raise an Alarm by a canned reply.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 10 exit-criteria
    runs under tests/e2e. Calls into starlette and uvicorn only, as a server, never the Hive.

Key invariants:
    - Every answer is a pure function of the request and the scenario: nothing is remembered
      between calls, exactly like a stateless model server.
    - Never records or prints a request body: a scenario may carry C2-shaped test words. The
      recorder keeps a model name, tool names, one boolean for "an image part was present" and the
      tool names (with a write's path) the script issued, and nothing else.
    - Every manifest `write_manifest` writes pins the Hive Stand's cores, so the grant a goal gets
      never depends on how busy the machine running the tests is.

See Also:
    - tests/e2e/kernel_helpers.py for the in-process FakeLLMProvider scripts this mirrors.
    - hivemind.llm.providers.openai_compat for the client this server answers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
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
# The Hive Stand's cores, pinned in every manifest `write_manifest` writes: Forage grants a Drone
# the free cores (cores less the one-minute load average) over its footprint, so a busy test host
# near its own core count would leave none; 64 keeps a whole grant under any load a test host
# sees. Mirrors builders.cli.HIVE_STAND_CORES, restated because this module also runs as its own
# process, where the tests' builders are not importable.
_HIVE_STAND_CORES = 64

# Every ModelSlot's manifest key, and the scripted model each is bound to (the routing key).
SLOT_MODELS = {
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

# The manifest `write_manifest` fills in (short cadences, the Hive Stand enabled, and one
# openai_compat provider, the scripted server, that every slot is bound to): a template file
# beside this one, so the script stays small enough to read. Read when a manifest is written,
# never at import.
_MANIFEST_TEMPLATE = Path(__file__).with_name("scripted_manifest.toml.tmpl")

# The embedding width: small enough to be cheap, wide enough that distinct words rarely collide.
EMBEDDING_DIMENSIONS = 64
_WORD = re.compile(r"[a-z0-9]+")  # What counts as a word when hashing a text into a vector.
_REDO = re.compile(r"Do not redo writing to (\S+?);")  # A Handoff's line for a landed write.
_TOOL_BLOCK = re.compile(r"```tool\n(.*?)\n```", re.S)  # A prompted tool call in an answer.
_RESULT_MARK = "Result for "  # How a prompted tool protocol echoes a tool result (user text).
# The content-part types that hold pixels.
_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})
HOLD_MAX_S = 60.0  # A parked call is released by its test; this bound is a hang, not a timing.
# How a Warden's Patrol prompt begins (its system prompt), and the line its facts carry for a new
# process using a notable share of the CPU. Both are the Warden's own wording, restated because
# this module also runs as its own process, where the Hive's code is not importable.
_PATROL_HEADING = "# The Patrol"
_PATROL_SIGNAL = re.compile(r'signal NEW_PROCESS_CPU: name="([^"]*)"')
_SUMMARY = {
    "title": "Widget staging service restart",
    "summary": "How to restart the widget staging service after editing its settings file.",
    "key_facts": ["Restart the staging widget service after editing the settings."],
    "clearance": "C1",
    "clearance_reason": "A service's restart steps; nothing about a person.",
}

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "SLOT_MODELS",
    "RequestShape",
    "Scenario",
    "Step",
    "Switches",
    "build_app",
    "free_port",
    "serve_in_thread",
    "write_manifest",
]


@dataclass(frozen=True)
class Step:
    """One tool call a scripted Drone makes in its own round.

    Attributes:
        tool: The tool's name.
        arguments: Its arguments.
        only_if_offered: Make the call only when the request offered this tool, as a model that
            looks at its tools would (a screenshot only when the binding can see).
        when: Make the call only in a conversation whose messages hold these words, such as a
            sentence of the objective only this Drone was given; empty makes it in any.
    """

    tool: str
    arguments: Mapping[str, object] = field(default_factory=dict)
    only_if_offered: bool = False
    when: str = ""


@dataclass(frozen=True)
class RequestShape:
    """What the recorder keeps of one chat request: its shape, never a word of its content."""

    model: str  # The routing key the manifest bound.
    tools: tuple[str, ...]  # The tool names the request offered.
    has_image_part: bool  # Whether any message carried an image part.
    issued: tuple[str, ...]  # The tool calls the script answered with ("name" or "name:path").
    rounds: int  # How many tool rounds the conversation already held.


@dataclass
class Switches:
    """The in-process controls a test shares with the running server, and its request recorder.

    Attributes:
        outage: While set, every route answers 503, as a model server that has gone away does.
        hold_at: When set to N, the next Drone call whose conversation holds N tool results parks
            (`arrived` is set) until `release` is set; the hold then clears itself.
        arrived: Set when a call parks.
        release: Set by the test to let the parked call answer.
        shapes: One RequestShape per chat request, in arrival order.
    """

    outage: threading.Event = field(default_factory=threading.Event)
    hold_at: int | None = None
    arrived: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    shapes: list[RequestShape] = field(default_factory=list)


@dataclass(frozen=True)
class Scenario:
    """What every scripted slot answers; the defaults finish a one-file goal and reply politely.

    Attributes:
        files: The files the Drone writes (and the plan's acceptance checks), scratch-relative.
        question: When set, the Drone first asks the human this and writes only once answered.
        reply: What the Queen says when the human writes to her in the chat.
        transcript: What the transcriber hears in a clip no `heard` marker matches.
        heard: `(marker, words)` pairs: a clip whose bytes hold the marker is heard as the words
            (in-process only: a scenario file for `--scenario` cannot carry bytes).
        extra_tasks: More plan entries, appended after the root task (e.g. a dependant).
        writes: The files the Drone actually writes; None writes every one of `files`, and an
            empty tuple writes nothing, so the root task's acceptance check fails.
        command: When set, the Drone runs this argv on every round and never finishes: a Cell
            kept busy for as long as a scenario needs it (the Docker egress proof, 10.6a).
        steps: When set, the Drone makes these calls one per round instead (see `Step`); a
            `write_file` the prompt's Handoff says already landed is skipped, so a resumed
            attempt carries on where the checkpointed one stopped.
        closing: The Drone's closing message once its rounds are done. A diagnostic Drone (roadmap
            11.9a) ends with a first line `CAUSE: <the cause>` or `CAUSE: none found`, which is
            how its Warden reads what it learned.
        plan_json: When set, the planner answers this plan whole, whatever the goal.
        alarm_on: The name of a process that makes a Warden's Patrol decide ALARM when the facts it
            is shown name it as a new process using the CPU; any other Patrol finds nothing notable.
        ripener: The structured summary the ripener's model (a name ending `ripener`) answers.
        switches: The in-process controls and recorder (`Switches`); not part of equality.
    """

    files: tuple[str, ...] = ("done.txt",)
    question: str | None = None
    reply: str = "I am on it."
    transcript: str = "Write a haiku about bees."
    extra_tasks: tuple[Mapping[str, object], ...] = field(default=())
    writes: tuple[str, ...] | None = None
    heard: tuple[tuple[bytes, str], ...] = ()
    command: tuple[str, ...] | None = None
    steps: tuple[Step, ...] | None = None
    closing: str = "Done."
    plan_json: Mapping[str, object] | None = None
    alarm_on: str = ""
    ripener: Mapping[str, object] = field(default_factory=lambda: dict(_SUMMARY))
    switches: Switches = field(default_factory=Switches, compare=False)

    def words_for(self, upload: bytes) -> str:
        """What the transcriber hears in an upload: the first marker's words, or `transcript`."""
        return next((words for marker, words in self.heard if marker in upload), self.transcript)

    def plan(self, goal: str) -> dict[str, object]:
        """The plan the planner answers for `goal`: one root task, plus any extra tasks."""
        if self.plan_json is not None:
            return dict(self.plan_json)
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
    switches = scenario.switches

    async def models(_request: Request) -> Response:
        names = ("scripted-queen", "scripted-worker", "scripted-judge", "scripted-ears")
        return _unless_down(switches) or JSONResponse(
            {"object": "list", "data": [{"id": n, "object": "model"} for n in names]}
        )

    async def chat(request: Request) -> Response:
        if (down := _unless_down(switches)) is not None:
            return down
        body = await request.json()
        await _maybe_park(switches, body)
        message = _answer(scenario, body)
        switches.shapes.append(_shape_of(body, message))
        if body.get("stream"):
            return StreamingResponse(_sse(body, message), media_type="text/event-stream")
        return JSONResponse(_completion(body, message))

    async def transcribe(request: Request) -> Response:
        if (down := _unless_down(switches)) is not None:
            return down
        # The multipart body is read whole, only to pick the scripted words; nothing keeps it.
        words = scenario.words_for(await request.body())
        segment = {"id": 0, "start": 0.0, "end": 1.0, "text": words}
        return JSONResponse(
            {"text": words, "language": "en", "duration": 1.0, "segments": [segment]}
        )

    async def embed(request: Request) -> Response:
        if (down := _unless_down(switches)) is not None:
            return down
        return JSONResponse(_embeddings(await request.json()))

    routes = [
        Route(f"{_API_ROOT}/models", models, methods=["GET"]),
        Route(f"{_API_ROOT}/chat/completions", chat, methods=["POST"]),
        Route(f"{_API_ROOT}/audio/transcriptions", transcribe, methods=["POST"]),
        Route(f"{_API_ROOT}/embeddings", embed, methods=["POST"]),
    ]
    return Starlette(routes=routes)


def _unless_down(switches: Switches) -> Response | None:
    """A 503 while the test has the outage switched on, else None (serve as usual)."""
    if not switches.outage.is_set():
        return None
    return JSONResponse({"error": {"message": "scripted outage"}}, status_code=503)


async def _maybe_park(switches: Switches, body: Mapping[str, object]) -> None:
    """Park one Drone call until the test releases it, when `hold_at` asks for exactly this one."""
    if switches.hold_at is None or not str(body.get("model", "")).endswith("worker"):
        return
    if _rounds_done(_messages(body)) != switches.hold_at:
        return
    switches.hold_at = None  # One parked call; the next ones answer at once.
    switches.arrived.set()
    # A worker thread waits, so this server's own loop keeps answering (the Queen's calls, health).
    await asyncio.to_thread(switches.release.wait, HOLD_MAX_S)


def _embeddings(body: Mapping[str, object]) -> dict[str, object]:
    """Embed each input as a hashed bag of words: texts sharing words land near each other."""
    raw = body.get("input")
    texts = (
        [raw] if isinstance(raw, str) else [str(i) for i in raw] if isinstance(raw, list) else []
    )
    data = [
        {"object": "embedding", "index": index, "embedding": _vector(text)}
        for index, text in enumerate(texts)
    ]
    tokens = sum(len(_WORD.findall(text.lower())) for text in texts)
    usage = {"prompt_tokens": tokens, "total_tokens": tokens}
    return {"object": "list", "data": data, "model": body.get("model", ""), "usage": usage}


def _vector(text: str) -> list[float]:
    """One unit vector: each word adds one to the dimension its hash picks."""
    counts = [0.0] * EMBEDDING_DIMENSIONS
    for word in _WORD.findall(text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
        counts[int.from_bytes(digest, "big") % EMBEDDING_DIMENSIONS] += 1.0
    norm = math.sqrt(sum(value * value for value in counts)) or 1.0
    return [value / norm for value in counts]


def free_port() -> int:
    """Return a loopback TCP port nothing is listening on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve_in_thread(scenario: Scenario, host: str = "127.0.0.1") -> Iterator[str]:
    """Run the scripted server on its own thread and loop; yield its loopback `/v1` base URL.

    For a test that drives the Hive through `asyncio.run` itself (`build_hive` probes the host
    with its own event loop, so the server cannot share the test's). `host` is where it binds:
    loopback by default; every interface for a real container to reach (a Docker Cell dials the
    host gateway), while the URL yielded stays loopback, which the Hive rewrites for its Cells.
    """
    port = free_port()
    config = uvicorn.Config(
        build_app(scenario), host=host, port=port, log_level="warning", lifespan="off"
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
        for slot, model in SLOT_MODELS.items()
    )
    text = _MANIFEST_TEMPLATE.read_text(encoding="utf-8").format(
        hive_id=hive_ids[0],
        node_id=hive_ids[1],
        db=(directory / "data" / "hive.sqlite3").as_posix(),
        scratch=(directory / "scratch").as_posix(),
        base_url=base_url,
        slots=slots,
        # Pinned, never the host's own count: a busy test host must never zero a Drone's grant.
        cores=_HIVE_STAND_CORES,
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
    if model.endswith("ripener"):
        return _content(dict(scenario.ripener))
    if model.endswith("queen"):
        return _queen(scenario, text)
    return _worker(scenario, body, messages)


def _queen(scenario: Scenario, text: str) -> dict[str, object]:
    """Plan a goal, review a Patrol, or decide an awake episode (a REPLY if the human wrote)."""
    if text.startswith(_PATROL_HEADING):
        return _content(_patrol(scenario, text))
    if PLANNER_TAG in text:
        start = text.index(PLANNER_TAG) + len(PLANNER_TAG)
        goal = text[start : text.find("<<<end user>>>", start)].strip()
        return _content(scenario.plan(goal))
    if HUMAN_FENCE in text:
        return _content(
            {"action": "REPLY", "reason": "The human wrote.", "message": scenario.reply}
        )
    return _content({"action": "ESCALATE_TO_HUMAN", "reason": "Scripted: the human decides."})


def _patrol(scenario: Scenario, text: str) -> dict[str, object]:
    """Decide a Patrol from its facts: an ALARM only for the process the scenario names."""
    if scenario.alarm_on and scenario.alarm_on in _PATROL_SIGNAL.findall(text):
        return {"choice": "ALARM", "reason": "A new process pins the CPU."}
    return {"choice": "NOTHING", "reason": "An ordinary interval."}


def _worker(
    scenario: Scenario, body: Mapping[str, object], messages: list[Mapping[str, object]]
) -> dict[str, object]:
    """Run the Drone's rounds: ask first when scripted to, then write every file, then stop."""
    done = _rounds_done(messages)
    calls: list[tuple[str, dict[str, object]]] = []
    if scenario.steps is not None:
        calls = _step_calls(scenario.steps, body, messages, done)
    elif scenario.command is not None:
        calls = [("run_command", {"argv": list(scenario.command)})]  # Busy, round after round.
    elif scenario.question is not None and done == 0:
        calls = [("ask", {"text": scenario.question})]
    elif done == (1 if scenario.question is not None else 0):
        written = scenario.files if scenario.writes is None else scenario.writes
        calls = [("write_file", {"path": name, "content": "bees hum"}) for name in written]
    if not calls:
        return {"role": "assistant", "content": scenario.closing}
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


def _rounds_done(messages: list[Mapping[str, object]]) -> int:
    """How many tool rounds this conversation holds: native results, or a prompted echo's."""
    native = sum(1 for message in messages if message.get("role") == "tool")
    return native or sum(
        _text_of(m).count(_RESULT_MARK) for m in messages if m.get("role") == "user"
    )


def _step_calls(
    steps: tuple[Step, ...],
    body: Mapping[str, object],
    messages: list[Mapping[str, object]],
    done: int,
) -> list[tuple[str, dict[str, object]]]:
    """The one call round `done` makes: the steps in order, less those this request rules out."""
    offered, words = _offered(body), "\n".join(_text_of(message) for message in messages)
    landed = set(_REDO.findall(words))
    live = [
        step
        for step in steps
        if (offered is None or not step.only_if_offered or step.tool in offered)
        and not (step.tool == "write_file" and step.arguments.get("path") in landed)
        and step.when in words  # An empty `when` is held by every conversation.
    ]
    return [(live[done].tool, dict(live[done].arguments))] if done < len(live) else []


def _offered(body: Mapping[str, object]) -> set[str] | None:
    """The tool names a request offered natively, or None when it offered none (prompted)."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return None
    functions = (tool.get("function") for tool in tools if isinstance(tool, Mapping))
    return {str(f.get("name")) for f in functions if isinstance(f, Mapping)}


def _shape_of(body: Mapping[str, object], message: Mapping[str, object]) -> RequestShape:
    """The recorder's row for one answered request: names and one boolean, never content."""
    return RequestShape(
        model=str(body.get("model", "")),
        tools=tuple(sorted(_offered(body) or ())),
        has_image_part=any(_has_image(m) for m in _messages(body)),
        issued=tuple(_issued(message)),
        rounds=_rounds_done(_messages(body)),
    )


def _issued(message: Mapping[str, object]) -> Iterator[str]:
    """The calls an answer makes, native or prompted, as "name" or "name:path"."""
    native = message.get("tool_calls")
    calls = [
        (call["function"]["name"], json.loads(call["function"]["arguments"]))
        for call in (native if isinstance(native, list) else [])
    ]
    for block in _TOOL_BLOCK.findall(str(message.get("content") or "")):
        parsed = json.loads(block)
        calls.append((parsed["name"], parsed["arguments"]))
    for name, arguments in calls:
        path = arguments.get("path")
        yield f"{name}:{path}" if path else str(name)


def _has_image(message: Mapping[str, object]) -> bool:
    """Whether one message carries an image part (pixels), whatever else it holds."""
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(part, Mapping) and part.get("type") in _IMAGE_PART_TYPES for part in content
    )


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
    for key in ("files", "extra_tasks", "writes", "command", "steps"):
        value = fields.get(key)
        if isinstance(value, list):  # JSON arrays become tuples; the Drone's rounds become Steps.
            fields[key] = tuple(Step(**item) if key == "steps" else item for item in value)
    scenario = Scenario(**fields)  # type: ignore[arg-type]
    uvicorn.run(build_app(scenario), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    _main()
