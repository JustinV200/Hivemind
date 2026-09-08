"""Unit tests for scripts/check_no_model_ids.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate). Sample violating content below is built by string
    concatenation rather than written as one literal: this test file is itself in scope for
    check_no_model_ids.py's own repo-wide scan (scripts/ is scanned, and only the checker module
    itself is path-exempt -- see its module docstring), so a literal "claude-3-opus" sitting in
    this file's own source would trip that scan. Concatenating it at runtime, before it is ever
    written to a tmp_path file outside the repository, sidesteps that without weakening what is
    actually being tested: the checker still sees the fully-formed pattern in the file it scans.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/check_no_model_ids.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] already grants S101/D103 to
# packages/*/tests/**; scripts/tests/ is not in that list (roadmap 0.3 was not authorised to add
# to it), so the same exemption is granted per-file here instead.

from pathlib import Path

import check_no_model_ids
import pytest


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write `content` to `tmp_path/name`, creating parent directories as needed."""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_check_no_model_ids_passes_on_a_clean_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/llm/slot.py", "SLOT = 'QUEEN'\n")

    exit_code = check_no_model_ids.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_model_ids_flags_a_model_id_literal_in_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # See the module docstring: built by concatenation so this test file's own source is clean.
    literal = "cla" + "ude-3-opus-20240229"
    source = f'MODEL_ID = "{literal}"\n'
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/llm/models.py", source)

    exit_code = check_no_model_ids.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"{sample}:1:" in out
    assert "cla" + "ude-" in out


def test_check_no_model_ids_flags_a_provider_url_fragment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "https://api." + "anthropic.com" + "/v1/messages"
    source = f'BASE_URL = "{url}"\n'
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/llm/models.py", source)

    exit_code = check_no_model_ids.main([str(sample)])

    assert exit_code == 1
    assert "anthropic.com" in capsys.readouterr().out


def test_check_no_model_ids_skips_a_docstring_naming_the_pattern(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # This is the exact scenario the module docstring names: an adapter documenting which local
    # model servers it speaks to must not fail the check just for mentioning one by name.
    literal = "lla" + "ma"
    source = (
        f'"""Talk to an OpenAI-compatible local server such as {literal}.cpp.\n\n'
        'More text.\n"""\n\nX = 1\n'
    )
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/llm/providers/local.py", source)

    exit_code = check_no_model_ids.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_model_ids_skips_a_comment_naming_the_pattern(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    literal = "gpt" + "-4"
    source = f"# talks to something like {literal} for comparison\nX = 1\n"
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/llm/providers/local.py", source)

    exit_code = check_no_model_ids.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_model_ids_exempts_the_manifest_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    literal = "cla" + "ude-3-opus-20240229"
    source = f'MODEL_ID = "{literal}"\n'
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/manifest/defaults.py", source)

    exit_code = check_no_model_ids.main([str(tmp_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert sample.exists()


def test_check_no_model_ids_exempts_docs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    literal = "cla" + "ude-3-opus-20240229"
    source = f'MODEL_ID = "{literal}"\n'
    _write(tmp_path, "docs/adr/0001-x.py", source)

    exit_code = check_no_model_ids.main([str(tmp_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_model_ids_does_not_scan_a_file_outside_its_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A file under packages/<pkg>/ but not under src/ or tests/ (e.g. a top-level pyproject helper)
    # is not in scope per the module docstring.
    literal = "cla" + "ude-3-opus-20240229"
    source = f'MODEL_ID = "{literal}"\n'
    _write(tmp_path, "packages/hivemind/setup_helper.py", source)

    exit_code = check_no_model_ids.main([str(tmp_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
