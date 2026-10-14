"""Script the Drones of the diagnosis scenarios: what each looks at, what it changes, how it ends.

Three Drones are scripted here, and each is the real role on a real container (or the Hive Stand)
running real commands; only the choice of commands is a script, because the container has no
model. The Nuc's model server answers two Drones as the same model: the task's, and the one its
Warden sends when the link goes (roadmap 11.9a), so a step carries `when`, the words only its
Drone's objective holds (`e2e.scripted_openai.Step`). `mend_hosts` is the diagnosis of a wrong line
in `/etc/hosts`: look the name up, find the line, write the right one back, look again, and say what
was wrong in the closing line that starts `CAUSE:`, which is how the Warden reads what it learned.
`lost_network` is the diagnosis of a container with no network left: it looks at the interfaces, the
routes and the gateway, finds nothing to fix, and says so. `stand_check` is the Hive Stand's own
Drone when the Queen asks it to look into a lost device: one check, the findings file it is asked
for, and a sentence. Every command writes what it printed to a file and ends with `echo`, because
the Drone's tool reports only whether a command worked and three failures stop a Drone.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 diagnosis
    scenarios; builds on `e2e.kernel_helpers` and the colony rig's rounds for the task's Drone.

Key invariants:
    - A step is for the task's Drone (`TASK_MARK`) or for the diagnostic one (`DIAGNOSIS_MARK`),
      never both, and the marks are words the objectives really hold (a unit test says so).
    - No command a diagnostic Drone runs can fail: a check ends in `echo`, so the three-failures
      rule never stops a scripted Drone.
    - The one fix is a write back through `cat >`, never a rename (`e2e.swarm.diagnose.plant`).

See Also:
    - hivemind.wardens.offline.diagnose.assign for the objective the Nuc's Drone is given.
    - hivemind.queen.swarm_gate.unreachable.hive_stand for the one the Hive Stand's is given.
"""

from __future__ import annotations

from collections.abc import Mapping

from e2e.kernel_helpers import text_response, tool_response, tool_round_count, write_call
from e2e.swarm.enrolled import HIVE_HOSTNAME
from e2e.swarm.hive import REPORT_FILE

from hivemind.llm import LLMRequest, LLMResponse
from hivemind.queen.swarm_gate.unreachable.hive_stand import FINDINGS_FILE

# The opening words of the objective a cut-off Warden gives its Drone (`diagnosis_objective`): a
# diagnostic Drone's conversation holds them and a task's does not.
DIAGNOSIS_MARK = "Diagnose and, if possible, restore the link to the Queen"
# The sentence of the task's objective (`single_task_plan`) that a diagnostic Drone never sees.
TASK_MARK = f"Write to {REPORT_FILE}"
MENDED = (
    f"CAUSE: /etc/hosts maps {HIVE_HOSTNAME} to an address nothing answers on.\n"
    "I looked the name up, found the bad line in /etc/hosts, wrote the right one back and looked "
    "again; it resolves to the Hive Stand now."
)
GONE = (
    "CAUSE: the container has no network but loopback, so no name and no route can reach the "
    "Hive Stand.\nI looked at the interfaces and the routes and tried the gateway; nothing on this "
    "machine can bring a network back."
)
FINDINGS = "Looked from the Hive Stand: names resolve here. The fault looks like that device."
CHECKED = "The fault looks like that device, not this end of the link."

__all__ = [
    "CHECKED",
    "DIAGNOSIS_MARK",
    "FINDINGS",
    "GONE",
    "MENDED",
    "TASK_MARK",
    "lost_network",
    "mend_hosts",
    "stand_check",
]


def mend_hosts(task: Mapping[str, object], gateway: str) -> dict[str, object]:
    """The device model's rounds when the fault is a wrong line in `/etc/hosts`.

    Args:
        task: The rounds the task's Drone makes (`colony.models.local_scenario`); kept, for that
            Drone only.
        gateway: The address the Hive's name is meant to resolve to: the network's host address.

    Returns:
        The scenario's fields: the task's rounds, then the diagnosis that mends the line, and the
        closing line that names the cause.
    """
    name, hosts = HIVE_HOSTNAME, "/etc/hosts"
    rounds = [
        _record(f"getent hosts {name}", "look.txt"),
        _record(f"grep -n {name} {hosts}", "find.txt"),
        # Every other line, then the right one, written back through the same file.
        f"grep -v {name} {hosts} > kept.txt; printf '%s\\t%s\\n' {gateway} {name} >> kept.txt; "
        f"cat kept.txt > {hosts}; echo exit=$? > fix.txt",
        _record(f"getent hosts {name}", "again.txt"),
    ]
    return _scenario(task, rounds, MENDED)


def lost_network(task: Mapping[str, object], gateway: str) -> dict[str, object]:
    """The device model's rounds when the container has no network left to mend.

    Args:
        task: The rounds the task's Drone makes; kept, for that Drone only.
        gateway: The address of the Hive Stand's end of the cable, which the Drone tries.

    Returns:
        The scenario's fields: the task's rounds, then a look at the interfaces, the routes and
        the gateway, and a closing line that names the cause and says nothing can be done.
    """
    rounds = [
        "ip -brief addr > net.txt 2>&1; ip route >> net.txt 2>&1; echo exit=$? >> net.txt",
        _record(f"ping -c 1 -W 2 {gateway}", "ping.txt"),
        _record(f"getent hosts {HIVE_HOSTNAME}", "name.txt"),
    ]
    return _scenario(task, rounds, GONE)


def stand_check(request: LLMRequest) -> LLMResponse:
    """The Hive Stand's Drone when the Queen asks it to look into a lost device, scripted.

    Args:
        request: One model call of that Drone.

    Returns:
        A look (a command that writes its output to a file), then the findings file the task asks
        for, then a closing sentence: the three things the objective tells the Drone to do.
    """
    done = tool_round_count(request)
    if done == 0:
        look = (
            "look",
            "run_command",
            {"argv": ["sh", "-c", _record("getent hosts localhost", "look.txt")]},
        )
        return tool_response(request, (look,))
    if done == 1:
        return tool_response(request, (write_call(FINDINGS_FILE, FINDINGS),))
    return text_response(CHECKED)


def _scenario(task: Mapping[str, object], rounds: list[str], closing: str) -> dict[str, object]:
    """Join the task's rounds, for the task's Drone, to the diagnosis `rounds`, for the other's."""
    steps = task["steps"]
    assert isinstance(steps, list)
    mine = [{**step, "when": TASK_MARK} for step in steps]
    theirs = [_shell(script) for script in rounds]
    return {"steps": [*mine, *theirs], "closing": closing}


def _shell(script: str) -> dict[str, object]:
    """One round of the diagnostic Drone: `script` run by `sh`, in scratch."""
    arguments = {"argv": ["sh", "-c", script]}
    return {"tool": "run_command", "arguments": arguments, "when": DIAGNOSIS_MARK}


def _record(command: str, name: str) -> str:
    """A check as the objective teaches it: its output goes to a file and it always succeeds."""
    return f"{command} > {name} 2>&1; echo exit=$? >> {name}"
