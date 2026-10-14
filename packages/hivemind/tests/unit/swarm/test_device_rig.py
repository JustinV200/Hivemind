"""Unit: the parts of the swarm tests' device rig that need no docker daemon.

The rig in `tests/e2e/swarm` is proved whole by the container integration test, which needs a
daemon and a minute of building. What can go wrong before a container exists is checked here
in milliseconds, on every run: the parsers of the process and file snapshots, what a failing
command says (and does not), how the janitor names, labels and removes what it starts, which
pins of the lockfile reach the device, what makes the image tag change, and when a build is
pointed at a proxy.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises `e2e.swarm.container`
    and `e2e.swarm.image`; nothing here starts a container, builds an image or touches a network.

Key invariants:
    - Docker is replaced by a recorder, so no test here depends on a daemon being present.
    - No test sleeps; the one that waits on a process gives it a timeout a tenth of its length.

See Also:
    - tests/integration/test_pollen_in_container.py for the rig run for real.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from e2e.swarm import container as container_module
from e2e.swarm.container import (
    CommandError,
    CommandResult,
    ContainerSpec,
    FileEntry,
    Janitor,
    file_entries,
    process_table,
    run,
)
from e2e.swarm.image import device_pins, image_digest, proxy_flags

PIN = re.compile(r"^[a-z0-9][a-z0-9_.-]*==[0-9][^\s;]*$")  # `name==version`, no marker left on it.
BUNDLE = b"-----BEGIN CERTIFICATE-----\nproxy\n-----END CERTIFICATE-----\n"
PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


class FakeDocker:
    """Stands in for `container.docker`: records what was asked and answers from a table."""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        """Answer a call whose first two words are a key of `answers` with that text."""
        self.calls: list[tuple[str, ...]] = []
        self._answers = answers or {}

    def __call__(self, *args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        """Record the call and return the canned answer, or an empty success."""
        self.calls.append(args)
        stdout = self._answers.get(" ".join(args[:2]), "")
        return CommandResult(("docker", *args), 0, stdout, "")


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    """Replace the module's docker call with a recorder, for the janitor under test."""
    fake = FakeDocker({"ps --all": "one\ntwo\n", "network ls": "net\n"})
    monkeypatch.setattr(container_module, "docker", fake)
    return fake


def _values(call: tuple[str, ...], flag: str) -> list[str]:
    """The argument after each occurrence of `flag` in a docker call."""
    return [call[i + 1] for i, word in enumerate(call) if word == flag]


def test_the_process_probe_drops_its_own_row_and_keeps_command_names_that_hold_spaces() -> None:
    listing = "52\n    1 docker-init\n    7 python3\n   31 tmux: server\n   52 ps\n"

    assert process_table(listing) == {1: "docker-init", 7: "python3", 31: "tmux: server"}


def test_two_listings_of_a_quiet_container_are_equal_though_the_probe_has_another_pid() -> None:
    first = process_table("40\n 1 docker-init\n 7 python3\n 40 ps\n")
    second = process_table("43\n 1 docker-init\n 7 python3\n 43 ps\n")

    assert first == second


def test_a_file_listing_keeps_the_whole_path_after_its_three_fields() -> None:
    listing = "d 755 4096 leases\nf 644 12 leases/a file.txt\nl 777 9 link\n"

    assert file_entries(listing) == {
        "leases": FileEntry("d", "755", 4096),
        "leases/a file.txt": FileEntry("f", "644", 12),
        "link": FileEntry("l", "777", 9),
    }


def test_a_failed_command_is_named_by_its_first_words_and_never_by_its_secret_arguments() -> None:
    result = CommandResult(("docker", "run", "--env", "TOKEN=hunter2"), 125, "", "no such image")

    with pytest.raises(CommandError) as failure:
        result.require()

    assert "docker run --env" in str(failure.value)
    assert "no such image" in str(failure.value)
    assert "hunter2" not in str(failure.value)


def test_a_command_that_outlives_its_timeout_is_a_command_error_not_a_hang() -> None:
    with pytest.raises(CommandError, match="did not finish"):
        run(["sleep", "30"], 0.3)


def test_a_program_that_is_not_there_is_a_command_error() -> None:
    with pytest.raises(CommandError, match="could not be run"):
        run(["no-such-program-3f9a2c"], 5)


def test_a_command_reports_its_exit_code_and_both_streams() -> None:
    result = run(["sh", "-c", "echo out; echo err >&2; exit 4"], 10)

    assert (result.exit_code, result.stdout, result.stderr) == (4, "out\n", "err\n")


def test_the_janitor_starts_a_labelled_container_under_an_init_with_its_options(
    fake_docker: FakeDocker,
) -> None:
    janitor = Janitor()
    spec = ContainerSpec(
        image="device:1",
        command=("sh", "-c", "pollen run"),
        hosts={"hive.test": "172.17.0.1"},
        environment={"POLLEN_DATA_DIR": "/data"},
        network="net-a",
        capabilities=("NET_ADMIN",),
    )
    try:
        started = janitor.start(spec)
    finally:
        janitor.close()

    call = fake_docker.calls[0]
    assert call[:3] == ("run", "--detach", "--init")  # An init reaps what a lease leaves orphaned.
    assert _values(call, "--name") == [started.name]
    assert _values(call, "--add-host") == ["hive.test:172.17.0.1"]
    assert _values(call, "--env") == ["POLLEN_DATA_DIR=/data"]
    assert _values(call, "--network") == ["net-a"]
    assert _values(call, "--cap-add") == ["NET_ADMIN"]
    assert f"hivemind.test.run={janitor.run_id}" in _values(call, "--label")
    assert call[-4:] == ("device:1", "sh", "-c", "pollen run")  # The image, then its command.


def test_the_janitor_removes_what_carries_its_label_containers_before_networks(
    fake_docker: FakeDocker,
) -> None:
    janitor = Janitor()
    selector = f"label=hivemind.test.run={janitor.run_id}"

    janitor.close()

    assert fake_docker.calls == [
        ("ps", "--all", "--quiet", "--filter", selector),
        ("rm", "--force", "--volumes", "one", "two"),
        ("network", "ls", "--quiet", "--filter", selector),
        ("network", "rm", "net"),
    ]


def test_closing_the_janitor_when_nothing_was_started_removes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()  # Nothing carries the label, so the listings are empty.
    monkeypatch.setattr(container_module, "docker", fake)

    Janitor().close()

    assert [call[0] for call in fake.calls] == ["ps", "network"]


def test_the_device_pins_are_the_lockfiles_for_cpython_on_linux_and_no_other() -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is not on the path")

    pins = device_pins()
    names = {pin.split("==")[0] for pin in pins}

    assert pins == sorted(pins)
    assert all(PIN.match(pin) for pin in pins)
    assert {"pydantic", "websockets", "cryptography", "cffi", "psutil"} <= names
    assert not names & {"waggle", "pollen", "hivemind"}  # The workspace's own wheels are built.
    assert not names & {"colorama", "httpx2-jsfetch"}  # Windows-only and Pyodide-only pins.


def test_the_image_tag_follows_the_wheels_and_the_dockerfile_and_nothing_else(
    tmp_path: Path,
) -> None:
    wheels, dockerfile = tmp_path / "wheelhouse", tmp_path / "Dockerfile"
    wheels.mkdir()
    dockerfile.write_text("FROM ubuntu:24.04\n")
    (wheels / "a-1-py3-none-any.whl").write_bytes(b"first")
    (wheels / "b-2-py3-none-any.whl").write_bytes(b"second")
    baseline = image_digest(wheels, dockerfile)

    (wheels / ".gitignore").write_text("*")  # Not a wheel: no part of the image.
    assert image_digest(wheels, dockerfile) == baseline

    (wheels / "b-2-py3-none-any.whl").write_bytes(b"changed")
    changed_wheel = image_digest(wheels, dockerfile)
    (wheels / "b-2-py3-none-any.whl").write_bytes(b"second")
    dockerfile.write_text("FROM ubuntu:24.04\nRUN true\n")
    changed_dockerfile = image_digest(wheels, dockerfile)

    assert len({baseline, changed_wheel, changed_dockerfile}) == 3
    assert len(baseline) == 16


@pytest.fixture
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every variable the build's proxy handling reads."""
    for variable in PROXY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def _context(tmp_path: Path) -> Path:
    """A build context with the empty `ca` directory the Dockerfile mounts."""
    (tmp_path / "ca").mkdir(parents=True)
    return tmp_path


def test_a_proxy_on_the_host_loopback_builds_on_the_host_network_and_hands_over_its_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_proxy: None
) -> None:
    bundle = tmp_path / "bundle.crt"
    bundle.write_bytes(BUNDLE)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:38973")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    context = _context(tmp_path / "context")

    flags = proxy_flags(context)

    assert flags == ["--network", "host", "--build-arg", "APT_PROXY=http://127.0.0.1:38973"]
    assert (context / "ca" / "proxy.crt").read_bytes() == BUNDLE


@pytest.mark.parametrize(
    "proxy",
    ["", "http://proxy.example:3128", "http://user:secret@127.0.0.1:3128"],
    ids=["none", "off the host", "with credentials"],
)
def test_a_build_with_no_proxy_or_one_a_container_can_reach_or_one_that_needs_a_login_is_plain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_proxy: None, proxy: str
) -> None:
    if proxy:
        monkeypatch.setenv("HTTPS_PROXY", proxy)
    context = _context(tmp_path)

    assert proxy_flags(context) == []
    assert list((context / "ca").iterdir()) == []  # No certificate is copied for a plain build.
