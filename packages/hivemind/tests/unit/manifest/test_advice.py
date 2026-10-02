"""Tests for hivemind.manifest.advice: the JUDGE-shares-RIPENER warning on real manifests.

Fits into the Hive:
    Mirrors src/hivemind/manifest/advice.py (codingrules section 3). Builds real `LlmSection`
    values, and loads the shipped `local.toml`, which binds every slot to one model.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.advice for the module under test.
    - .claude/phase-7-handoff.md section 8 item 2 for why the warning exists.
"""

from __future__ import annotations

from pathlib import Path

from structlog.testing import capture_logs

from hivemind.forage import ModelSlot
from hivemind.manifest import judge_shares_ripener, load_manifest
from hivemind.manifest.schema import LlmSection

_MANIFESTS_DIR = Path(__file__).resolve().parents[5] / "docs" / "manifests"
_LOCAL = {"kind": "openai_compat", "base_url": "http://127.0.0.1:1234/v1"}


def _llm(judge: dict[str, str], **providers: dict[str, str]) -> LlmSection:
    """Every slot on `local`'s `test-model`, except JUDGE, bound as `judge` says."""
    slots: dict[str, object] = {
        slot.manifest_key: {"provider": "local", "model": "test-model"} for slot in ModelSlot
    }
    slots[ModelSlot.JUDGE.manifest_key] = judge
    return LlmSection(providers={"local": _LOCAL, **providers}, slots=slots)


def test_a_judge_on_the_ripeners_own_model_gets_advice_naming_it() -> None:
    advice = judge_shares_ripener(_llm({"provider": "local", "model": "test-model"}))

    assert advice is not None
    assert "JUDGE and RIPENER" in advice and "'test-model'" in advice


def test_a_second_provider_name_for_the_same_server_and_model_is_still_the_same_model() -> None:
    llm = _llm({"provider": "alias", "model": "test-model"}, alias=_LOCAL)

    assert judge_shares_ripener(llm) is not None


def test_a_judge_on_another_model_or_another_server_gets_no_advice() -> None:
    other_server = {"kind": "openai_compat", "base_url": "http://127.0.0.1:8080/v1"}

    other_model = _llm({"provider": "local", "model": "bigger-model"})
    elsewhere = _llm({"provider": "far", "model": "test-model"}, far=other_server)

    assert judge_shares_ripener(other_model) is None
    assert judge_shares_ripener(elsewhere) is None


def test_the_shipped_local_manifest_gets_the_advice_and_the_minimal_one_does_not() -> None:
    local = load_manifest(_MANIFESTS_DIR / "local.toml")
    minimal = load_manifest(_MANIFESTS_DIR / "minimal.toml")

    assert judge_shares_ripener(local.llm) is not None  # One model behind every slot.
    assert judge_shares_ripener(minimal.llm) is None


def test_loading_a_manifest_stays_silent_about_it() -> None:
    """Advice is logged where a judge is built, never on every load (every CLI command loads)."""
    with capture_logs() as logs:
        load_manifest(_MANIFESTS_DIR / "local.toml")

    assert not [entry for entry in logs if entry["event"] == "manifest.judge_shares_ripener"]
