"""Write what a colonizable device and the Hive that promotes it say about models, as text.

A Nuc is a device whose Warden serves a model of its own, and three parties must agree on it
before the Queen will start one: the Hive's manifest lists the server (with `load_on_start =
false`, so the Hive Stand never starts a copy) and the models it may load, the device's manifest
declares the same server id and the Forage map rows of everything it could load, and the model
itself answers. This module is the first two as text and the third as the scenario files the
scripted model server (`e2e.scripted_openai`) runs from, so a scenario's agreement is one place:

- `hive_tables` is what the Hive appends to its manifest: the provider the server answers as, the
  server, and two models it is allowed (`SMALL`, loaded when the device is promoted, and `EXTRA`,
  which its Warden may load later without asking).
- `device_manifest` is the device's own `warden.toml`: the shared provider (the scripted server on
  the Hive Stand's side of the cable), its own, every slot bound, the worker slot falling back to
  the local model, and the server's rows, with a third model (`ROGUE`) the device lists and the
  Hive does not allow, which is what the Warden is refused.
- `shared_scenario` and `local_scenario` are the two scripted servers' rounds: a Drone that thinks
  on either makes the same kind of tool calls, a few seconds apart, and writes the report last.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `e2e.swarm.colony.device`
    and the phase 11 Nuc scenario; pure, and imports nothing of the Hive.

Key invariants:
    - The model names the two servers answer to are the scripted server's own routing keys: a name
      ending `worker` is answered as a Drone, so the three local models are all Drones.
    - The device's server lists every model the Hive's lists, in the Hive's order, and one more:
      the allowlist is the Hive's, never the device's.
    - Nothing here is a secret: an API key is never in either manifest.

See Also:
    - docs/manifests/device.toml for the annotated shape of the device's file.
    - e2e/scripted_openai.py for the server these scenarios script.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from e2e.scripted_openai import SLOT_MODELS

SERVER_ID = "nuc_model"  # The `[llm.servers]` key both manifests share: what the Warden declares.
SERVER_PORT = 18081  # The loopback port the device's model server answers on.
# Seats on each model, and on each provider: a grant reserves seats on every source its bees may
# draw on, so a device with two seats in all gives its first task all of them and its second none.
SEATS = 6
LOCAL_PROVIDER = "nuc_local"  # The provider the server answers as, in both manifests.
SHARED_PROVIDER = "shared"  # The device's name for the Hive's side of the cable.
SMALL, EXTRA, ROGUE = "nuc_small", "nuc_extra", "nuc_rogue"  # The three Forage map sources.
MODELS = {SMALL: "local-small-worker", EXTRA: "local-extra-worker", ROGUE: "local-rogue-worker"}
ALLOWED = (SMALL, EXTRA)  # What the Hive lets the server load; ROGUE is the device's own idea.
STRONG = "shared_strong"  # The Hive's own best model: the only source that clears an exacting bar.
LOCAL_SLOT = "local_worker"  # The named binding the worker slot falls back to: the local model.
LOCAL_JUDGE = (
    "local_judge"  # The same for the judge slot, which a Capping review of a command uses.
)
# The slots whose chain ends on the device's own model, so a Nuc cut off from the Hive keeps working
# on them: a Drone's own, and the judge a Capping review of its commands asks (the scripted server
# answers a model whose name ends `judge` as a judge). Every other slot is shared, with no fallback.
FALLBACKS = {"worker": LOCAL_SLOT, "judge": LOCAL_JUDGE}
LOCAL_ROWS = {LOCAL_SLOT: MODELS[SMALL], LOCAL_JUDGE: "local-judge"}
SCRIPTED_SERVER = "/opt/scripted_openai.py"  # Where the scripted server's source is copied to.
LOCAL_SCENARIO = "/opt/local-scenario.json"  # Where the local server's rounds are copied to.
WARDEN_MANIFEST = "/opt/warden.toml"  # Where the device's manifest is copied to.
# The interpreter the packet's PREPARE builds: the only Python on the device that has uvicorn.
RUNTIME_PYTHON = "/var/lib/pollen/runtime/venv/bin/python"

__all__ = [
    "ALLOWED",
    "EXTRA",
    "FALLBACKS",
    "LOCAL_JUDGE",
    "LOCAL_PROVIDER",
    "LOCAL_ROWS",
    "LOCAL_SCENARIO",
    "LOCAL_SLOT",
    "MODELS",
    "ROGUE",
    "RUNTIME_PYTHON",
    "SCRIPTED_SERVER",
    "SEATS",
    "SERVER_ID",
    "SERVER_PORT",
    "SHARED_PROVIDER",
    "SMALL",
    "STRONG",
    "WARDEN_MANIFEST",
    "device_manifest",
    "hive_tables",
    "local_scenario",
    "scenario_text",
    "shared_scenario",
]


def hive_tables() -> str:
    """The tables the Hive's manifest gains: the server, its provider and the models it may load."""
    lines = [f"[llm.providers.{LOCAL_PROVIDER}]", 'kind = "openai_compat"']
    lines += [
        f'base_url = "http://127.0.0.1:{SERVER_PORT}/v1"',
        "timeout_s = 30.0",
        f"seats = {SEATS}",
        "",
    ]
    lines += [f"[llm.servers.{SERVER_ID}]", f'provider = "{LOCAL_PROVIDER}"']
    # Never run on the Hive Stand: `load_on_start` is false, and a Nuc's Warden starts its own.
    lines += ['argv = ["sh", "-c", "exit 1"]', f"port = {SERVER_PORT}", 'health_path = "/models"']
    lines += ["startup_timeout_s = 60.0", f"sources = {json.dumps(list(ALLOWED))}"]
    lines += ["load_on_start = false", ""]
    for source in ALLOWED:
        lines += _map_row(source)
    # The one model on the Hive's map that clears the bar of an exacting task (grade 5): shared, so
    # it sits behind the cable. Without it no source qualifies and the Queen refuses the task.
    lines += [f"[forage.map.{STRONG}]", 'provider = "fake"', 'model = "fake-strong"']
    lines += ["grade = 5", "context_window = 8192", f"seats = {SEATS}", ""]
    return "\n".join(lines)


def device_manifest(
    shared_url: str, sources: Sequence[str] = (*ALLOWED, ROGUE), supervision: str = ""
) -> str:
    """The device's `warden.toml`: the Hive's side of the cable, its own model and its server.

    Args:
        shared_url: The `/v1` base URL of the scripted model server on the Hive Stand's side of
            the cable, as the device reaches it.
        sources: The Forage map sources the device's server may load, in order.
        supervision: A `[supervision]` table (header included) for a scenario that cuts the device
            off and cannot wait the ten minutes the shipped defaults give; none leaves them.

    Returns:
        The manifest's text.
    """
    lines = ["[llm]", "offline = false", ""]
    lines += [f"[llm.providers.{SHARED_PROVIDER}]", 'kind = "openai_compat"']
    lines += [f'base_url = "{shared_url}"', "timeout_s = 15.0", f"seats = {SEATS}", ""]
    lines += [f"[llm.providers.{LOCAL_PROVIDER}]", 'kind = "openai_compat"']
    lines += [
        f'base_url = "http://127.0.0.1:{SERVER_PORT}/v1"',
        "timeout_s = 30.0",
        f"seats = {SEATS}",
        "",
    ]
    for slot, model in SLOT_MODELS.items():
        lines += [f"[llm.slots.{slot}]", f'provider = "{SHARED_PROVIDER}"', f'model = "{model}"']
        if slot in FALLBACKS:
            lines += [f'fallback = "{FALLBACKS[slot]}"']  # Where a Nuc's bees go first, or next.
        lines += [""]
    for key, model in LOCAL_ROWS.items():
        lines += [f"[llm.slots.{key}]", f'provider = "{LOCAL_PROVIDER}"', f'model = "{model}"', ""]
    lines += [f"[llm.servers.{SERVER_ID}]", f'provider = "{LOCAL_PROVIDER}"']
    argv = [RUNTIME_PYTHON, SCRIPTED_SERVER, "--port", "{port}", "--scenario", LOCAL_SCENARIO]
    lines += [f"argv = {json.dumps(argv)}", f"port = {SERVER_PORT}", 'health_path = "/models"']
    lines += ["startup_timeout_s = 60.0", f"sources = {json.dumps(list(sources))}"]
    lines += ["load_on_start = false", ""]
    for source in sources:
        lines += _map_row(source)
    if supervision:
        lines += [supervision, ""]
    return "\n".join(lines)


def _map_row(source: str) -> list[str]:
    """The `[forage.map.<source>]` row for one model: a second-rung model that runs on the CPU.

    It declares no VRAM, which is how a model says it needs none: the Queen promotes a device only
    if the models she allows fit the VRAM it has free, and a container has no GPU at all.
    """
    row = [
        f"[forage.map.{source}]",
        f'provider = "{LOCAL_PROVIDER}"',
        f'model = "{MODELS[source]}"',
    ]
    return [*row, "grade = 2", "context_window = 8192", f"seats = {SEATS}", ""]


def shared_scenario(rounds: int, seconds: int, report: str) -> dict[str, object]:
    """The rounds a Drone makes on the shared server: `rounds` commands, then the report.

    Args:
        rounds: How many commands to run before the report is written.
        seconds: How long each takes.
        report: The file to write last, which the task's acceptance criterion names.

    Returns:
        The scenario's fields, as the scripted server's `--scenario` file spells them.
    """
    steps: list[dict[str, object]] = [
        {"tool": "run_command", "arguments": {"argv": ["sleep", str(seconds)]}}
    ] * rounds
    steps.append({"tool": "write_file", "arguments": {"path": report, "content": "done"}})
    return {"steps": steps, "closing": "Finished the work on this device."}


def local_scenario(rounds: int, seconds: int, report: str) -> dict[str, object]:
    """The rounds a Drone makes on the device's own server, and what a diagnosing Drone ends with.

    The same rounds as `shared_scenario`, with a closing line that begins `CAUSE:`, which is how
    the Drone the Warden sends to find out why its link went reports what it found: any Drone on
    this server ends that way, and a task's Drone takes it as its closing words.
    """
    scenario = shared_scenario(rounds, seconds, report)
    scenario["closing"] = "CAUSE: none found\nThe Hive Stand cannot be reached from here."
    return scenario


def scenario_text(scenario: dict[str, object]) -> str:
    """Spell a scenario as the JSON file the scripted server reads."""
    return json.dumps(scenario, indent=1)
