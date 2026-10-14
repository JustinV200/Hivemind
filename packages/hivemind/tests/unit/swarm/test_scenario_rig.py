"""Unit: the parts of the container scenarios' rig that need no docker daemon.

The phase 11 exit scenarios (`tests/e2e/test_phase11_exit_*.py`) prove the rig whole against real
containers. What can go wrong before a container exists is checked here in milliseconds, on every
run: what a snapshot counts as the Hive's write (the one place a mistake would let a write go
unseen), how the `find` and `docker diff` listings are read, the order of the commands that make
a tunnel, how the scripted Drone picks its command for each attempt, the plan and ceiling it is
given, and what the bridge Hive's manifest says (the bridge address, TLS, a slow beat, a Drone
that costs no cores).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises `e2e.swarm.snapshots`,
    `.tunnel`, `.drone` and `.bridge` and `builders.swarm.manifest`; nothing here starts a
    container or touches a network.

Key invariants:
    - A container is replaced by a recorder, so no test here depends on a daemon being present.
    - No test sleeps.

See Also:
    - tests/e2e/test_phase11_exit_propolis.py and its siblings for the rig run for real.
    - tests/unit/swarm/test_device_rig.py for the container half of the rig.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from builders.cli import ManifestTuning
from builders.llm import make_request, make_tool
from builders.swarm.manifest import swarm_manifest, tuned_swarm_manifest
from e2e.kernel_helpers import text_response
from e2e.swarm.bridge import BRIDGE_BEAT_S, BridgeTerms, bridge_hive
from e2e.swarm.container import CommandResult, Container, FileEntry, Network
from e2e.swarm.drone import Script, command_turn, pinned_ceiling, plan_for
from e2e.swarm.hive import HELLO, REPORT_FILE
from e2e.swarm.snapshots import (
    INIT_BINARY,
    OPERATOR_DIRS,
    PACKET_INSTALL,
    WrittenFile,
    files_under,
    operator_files,
    unexplained,
    written_files,
)
from e2e.swarm.tunnel import (
    RESOLVER_FILE,
    TUN_NODE,
    TUNNEL_NAMESERVER,
    NetworkState,
    network_state,
    raise_tunnel,
)

from hivemind.forage import ModelSlot
from hivemind.llm import LLMResponse, Message, Role, ToolCallPart, ToolResultPart
from hivemind.manifest import load_manifest

DATA = "/var/lib/pollen"
FIND_ROW = "%y %s %T@ %p\\n"  # The `-printf` format `written_files` reads back.


class FakeBox:
    """Stands in for a `Container`: answers `exec` from a table and records every command."""

    def __init__(
        self,
        answers: Mapping[tuple[str, ...], str] | None = None,
        present: Sequence[str] = (),
        tree: Mapping[str, FileEntry] | None = None,
    ) -> None:
        """Answer an `exec` whose argv is a key of `answers`; `present` paths `exists`."""
        self.commands: list[tuple[str, ...]] = []
        self.checks: dict[tuple[str, ...], bool] = {}
        self._answers = answers or {}
        self._present = set(present)
        self._tree = dict(tree or {})

    def exec(self, argv: Sequence[str], *, check: bool = True) -> CommandResult:
        """Record the command, and whether a failure of it would have raised."""
        key = tuple(argv)
        self.commands.append(key)
        self.checks[key] = check
        return CommandResult(key, 0, self._answers.get(key, ""), "")

    def exists(self, path: str) -> bool:
        """Whether the test said `path` is there."""
        return path in self._present

    def file_tree(self, path: str) -> dict[str, FileEntry]:
        """The tree the test gave, whatever `path` is."""
        return dict(self._tree)

    @property
    def box(self) -> Container:
        """This fake, for the functions that take a Container."""
        return cast(Container, self)


# ──────────────────────────────────────────────────────────────────────────────
# What counts as the Hive's write
# ──────────────────────────────────────────────────────────────────────────────


def test_a_write_under_an_allowed_directory_is_explained_with_the_directories_above_it() -> None:
    layer = ["C /var", "C /var/lib", f"A {DATA}", f"A {DATA}/key", f"D {DATA}/old"]

    assert unexplained(layer, DATA) == []


def test_the_init_process_the_janitor_adds_is_explained_and_nothing_else_is() -> None:
    stray = ["A /etc/stray", "C /var/log", "A /var/log/x"]

    assert unexplained(["C /usr", "C /usr/sbin", f"A {INIT_BINARY}", *stray], DATA) == stray


def test_a_directory_that_only_shares_a_prefix_with_an_allowed_one_is_not_under_it() -> None:
    layer = [f"A {DATA}-evil/key", f"A {DATA}x"]

    assert unexplained(layer, DATA) == layer


def test_only_a_changed_directory_is_excused_as_a_parent_never_an_added_one() -> None:
    # An added `/var/lib` would be a directory the Hive made, not one that merely holds the
    # packet's data.
    assert unexplained(["A /var/lib", "C /var"], DATA) == ["A /var/lib"]


# ──────────────────────────────────────────────────────────────────────────────
# Reading the container's files
# ──────────────────────────────────────────────────────────────────────────────


def _find(*roots: str) -> tuple[str, ...]:
    """The `find` command `written_files` runs over `roots`."""
    return ("find", *roots, "-mindepth", "1", "-printf", FIND_ROW)


def test_a_regular_file_carries_its_size_and_time_and_anything_else_only_its_kind() -> None:
    listing = "f 12 100.5 /etc/hostname\nd 4096 7.0 /etc/with space\nl 9 3.0 /etc/link\n"
    box = FakeBox({_find("/etc", "/opt"): listing})

    tree = written_files(box.box, "/etc", "/opt")

    assert tree == {
        "/etc/hostname": WrittenFile(12, 100_500_000_000),
        "/etc/with space": "d",
        "/etc/link": "l",
    }
    # A root that is not there makes `find` complain about it, which must not fail the snapshot.
    assert box.checks[_find("/etc", "/opt")] is False


def test_the_packets_own_install_is_left_out_of_the_operators_files() -> None:
    names = ["/opt/pollen", "/opt/pollen/bin/python3", "/opt/pollenx", "/opt/other", "/etc/hosts"]
    listing = "".join(f"f 1 1.0 {name}\n" for name in names)

    kept = operator_files(FakeBox({_find(*OPERATOR_DIRS): listing}).box)

    assert PACKET_INSTALL == "/opt/pollen"
    assert sorted(kept) == ["/etc/hosts", "/opt/other", "/opt/pollenx"]


def test_no_directory_and_an_empty_one_both_say_the_lease_left_nothing_behind() -> None:
    entry = FileEntry("f", "644", 3)

    assert files_under(FakeBox(present=[DATA], tree={"a": entry}).box, DATA) == {"a": entry}
    assert files_under(FakeBox(tree={"a": entry}).box, DATA) == {}


# ──────────────────────────────────────────────────────────────────────────────
# The operator's tunnel
# ──────────────────────────────────────────────────────────────────────────────


def _index(commands: Sequence[tuple[str, ...]], *words: str) -> int:
    """Where in `commands` the first one that starts with `words` is."""
    return next(i for i, command in enumerate(commands) if command[: len(words)] == words)


def test_the_tunnel_is_made_in_the_order_a_vpn_client_makes_it_and_the_resolver_in_place() -> None:
    box = FakeBox()

    raise_tunnel(box.box)

    steps = [
        _index(box.commands, "mknod", TUN_NODE),
        _index(box.commands, "ip", "tuntap"),
        _index(box.commands, "ip", "addr"),
        _index(box.commands, "ip", "link"),
        _index(box.commands, "ip", "route", "replace", "default"),
        _index(box.commands, "sh", "-c"),
    ]
    assert steps == sorted(steps)
    # Redirected into the file Docker bind-mounts, not renamed over it, which it refuses.
    assert box.commands[steps[-1]][-1] == f"echo 'nameserver {TUNNEL_NAMESERVER}' > {RESOLVER_FILE}"


def test_a_device_node_that_is_already_there_is_not_made_again() -> None:
    box = FakeBox(present=[TUN_NODE])

    raise_tunnel(box.box)

    assert not [command for command in box.commands if command[0] == "mknod"]


def test_the_network_state_is_what_the_propolis_checks_read_and_a_missing_table_is_empty() -> None:
    box = FakeBox({("cat", "/proc/net/route"): "routes", ("cat", RESOLVER_FILE): "resolver"})

    state = network_state(box.box)

    assert state == NetworkState("routes", "", "resolver")
    assert box.checks[("cat", "/proc/net/ipv6_route")] is False  # Not every kernel has the file.
    assert box.checks[("cat", "/proc/net/route")] is True


# ──────────────────────────────────────────────────────────────────────────────
# The scripted Drone
# ──────────────────────────────────────────────────────────────────────────────


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


def test_each_attempt_runs_its_own_command_and_the_last_one_is_run_by_every_attempt_after() -> None:
    turn = command_turn(("sleep", "100"), ("true",))
    tools = (make_tool(),)

    first = _calls(turn(make_request(tools=tools)))
    closing = _calls(turn(make_request(tools=tools, messages=(_results("ok", "ok"),))))
    again = _calls(turn(make_request(tools=tools)))
    later = _calls(turn(make_request(tools=tools)))

    assert first["run_command"]["argv"] == ["sleep", "100"]
    assert first["write_file"] == {"path": REPORT_FILE, "content": HELLO}
    assert closing == {}  # After its tools ran the Drone only says so.
    assert again["run_command"]["argv"] == ["true"]
    assert later["run_command"]["argv"] == ["true"]


def _needs(plan: Mapping[str, object]) -> object:
    """The needs of a plan's one task."""
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    return tasks[0]["needs"]


def test_a_plan_carries_the_needs_the_test_chose_and_each_plan_is_its_own() -> None:
    plain = plan_for()
    propolis = plan_for({"comb_shield": "PROPOLIS"})

    assert _needs(plain) == {}
    assert _needs(propolis) == {"comb_shield": "PROPOLIS"}
    assert REPORT_FILE in str(plain["tasks"])  # What the Warden looks for to accept the task.


def test_a_script_plans_the_goal_with_the_plan_the_test_holds_when_the_queen_asks() -> None:
    script = Script(command_turn(("true",)), plan_for())
    script.plan = plan_for({"comb_shield": "NIGHT_VEIL"})

    planned = script.responder(make_request(slot=ModelSlot.QUEEN))

    assert "NIGHT_VEIL" in "".join(getattr(part, "text", "") for part in planned.parts)


def test_the_goal_ceiling_names_the_one_cell_and_no_other() -> None:
    ceiling = pinned_ceiling("cell_01XYZ")

    assert [item for item in ceiling if item.startswith("cell:real:")] == ["cell:real:cell_01XYZ"]
    assert "cell:hive_stand" not in ceiling and "exec:*" in ceiling


# ──────────────────────────────────────────────────────────────────────────────
# The Hive a container dials
# ──────────────────────────────────────────────────────────────────────────────


class LoopbackBridge(Network):
    """A network whose host address is this machine's, so no docker daemon is asked."""

    def __init__(self) -> None:
        """Name it; the address is fixed."""
        super().__init__("unit-test-network")

    @property
    def gateway(self) -> str:
        """Loopback: the manifest only has to bind somewhere that exists."""
        return "127.0.0.1"


def test_the_bridge_hive_listens_on_the_bridge_over_tls_beats_slowly_and_costs_the_drone_no_cores(
    tmp_path: Path,
) -> None:
    terms = BridgeTerms({"dead_man_s": 7.0}, watch_table="interval_s = 0.2\n")

    hive = bridge_hive(tmp_path, LoopbackBridge(), lambda request: text_response("{}"), terms)

    manifest = load_manifest(tmp_path / "hive.toml", {})
    swarm = manifest.swarm
    assert hive.swarm is not None and swarm.enabled
    assert (swarm.listen_host, swarm.dead_man_s) == ("127.0.0.1", 7.0)
    assert swarm.advertise_url == f"wss://hive.test:{swarm.listen_port}"
    assert Path(swarm.tls_cert).is_file() and Path(swarm.tls_key).is_file()
    # The Hive calls a device lost after four silent beats: a one-second stall must not do it.
    assert swarm.heartbeat_interval_s == BRIDGE_BEAT_S and 4 * BRIDGE_BEAT_S > 1.0
    assert manifest.forage.roles["drone"].cpu_cores == 0.0
    assert manifest.supervision.watch.interval_s == 0.2


def test_a_swarm_manifest_keeps_the_half_core_drone_unless_a_test_says_otherwise(
    tmp_path: Path,
) -> None:
    (tmp_path / "ordinary").mkdir()
    (tmp_path / "free").mkdir()

    ordinary = load_manifest(swarm_manifest(tmp_path / "ordinary"), {})
    free = load_manifest(
        tuned_swarm_manifest(tmp_path / "free", ManifestTuning(drone_cpu_cores=0.0)), {}
    )

    assert ordinary.forage.roles["drone"].cpu_cores == 0.5
    assert free.forage.roles["drone"].cpu_cores == 0.0
