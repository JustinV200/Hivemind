"""Pin protocol 1.6's task needs: ExoskeletonNeed and the network scopes on TaskAssign.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.messages.task.needs against spec
    section 8.2: defaults, the audio-needs-a-desktop rule, and the bounds TaskAssign applies to
    its network scopes.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8.2 for ExoskeletonNeed and TaskAssign.network_scopes.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.messages.task import ExoskeletonNeed
from waggle.messages.task.needs import MAX_TASK_NETWORK_SCOPES, MAX_TASK_SCOPE_CHARS


def test_exoskeleton_need_defaults_to_a_full_desktop_without_audio() -> None:
    need = ExoskeletonNeed()

    assert not need.browser_only
    assert not need.audio


@pytest.mark.parametrize(("browser_only", "audio"), [(False, False), (True, False), (False, True)])
def test_exoskeleton_need_round_trips(browser_only: bool, audio: bool) -> None:
    need = ExoskeletonNeed(browser_only=browser_only, audio=audio)

    assert ExoskeletonNeed.model_validate_json(need.model_dump_json()) == need


def test_audio_is_refused_on_a_browser_only_need() -> None:
    # Audio runs through the desktop's sound server, which a browser-only attach never starts.
    with pytest.raises(ValidationError, match="browser_only"):
        ExoskeletonNeed(browser_only=True, audio=True)


def test_exoskeleton_need_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ExoskeletonNeed.model_validate({"browser_only": False, "display": True})


def test_the_scope_bounds_mirror_hivemind_task_needs() -> None:
    # hivemind.cell.needs keeps its own copy of both numbers; a drift would let a plan carry a
    # need the wire then refuses.
    assert MAX_TASK_NETWORK_SCOPES == 32
    assert MAX_TASK_SCOPE_CHARS == 253
