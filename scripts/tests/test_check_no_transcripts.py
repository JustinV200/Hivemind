"""Unit tests for scripts/check_no_transcripts.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate). Exercises the AST rules and the allowlist directly.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/check_no_transcripts.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] already grants S101/D103 to
# packages/*/tests/**; scripts/tests/ is not in that list (roadmap 0.3 was not authorised to add
# to it), so the same exemption is granted per-file here instead.

from pathlib import Path

import check_no_transcripts
import pytest


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write `content` to `tmp_path/name`, creating parent directories as needed."""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_check_no_transcripts_passes_on_a_local_variable_named_messages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A local variable that lives only for the duration of one call is not module or class state.
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/queen/planner/build.py",
        "def build():\n    messages = []\n    return messages\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_transcripts_flags_a_module_level_messages_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/queen/planner/build.py",
        "messages = []\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"{sample}:1:" in out
    assert "messages" in out


def test_check_no_transcripts_flags_a_self_history_attribute_assignment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "class Warden:\n    def __init__(self):\n        self.history = []\n"
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/wardens/state.py", source)

    exit_code = check_no_transcripts.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "'.history'" in out


def test_check_no_transcripts_flags_appending_to_self_messages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "class Warden:\n    def handle(self, item):\n        self.messages.append(item)\n"
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/wardens/state.py", source)

    exit_code = check_no_transcripts.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "grows a transcript" in out


def test_check_no_transcripts_flags_augmented_assignment_on_a_module_level_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "history = []\n\n\ndef record(event):\n    history += [event]\n"
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/wardens/state.py", source)

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 1


def test_check_no_transcripts_exempts_the_memory_subsystem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/memory/hot_state.py",
        "class HotState:\n    def __init__(self):\n        self.history = []\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_transcripts_exempts_llm_models_py(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/llm/models.py",
        "class LLMRequest:\n    def __init__(self):\n        self.messages = []\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_transcripts_exempts_llm_tools_py(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/llm/tools.py",
        "class ToolLoop:\n    def __init__(self):\n        self.messages = []\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_transcripts_exempts_the_waggle_messages_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/waggle/src/waggle/messages/task.py",
        "history = []\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_transcripts_exempts_the_mirrored_test_for_llm_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/tests/unit/llm/test_models.py",
        "class FakeRequest:\n    def __init__(self):\n        self.messages = []\n",
    )

    exit_code = check_no_transcripts.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
