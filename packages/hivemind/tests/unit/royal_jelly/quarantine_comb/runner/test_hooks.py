"""Test the audit hook's attribution: when an event is the tool's and when it is the session's.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/hooks.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/hooks.py for the module under test.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from hivemind.royal_jelly.quarantine_comb.runner.hooks import (
    AuditPolicy,
    AuditRecorder,
    SandboxViolationError,
    build_policy,
    classify,
)

_STDLIB = "/usr/lib/python3.12"
_TOOL_FRAME = ("{pkg}/tool.py", 7)
_STDLIB_FRAME = (f"{_STDLIB}/subprocess.py", 1_900)
_HIVE_FRAME = ("/opt/venv/lib/python3.12/site-packages/hivemind/cell/local/process.py", 240)


def _policy(tmp_path: Path) -> AuditPolicy:
    """Build a policy rooted in a temp sandbox, with a fake stdlib root."""
    root = tmp_path.resolve()
    return AuditPolicy(
        package_dir=str(root / "pkg"),
        allowed_roots=(str(root), "/opt/venv"),
        stdlib_roots=(_STDLIB,),
    )


def _frames(policy: AuditPolicy, *frames: tuple[str, int]) -> list[tuple[str, int]]:
    """Fill `{pkg}` in the frame templates with the policy's package directory."""
    return [(name.format(pkg=policy.package_dir), line) for name, line in frames]


def test_a_process_started_by_the_tool_itself_is_its_violation(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    frames = _frames(policy, _STDLIB_FRAME, _TOOL_FRAME)

    hit = classify("subprocess.Popen", ("ls", ["ls", "-l"], None, None), frames, policy)

    assert hit is not None
    assert hit.kind == "process"
    assert "ls" in hit.detail
    assert hit.location == "tool.py:7"


def test_a_process_started_through_the_session_is_not_the_tools_violation(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    # The innermost frame outside the stdlib is hivemind's session code, not the tool's line.
    frames = _frames(policy, _STDLIB_FRAME, _HIVE_FRAME, _TOOL_FRAME)

    assert classify("subprocess.Popen", ("ls", ["ls"], None, None), frames, policy) is None


@pytest.mark.parametrize("event", ["os.system", "os.exec", "os.fork", "os.posix_spawn", "os.spawn"])
def test_every_process_event_is_forbidden_to_the_tool(tmp_path: Path, event: str) -> None:
    policy = _policy(tmp_path)

    hit = classify(event, ("x", ["x"]), _frames(policy, _TOOL_FRAME), policy)

    assert hit is not None
    assert hit.kind == "process"


def test_opening_a_file_outside_every_allowed_root_names_the_path(tmp_path: Path) -> None:
    policy = _policy(tmp_path)

    hit = classify("open", ("/etc/passwd", "r", 0), _frames(policy, _TOOL_FRAME), policy)

    assert hit is not None
    assert hit.kind == "open"
    assert "/etc/passwd" in hit.detail


@pytest.mark.parametrize(
    "path",
    ["{root}/scratch/notes.txt", "{root}/pkg/tool.py", "/opt/venv/lib/x.py", "/dev/null", 3, None],
)
def test_opening_inside_the_package_scratch_or_prefix_is_fine(tmp_path: Path, path: object) -> None:
    policy = _policy(tmp_path)
    resolved = path.format(root=tmp_path.resolve()) if isinstance(path, str) else path

    assert classify("open", (resolved, "r", 0), _frames(policy, _TOOL_FRAME), policy) is None


def test_a_relative_path_is_resolved_before_it_is_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _policy(tmp_path)
    (tmp_path / "scratch").mkdir()
    monkeypatch.chdir(tmp_path / "scratch")

    assert classify("open", ("notes.txt", "r", 0), _frames(policy, _TOOL_FRAME), policy) is None
    monkeypatch.chdir("/")
    assert classify("open", ("etc/hosts", "r", 0), _frames(policy, _TOOL_FRAME), policy) is not None


def test_a_file_opened_by_the_session_on_the_tools_behalf_is_not_the_tools_violation(
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path)
    frames = _frames(policy, _HIVE_FRAME, _TOOL_FRAME)

    assert classify("open", ("/etc/passwd", "r", 0), frames, policy) is None


def test_frozen_bootstrap_frames_are_looked_through(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    frames = [("<frozen importlib._bootstrap>", 1), *_frames(policy, _TOOL_FRAME)]

    assert classify("open", ("/etc/passwd", "r", 0), frames, policy) is not None


def test_asyncios_own_unix_socket_pair_is_not_network_use(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    frames = _frames(policy, _TOOL_FRAME)

    assert classify("socket.__new__", (object(), socket.AF_UNIX, 1, 0), frames, policy) is None
    assert classify("socket.__new__", (object(), socket.AF_INET, 1, 0), frames, policy) is not None
    assert classify("socket.connect", (object(), ("example.org", 80)), frames, policy) is not None
    assert classify("socket.getaddrinfo", ("example.org", 80), frames, policy) is not None


def test_a_thread_with_no_outside_frame_is_blamed_only_for_process_creation(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    only_stdlib = [_STDLIB_FRAME]

    spawn = classify("subprocess.Popen", ("x", ["x"]), only_stdlib, policy, in_thread=True)
    read = classify("open", ("/etc/passwd", "r", 0), only_stdlib, policy, in_thread=True)
    main_thread = classify("subprocess.Popen", ("x", ["x"]), only_stdlib, policy, in_thread=False)

    assert spawn is not None
    assert spawn.location == "a worker thread"
    assert read is None
    assert main_thread is None


@pytest.mark.parametrize(
    "event", ["import", "exec", "compile", "os.listdir-not-a-path", "sys.settrace"]
)
def test_events_the_hook_does_not_watch_are_ignored(tmp_path: Path, event: str) -> None:
    policy = _policy(tmp_path)

    assert classify(event, ("x",), _frames(policy, _TOOL_FRAME), policy) is None


def test_the_recorder_raises_and_records_when_the_caller_is_in_the_package() -> None:
    # This test file stands in for the tool: the recorder walks the real stack from here.
    here = Path(__file__).resolve().parent
    recorder = AuditRecorder(
        AuditPolicy(
            package_dir=str(here), allowed_roots=(str(here),), stdlib_roots=("/usr/lib/python3.12",)
        )
    )
    recorder.phase = "fuzz"

    with pytest.raises(SandboxViolationError):
        recorder("os.system", ("true",))

    assert len(recorder.violations) == 1
    assert recorder.violations[0].phase == "fuzz"
    assert recorder.violations[0].location.startswith("test_hooks.py:")


def test_the_recorder_stays_quiet_when_the_caller_is_outside_the_package(tmp_path: Path) -> None:
    recorder = AuditRecorder(_policy(tmp_path))

    recorder("os.system", ("true",))

    assert recorder.violations == []


def test_the_recorder_bounds_how_many_violations_it_keeps() -> None:
    here = Path(__file__).resolve().parent
    recorder = AuditRecorder(AuditPolicy(package_dir=str(here), allowed_roots=(), stdlib_roots=()))

    for _ in range(100):
        with pytest.raises(SandboxViolationError):
            recorder("os.system", ("true",))

    assert len(recorder.violations) == 64


def test_build_policy_allows_the_package_the_sandbox_and_the_interpreter(tmp_path: Path) -> None:
    import sys

    policy = build_policy(tmp_path / "pkg", tmp_path)

    assert policy.package_dir.endswith("pkg")
    assert str(tmp_path.resolve()) in policy.allowed_roots
    assert any(str(Path(sys.prefix).resolve()) == root for root in policy.allowed_roots)
    assert policy.stdlib_roots
