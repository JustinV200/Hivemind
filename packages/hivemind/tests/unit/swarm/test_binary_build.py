"""Unit: what makes the packaged binary's cache key change, and how a build is run and kept.

The rig in `tests/e2e/swarm/binary` is proved whole by `tests/e2e/test_phase11_exit_binary.py`,
which needs a daemon and, the first time, a minute of building. What can go wrong before a
container exists is checked here in milliseconds on every run: what makes the binary's cache key
change (and what must not), that the recipe is the workflow's, how a build is pointed at a proxy,
and what a build does and leaves behind (a binary under its key, or nothing and an error that
carries the end of the build's output).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises
    `e2e.swarm.binary.build`; nothing here starts a container, builds a binary or touches a
    network.

Key invariants:
    - Docker is replaced by a recorder, so no test here depends on a daemon being present.
    - No test sleeps and none reads the real cache directory: each points it at its own.

See Also:
    - tests/unit/swarm/test_binary_device.py for the clean device, the Hive and the Drone.
    - tests/e2e/test_phase11_exit_binary.py for the rig run for real.
    - tests/unit/swarm/test_device_rig.py for the same treatment of the device image rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e.swarm import container as container_module
from e2e.swarm.binary import build as build_module
from e2e.swarm.binary.build import (
    BINARY_NAME,
    BUILD_ENVIRONMENT,
    BUILD_IMAGE,
    BUILD_SCRIPT,
    CA_BUNDLE_VARIABLES,
    CONTAINER_CA,
    REPO_ROOT,
    BuildNetwork,
    binary_digest,
    build_binary,
    build_network,
    frozen_inputs,
    stage_files,
)
from e2e.swarm.container import CommandError, CommandResult

BUNDLE = b"-----BEGIN CERTIFICATE-----\nproxy\n-----END CERTIFICATE-----\n"
PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", *CA_BUNDLE_VARIABLES)  # Everything the rig reads.
# A checkout in miniature: every kind of file the rig tells apart, one line each.
TREE = {
    "pyproject.toml": "root",
    "uv.lock": "lock",
    ".python-version": "3.12",
    "scripts/build_pollen.py": "script",
    "packages/waggle/pyproject.toml": "waggle project",
    "packages/waggle/src/waggle/__init__.py": "waggle code",
    "packages/waggle/tests/test_waggle.py": "waggle test",
    "packages/pollen/pyproject.toml": "pollen project",
    "packages/pollen/src/pollen/__init__.py": "pollen code",
    "packages/pollen/src/pollen/__pycache__/cli.cpython-312.pyc": "bytecode",
    "packages/pollen/tests/test_pollen.py": "pollen test",
    "packages/hivemind/pyproject.toml": "hivemind project",
    "packages/hivemind/src/hivemind/__init__.py": "hivemind code",
    "packages/hivemind/tests/test_hivemind.py": "hivemind test",
}


def _tree(root: Path) -> Path:
    """Write `TREE` under `root` and return it."""
    for name, text in TREE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _call_values(call: tuple[str, ...], flag: str) -> list[str]:
    """The argument after each occurrence of `flag` in a docker call."""
    return [call[i + 1] for i, word in enumerate(call) if word == flag]


# ──────────────────────────────────────────────────────────────────────────────
# The key
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "scripts/build_pollen.py",
        "packages/waggle/src/waggle/__init__.py",
        "packages/pollen/src/pollen/__init__.py",
        "packages/pollen/pyproject.toml",
        "packages/hivemind/pyproject.toml",
    ],
)
def test_the_key_changes_with_anything_the_binary_is_made_from(tmp_path: Path, name: str) -> None:
    root = _tree(tmp_path)
    before = binary_digest(root)

    (root / name).write_text("changed", encoding="utf-8")

    assert binary_digest(root) != before


@pytest.mark.parametrize(
    "name",
    [
        "packages/hivemind/src/hivemind/__init__.py",
        "packages/hivemind/tests/test_hivemind.py",
        "packages/pollen/tests/test_pollen.py",
        "packages/waggle/tests/test_waggle.py",
        "packages/pollen/src/pollen/__pycache__/cli.cpython-312.pyc",
    ],
)
def test_the_key_ignores_what_is_not_frozen_tests_bytecode_and_the_hives_code(
    tmp_path: Path, name: str
) -> None:
    root = _tree(tmp_path)
    before = binary_digest(root)

    (root / name).write_text("changed", encoding="utf-8")

    assert binary_digest(root) == before  # The binary excludes hivemind: its code is no input.


def test_a_new_file_in_a_frozen_package_changes_the_key_and_a_new_test_does_not(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    before = binary_digest(root)

    (root / "packages/pollen/tests/test_new.py").write_text("x", encoding="utf-8")
    assert binary_digest(root) == before
    (root / "packages/pollen/src/pollen/new.py").write_text("x", encoding="utf-8")

    assert binary_digest(root) != before


def test_moving_a_byte_from_one_file_to_the_next_changes_the_key(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    one, two = root / "packages/waggle/src/waggle/__init__.py", root / "uv.lock"
    one.write_text("ab", encoding="utf-8")
    two.write_text("c", encoding="utf-8")
    before = binary_digest(root)

    one.write_text("a", encoding="utf-8")
    two.write_text("bc", encoding="utf-8")

    assert binary_digest(root) != before  # Names and lengths are hashed, not just the bytes.


def test_the_key_is_sixteen_hex_digits_and_the_same_for_the_same_tree(tmp_path: Path) -> None:
    one, two = _tree(tmp_path / "one"), _tree(tmp_path / "two")

    assert binary_digest(one) == binary_digest(two)
    assert len(binary_digest(one)) == 16 and int(binary_digest(one), 16) >= 0


def test_the_build_container_gets_the_whole_workspace_though_the_key_hashes_only_the_frozen_part(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)

    staged = {path.as_posix() for path in stage_files(root)}
    keyed = {path.as_posix() for path in frozen_inputs(root)}

    assert "packages/hivemind/src/hivemind/__init__.py" in staged  # `uv sync` installs it.
    assert "packages/hivemind/src/hivemind/__init__.py" not in keyed
    assert keyed <= staged
    assert not any("/tests/" in path or "__pycache__" in path for path in staged)


def test_the_real_tree_has_every_file_the_key_names() -> None:
    inputs = frozen_inputs(REPO_ROOT)

    assert all((REPO_ROOT / path).is_file() for path in inputs)
    assert Path("packages/pollen/src/pollen/__main__.py") in inputs


def test_the_recipe_is_the_workflows() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "pollen.yml").read_text(encoding="utf-8")
    commands = [line for line in BUILD_SCRIPT.splitlines() if line.startswith(("pip ", "uv "))]

    assert len(commands) == 3  # Install uv, sync the locked environment, run the build script.
    assert all(command in workflow for command in commands)
    assert all(f"{name}={value}" in workflow for name, value in BUILD_ENVIRONMENT.items())
    assert f"BUILD_IMAGE: {BUILD_IMAGE}" in workflow


# ──────────────────────────────────────────────────────────────────────────────
# Reaching PyPI
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every variable the build's proxy handling reads."""
    for variable in PROXY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def test_a_proxy_on_the_host_loopback_gives_a_host_network_build_with_its_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_proxy: None
) -> None:
    bundle = tmp_path / "bundle.crt"
    bundle.write_bytes(BUNDLE)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:38973")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))

    access = build_network()

    assert access.network == "host"  # A container's own network cannot see the host's loopback.
    assert access.ca_bundle == bundle
    assert access.environment["HTTPS_PROXY"] == "http://127.0.0.1:38973"
    assert access.environment["https_proxy"] == "http://127.0.0.1:38973"
    # Every tool that does TLS in the build is pointed at the copied certificate.
    for variable in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT"):
        assert access.environment[variable] == CONTAINER_CA


def test_a_loopback_proxy_with_no_certificate_bundle_is_still_used_without_certificate_variables(
    monkeypatch: pytest.MonkeyPatch, no_proxy: None
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:3128")

    access = build_network()

    assert (access.network, access.ca_bundle) == ("host", None)
    assert set(access.environment) == {"HTTPS_PROXY", "https_proxy"}


@pytest.mark.parametrize(
    "proxy",
    ["", "http://proxy.example:3128", "http://user:secret@127.0.0.1:3128"],
    ids=["none", "off the host", "with credentials"],
)
def test_no_proxy_or_one_a_container_can_reach_or_one_that_needs_a_login_is_the_default_network(
    monkeypatch: pytest.MonkeyPatch, no_proxy: None, proxy: str
) -> None:
    if proxy:
        monkeypatch.setenv("HTTPS_PROXY", proxy)

    assert build_network() == BuildNetwork()


# ──────────────────────────────────────────────────────────────────────────────
# Building, and keeping what was built
# ──────────────────────────────────────────────────────────────────────────────


class BuildDocker:
    """Stands in for `docker`: records the calls, keeps what was copied in, makes the binary."""

    def __init__(self, build_exit: int = 0, output: str = "") -> None:
        """Answer the build's `exec` with `build_exit` and `output`; everything else succeeds."""
        self.calls: list[tuple[str, ...]] = []
        self.staged: set[str] = set()  # The files of the tree as it was when it was copied in.
        self._build_exit = build_exit
        self._output = output

    def __call__(self, *args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        """Record the call and do what a daemon would for the few commands that matter."""
        self.calls.append(args)
        stdout, exit_code = "", 0
        if args[0] == "cp" and args[2].endswith(":/src"):
            stage = Path(args[1].removesuffix("/."))  # The tree is only there during this call.
            self.staged = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
        elif args[0] == "cp" and args[1].endswith(f":/out/{BINARY_NAME}"):
            Path(args[2]).write_bytes(b"the binary")
        elif args[0] == "exec":
            stdout, exit_code = self._output, self._build_exit
        elif args[0] == "ps":
            stdout = "the-build-container\n"  # So the janitor has something to remove.
        return CommandResult(("docker", *args), exit_code, stdout, "")

    def verbs(self) -> list[str]:
        """The docker subcommand of each call, in order."""
        return [call[0] for call in self.calls]


@pytest.fixture
def building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_proxy: None
) -> tuple[BuildDocker, Path, Path]:
    """A recorder for docker, a miniature checkout and an empty cache for `build_binary`."""
    docker = BuildDocker()
    monkeypatch.setattr(build_module, "docker", docker)
    monkeypatch.setattr(container_module, "docker", docker)  # The janitor's own calls.
    monkeypatch.setenv("HIVEMIND_TEST_CACHE_DIR", str(tmp_path / "cache"))
    return docker, _tree(tmp_path / "checkout"), tmp_path / "cache" / "pollen-binary"


def test_a_first_build_runs_the_workflows_recipe_and_keeps_the_binary_under_its_key(
    building: tuple[BuildDocker, Path, Path],
) -> None:
    docker, root, cache = building

    binary = build_binary(root)

    assert binary == cache / binary_digest(root) / BINARY_NAME
    assert binary.read_bytes() == b"the binary" and binary.stat().st_mode & 0o777 == 0o755
    run, *_ = docker.calls
    assert run[:3] == ("run", "--detach", "--init")
    assert BUILD_IMAGE in run and run[-2:] == ("sleep", "infinity")
    assert [f"{n}={v}" for n, v in BUILD_ENVIRONMENT.items()] == _call_values(run, "--env")
    exec_call = next(call for call in docker.calls if call[0] == "exec")
    assert exec_call[2:] == ("bash", "-c", BUILD_SCRIPT)
    assert sorted(docker.staged) == sorted(p.as_posix() for p in stage_files(root))
    assert docker.verbs()[-3:] == ["ps", "rm", "network"]  # The janitor removed the container.
    assert [entry.name for entry in cache.iterdir()] == [binary_digest(root)]  # No partial left.


def test_a_second_build_of_the_same_inputs_finds_the_binary_and_starts_nothing(
    building: tuple[BuildDocker, Path, Path],
) -> None:
    docker, root, _ = building
    first = build_binary(root)
    calls = len(docker.calls)

    second = build_binary(root)

    assert second == first and len(docker.calls) == calls


def test_a_change_to_the_packet_builds_again_and_leaves_the_old_binary_where_it_was(
    building: tuple[BuildDocker, Path, Path],
) -> None:
    docker, root, cache = building
    first = build_binary(root)

    (root / "packages/pollen/src/pollen/__init__.py").write_text("new code", encoding="utf-8")
    second = build_binary(root)

    assert second != first and first.is_file() and second.is_file()
    assert len(list(cache.iterdir())) == 2
    assert docker.verbs().count("exec") == 2


def test_a_failed_build_raises_with_the_end_of_its_output_and_keeps_nothing(
    building: tuple[BuildDocker, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, root, cache = building
    failing = BuildDocker(build_exit=2, output="\n".join(f"line {i}" for i in range(100)))
    monkeypatch.setattr(build_module, "docker", failing)
    monkeypatch.setattr(container_module, "docker", failing)

    with pytest.raises(CommandError) as failure:
        build_binary(root)

    message = str(failure.value)
    assert "did not build (exit 2)" in message and "line 99" in message
    assert "line 0\n" not in message  # Only the end: the first lines are long gone.
    assert list(cache.iterdir()) == []  # Neither a binary nor a half-made directory.
    assert "rm" in failing.verbs()  # And the container is removed all the same.


def test_a_binary_that_could_not_be_copied_out_is_an_error_and_keeps_nothing(
    building: tuple[BuildDocker, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, root, cache = building

    def refuse(*args: str, timeout_s: float = 0.0, check: bool = True) -> CommandResult:
        if args[0] == "cp" and args[1].endswith(f":/out/{BINARY_NAME}"):
            raise CommandError("docker cp exited 1: no such file")
        return CommandResult(("docker", *args), 0, "the-build-container\n", "")

    monkeypatch.setattr(build_module, "docker", refuse)
    monkeypatch.setattr(container_module, "docker", refuse)

    with pytest.raises(CommandError, match="no such file"):
        build_binary(root)

    assert list(cache.iterdir()) == []
