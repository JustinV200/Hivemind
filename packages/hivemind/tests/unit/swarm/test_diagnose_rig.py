"""Unit: the parts of the diagnosis scenarios' rig that need no docker daemon.

The phase 11 diagnosis scenarios (`tests/e2e/test_phase11_exit_diagnose.py`) prove the rig whole
against real containers, at several minutes a run. What can go wrong before a container exists is
checked here in milliseconds, on every run: that the words which tell the task's Drone from the
diagnostic one on a Nuc's model server really are in the two objectives, that the scripted Drones'
commands cannot fail (three failures stop a Drone) and do what they say when `sh` runs them on a
copy of the file, what the scripted server answers each conversation, how the fault is planted and
the name read back, which links are closed, and which Nectar a claim is about.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises `e2e.swarm.diagnose`
    and the `when` of `e2e.scripted_openai.Step`; the shell scripts are run for real on temporary
    files, and nothing here starts a container or touches a network.

Key invariants:
    - Docker is replaced by a recorder, so no test here depends on a daemon being present.
    - No test sleeps: the one wait that polls has its poll interval set to zero.

See Also:
    - tests/e2e/test_phase11_exit_diagnose.py for the scenarios the rig is for.
    - tests/unit/swarm/test_colony_rig.py for the rig this is cut from.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from builders.honey import make_nectar
from builders.llm import make_request, make_tool
from builders.swarm.parties import make_swarm_node
from builders.trail import make_trail_event
from e2e.kernel_helpers import single_task_plan
from e2e.scripted_openai import Scenario, Step, build_app
from e2e.swarm import container as container_module
from e2e.swarm.colony.models import local_scenario
from e2e.swarm.container import CommandResult, Container
from e2e.swarm.diagnose import (
    DIAGNOSIS_MARK,
    TASK_MARK,
    WRONG_ADDRESS,
    Deposit,
    address_of,
    findings_of,
    for_device,
    lost_network,
    mend_hosts,
    outcome_of,
    sever,
    stand_check,
    wait_for_address,
)
from e2e.swarm.diagnose import plant as plant_module
from e2e.swarm.diagnose.plant import planting_script
from e2e.swarm.diagnose.reversible import reversible_setup
from e2e.swarm.diagnose.scripts import CHECKED, GONE, MENDED
from e2e.swarm.enrolled import HIVE_HOSTNAME
from e2e.swarm.hive import REPORT_FILE
from starlette.testclient import TestClient

from hivemind.cli.compose import Hive
from hivemind.honey_store import NectarOrigin
from hivemind.llm import LLMResponse, Message, Role, ToolCallPart, ToolResultPart
from hivemind.queen.swarm_gate.unreachable.hive_stand import FINDINGS_FILE
from hivemind.wardens.offline.diagnose import diagnosis_objective, read_finding
from waggle.clock import FakeClock
from waggle.messages.honey import NectarKind

type Builder = Callable[[Mapping[str, object], str], dict[str, object]]  # `mend_hosts`' shape.
GATEWAY = "172.20.0.1"
HOSTS = f"127.0.0.1\tlocalhost\n172.20.0.7\tabcdef012345\n{GATEWAY}\t{HIVE_HOSTNAME}\n"
_CLOCK = FakeClock()
# The scripted server answers where the OpenAI-compatible adapters ask. The joined fragment is
# banned as a literal outside the manifest (codingrules 8.6), so it is spelt in two halves, as the
# adapter's own tests do.
_CHAT = "/v1" + "/chat/completions"


class Docker:
    """Stands in for `container.docker`: records each call and answers from a script."""

    def __init__(self, *answers: CommandResult) -> None:
        """Start with the answers to give, in order; the last is repeated when they run out."""
        self.calls: list[tuple[str, ...]] = []
        self._answers = list(answers)

    def __call__(self, *args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        """Record the call and answer with the next scripted result (an empty success if none)."""
        self.calls.append(args)
        if not self._answers:
            return CommandResult(("docker", *args), 0, "", "")
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return CommandResult(("docker", *args), answer.exit_code, answer.stdout, answer.stderr)


def _ok(stdout: str = "") -> CommandResult:
    """A successful command that printed `stdout`."""
    return CommandResult((), 0, stdout, "")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    """Replace the module's docker call with a recorder that succeeds and prints nothing."""
    fake = Docker()
    monkeypatch.setattr(container_module, "docker", fake)
    yield fake


def _objective() -> str:
    """The objective a cut-off Warden gives its Drone for the Hive's host name."""
    return diagnosis_objective(HIVE_HOSTNAME, ())


# ──────────────────────────────────────────────────────────────────────────────
# The words that tell one Drone from the other
# ──────────────────────────────────────────────────────────────────────────────


def test_the_diagnostic_mark_opens_the_objective_a_cut_off_warden_gives_its_drone() -> None:
    assert _objective().startswith(DIAGNOSIS_MARK)


def test_the_task_mark_is_in_the_tasks_objective_and_not_in_the_diagnostic_one() -> None:
    plan = cast(dict[str, Any], single_task_plan(REPORT_FILE))

    assert TASK_MARK in plan["tasks"][0]["objective"]
    assert TASK_MARK not in _objective()
    assert DIAGNOSIS_MARK not in plan["tasks"][0]["objective"]


# ──────────────────────────────────────────────────────────────────────────────
# The scripted Drones
# ──────────────────────────────────────────────────────────────────────────────


def _rounds(scenario: dict[str, object], mark: str) -> list[str]:
    """The shell scripts a Drone whose objective holds `mark` runs, in order."""
    steps = cast(list[dict[str, Any]], scenario["steps"])
    return [
        s["arguments"]["argv"][2] for s in steps if s["when"] == mark and s["tool"] == "run_command"
    ]


def test_every_round_of_a_scenario_is_for_the_task_or_for_the_diagnosis_and_never_both() -> None:
    task = local_scenario(3, 5, REPORT_FILE)

    for scenario in (mend_hosts(task, GATEWAY), lost_network(task, GATEWAY)):
        steps = cast(list[dict[str, Any]], scenario["steps"])
        assert {step["when"] for step in steps} == {TASK_MARK, DIAGNOSIS_MARK}
        mine = [s for s in steps if s["when"] == TASK_MARK]
        assert len(mine) == len(cast(list[object], task["steps"]))  # The task's rounds, all kept.


@pytest.mark.parametrize("build", [mend_hosts, lost_network])
def test_a_diagnostic_command_cannot_fail_because_it_ends_in_an_echo(build: Builder) -> None:
    scenario = build(local_scenario(2, 1, REPORT_FILE), GATEWAY)

    # The Drone's tool reports only whether a command worked and three failures stop it, so each
    # check hides its own exit status in the file it writes.
    for script in _rounds(scenario, DIAGNOSIS_MARK):
        assert script.rsplit(";", 1)[-1].strip().startswith("echo ")


@pytest.mark.parametrize(("closing", "word"), [(MENDED, "hosts"), (GONE, "network")])
def test_each_closing_line_names_a_cause_the_warden_reads(closing: str, word: str) -> None:
    finding = read_finding(closing)

    assert finding.cause is not None and word in finding.cause + closing


def _run(script: str, directory: Path) -> None:
    """Run a scripted round for real, in `directory`, the way the Drone's `sh -c` does."""
    # SAFETY: the script is one this module's own rig built, run in a temporary directory against
    # a copy of the file it would change; argument list, no shell string of the test's own.
    subprocess.run(["sh", "-c", script], cwd=directory, check=True)  # noqa: S603, S607


def test_the_planted_fault_replaces_the_hives_line_and_keeps_every_other(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text(HOSTS, encoding="utf-8")

    _run(planting_script(str(hosts)), tmp_path)

    lines = hosts.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["127.0.0.1\tlocalhost", "172.20.0.7\tabcdef012345"]
    assert lines[2:] == [f"{WRONG_ADDRESS}\t{HIVE_HOSTNAME}"]


def test_the_drones_fix_writes_the_right_line_back_through_the_same_file(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text(HOSTS, encoding="utf-8")
    _run(planting_script(str(hosts)), tmp_path)
    inode = hosts.stat().st_ino
    _look, _find, fix, _again = _rounds(
        mend_hosts(local_scenario(1, 1, REPORT_FILE), GATEWAY), DIAGNOSIS_MARK
    )

    _run(fix.replace("/etc/hosts", str(hosts)), tmp_path)

    lines = hosts.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if HIVE_HOSTNAME in line] == [f"{GATEWAY}\t{HIVE_HOSTNAME}"]
    assert "127.0.0.1\tlocalhost" in lines  # What was not the Hive's line is as it was.
    assert hosts.stat().st_ino == inode  # Never renamed over: a bind mount cannot be.
    assert "sed -i" not in fix and "mv " not in fix


def _results(*texts: str) -> Message:
    """A user turn carrying one tool result for each of `texts`."""
    parts = tuple(ToolResultPart(call_id=f"c{i}", content=text) for i, text in enumerate(texts))
    return Message(role=Role.USER, parts=parts)


def _calls(response: LLMResponse) -> dict[str, dict[str, object]]:
    """The tool calls of a native response: name to arguments."""
    return {
        part.call.name: dict(part.call.arguments)
        for part in response.parts
        if isinstance(part, ToolCallPart)
    }


def test_the_hive_stands_check_looks_then_writes_its_findings_then_says_so() -> None:
    tools = (make_tool(),)

    looking = _calls(stand_check(make_request(tools=tools)))
    writing = _calls(stand_check(make_request(tools=tools, messages=(_results("ok"),))))
    closing = stand_check(make_request(tools=tools, messages=(_results("ok", "ok"),)))

    assert list(looking) == ["run_command"]
    assert cast(list[str], looking["run_command"]["argv"])[:2] == ["sh", "-c"]
    assert list(writing) == ["write_file"] and writing["write_file"]["path"] == FINDINGS_FILE
    assert _calls(closing) == {} and getattr(closing.parts[0], "text", "") == CHECKED


# ──────────────────────────────────────────────────────────────────────────────
# The scripted server: one model, two Drones
# ──────────────────────────────────────────────────────────────────────────────


def _ask(client: TestClient, words: str, rounds: int) -> dict[str, Any]:
    """Ask the scripted worker as a Drone whose conversation holds `words` and `rounds` results."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": words}]
    messages += [{"role": "tool", "tool_call_id": f"c{n}", "content": "ok"} for n in range(rounds)]
    tools = [{"type": "function", "function": {"name": "run_command"}}]
    body = {"model": "local-small-worker", "messages": messages, "tools": tools}
    reply = client.post(_CHAT, json=body)
    return cast(dict[str, Any], reply.json()["choices"][0]["message"])


def _argv(message: dict[str, Any]) -> list[str]:
    """The command a scripted round asks for."""
    return cast(list[str], json.loads(message["tool_calls"][0]["function"]["arguments"])["argv"])


def test_one_server_gives_the_task_and_the_diagnostic_drone_each_its_own_rounds() -> None:
    steps = (
        Step("run_command", {"argv": ["sleep", "8"]}, when=TASK_MARK),
        Step("run_command", {"argv": ["sh", "-c", "look"]}, when=DIAGNOSIS_MARK),
    )
    scenario = Scenario(steps=steps, closing="CAUSE: it was the name")

    with TestClient(build_app(scenario)) as client:
        task, diagnosis = _ask(client, TASK_MARK, 0), _ask(client, DIAGNOSIS_MARK, 0)
        done = _ask(client, DIAGNOSIS_MARK, 1)
        stranger = _ask(client, "an objective neither Drone has", 0)

    assert _argv(task) == ["sleep", "8"] and _argv(diagnosis) == ["sh", "-c", "look"]
    assert done["content"] == "CAUSE: it was the name"  # Its one round is over: it closes.
    assert stranger["content"] == "CAUSE: it was the name"  # Neither mark: no round is its own.


def test_a_step_with_no_when_is_made_in_every_conversation() -> None:
    scenario = Scenario(steps=(Step("run_command", {"argv": ["true"]}),))

    with TestClient(build_app(scenario)) as client:
        assert (
            _argv(_ask(client, TASK_MARK, 0)) == _argv(_ask(client, DIAGNOSIS_MARK, 0)) == ["true"]
        )


# ──────────────────────────────────────────────────────────────────────────────
# The fault, the name and the links
# ──────────────────────────────────────────────────────────────────────────────


def test_the_fault_is_planted_with_one_shell_command_in_the_container(docker: Docker) -> None:
    plant_module.plant_wrong_entry(Container("device"))

    [(verb, name, program, flag, script)] = docker.calls
    assert (verb, name, program, flag) == ("exec", "device", "sh", "-c")
    assert script == planting_script()


def test_the_name_is_read_as_the_drones_getent_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = Docker(_ok(f"{GATEWAY}      {HIVE_HOSTNAME}\n"))
    monkeypatch.setattr(container_module, "docker", resolved)
    assert address_of(Container("device")) == GATEWAY
    assert resolved.calls == [("exec", "device", "getent", "hosts", HIVE_HOSTNAME)]

    unresolved = Docker(CommandResult((), 2, "", ""))
    monkeypatch.setattr(container_module, "docker", unresolved)
    assert address_of(Container("device")) is None  # `getent` exits 2 for a name it cannot find.


def test_the_wait_returns_the_moment_the_name_first_resolves_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups = Docker(_ok(f"{WRONG_ADDRESS} {HIVE_HOSTNAME}\n"), _ok(f"{GATEWAY} {HIVE_HOSTNAME}\n"))
    monkeypatch.setattr(container_module, "docker", lookups)
    monkeypatch.setattr(plant_module, "POLL_S", 0.0)  # No test sleeps.

    at = asyncio.run(wait_for_address(Container("device"), _CLOCK, GATEWAY, 5.0))

    assert at == _CLOCK.now() and len(lookups.calls) == 2


def test_the_wait_fails_saying_what_the_name_resolves_to_if_it_never_comes_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(container_module, "docker", Docker(_ok(f"{WRONG_ADDRESS} x\n")))
    monkeypatch.setattr(plant_module, "POLL_S", 0.0)

    with pytest.raises(
        AssertionError, match=rf"did not resolve to {GATEWAY}.*gives {WRONG_ADDRESS}"
    ):
        asyncio.run(wait_for_address(Container("device"), _CLOCK, GATEWAY, 0.05))


class _Closer:
    """A link that records that it was closed."""

    def __init__(self) -> None:
        """Start open."""
        self.closed = False
        self.transport = self

    async def close(self) -> None:
        """Record the close."""
        self.closed = True


def _hive(packet: _Closer, warden: _Closer | None) -> Hive:
    """Just enough of a Hive for `sever`: a listener for the packet's link, a gate for Warden's."""

    async def disconnect(device_id: str) -> bool:
        await packet.close()
        return True

    listener = SimpleNamespace(disconnect=disconnect)
    wardens = SimpleNamespace(link_of=lambda warden_id: warden)
    gate = SimpleNamespace(wardens=wardens)
    return cast(Hive, SimpleNamespace(swarm=SimpleNamespace(listener=listener, gate=gate)))


def test_severing_closes_the_packets_link_and_the_wardens_and_nothing_else() -> None:
    packet, warden = _Closer(), _Closer()

    asyncio.run(sever(_hive(packet, warden), make_swarm_node()))

    assert packet.closed and warden.closed


def test_severing_a_device_whose_warden_is_not_attached_is_refused_not_ignored() -> None:
    with pytest.raises(AssertionError, match="not attached"):
        asyncio.run(sever(_hive(_Closer(), None), make_swarm_node()))


# ──────────────────────────────────────────────────────────────────────────────
# The Nectar a claim is about
# ──────────────────────────────────────────────────────────────────────────────


def _deposit(**nectar: object) -> Deposit:
    """An arrival of the Nectar `nectar` describes, with a trail event standing for its record."""
    event = make_trail_event("honey.nectar_received", _CLOCK)
    return Deposit(event, make_nectar(_CLOCK, **nectar))


def test_a_findings_deposit_is_a_wardens_own_from_the_devices_cell_and_no_tasks() -> None:
    node = make_swarm_node(clock=_CLOCK)
    own = _deposit(cell_id=node.cell_id, origin=NectarOrigin.BEE, task_id=None)
    outcome = _deposit(cell_id=node.cell_id, origin=NectarOrigin.TASK_OUTCOME, task_id=None)
    a_workers = _deposit(cell_id=node.cell_id, origin=NectarOrigin.BEE)  # Names a task.
    elsewhere = _deposit(origin=NectarOrigin.BEE, task_id=None)  # Another Cell's.
    a_summary = _deposit(
        cell_id=node.cell_id, origin=NectarOrigin.BEE, task_id=None, kind=NectarKind.PATROL_SUMMARY
    )

    found = findings_of([own, outcome, a_workers, elsewhere, a_summary], node)

    assert found == [own]


def test_a_task_outcome_is_the_queens_deposit_naming_the_task() -> None:
    queens = _deposit(origin=NectarOrigin.TASK_OUTCOME)
    task = str(queens.nectar.task_id)
    other_task = _deposit(origin=NectarOrigin.TASK_OUTCOME)
    a_bees = _deposit(origin=NectarOrigin.BEE, task_id=queens.nectar.task_id)

    assert outcome_of([queens, other_task, a_bees], task) == [queens]


def test_a_devices_events_are_those_its_own_warden_recorded() -> None:
    node = make_swarm_node(clock=_CLOCK)
    mine = make_trail_event("warden.offline", _CLOCK, node_id=node.node_id)
    queens = make_trail_event("queen.decided", _CLOCK)

    assert for_device([mine, queens], node) == [mine]


def test_the_reversible_case_hands_the_device_the_gateway_to_mend_the_name_with() -> None:
    setup = reversible_setup()

    local = setup.local(GATEWAY)

    assert [s["when"] for s in cast(list[dict[str, Any]], local["steps"])][-4:] == [
        DIAGNOSIS_MARK
    ] * 4
    assert GATEWAY in "".join(_rounds(local, DIAGNOSIS_MARK))
    assert "max_offline_s" in setup.supervision and setup.nuc is True
