"""Roadmap step 4.6: assert no Waggle message model ever carries a full transcript field.

"A test asserts no full transcript ever appears in a Waggle message." Walks every registered
message class in `waggle.messages.registry.MESSAGE_SPECS` (the whole wire catalogue) and asserts
none has a field literally named `messages`, `history` or `transcript` -- the three names an
accumulated conversation would plausibly be smuggled under. This is a structural guarantee, not a
runtime one: `hivemind.supervision.telemetry`/`hivemind.memory.thresholds.capped_compact_view`
already bound every free-text field a bee reports (`ContextTelemetry`, `CompactView`), so this test
is what keeps a future message from reintroducing the one shape those bounds exist to rule out.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 4.6 for the no-transcript requirement this test proves.
    - waggle.messages.registry for MESSAGE_SPECS, the whole catalogue this test walks.
    - waggle.messages.supervision.telemetry for ContextTelemetry and CompactView, the two models
      most at risk of ever carrying one.
"""

from __future__ import annotations

from waggle.messages.registry import MESSAGE_SPECS

_FORBIDDEN_FIELD_NAMES = frozenset({"messages", "history", "transcript"})


def test_no_message_class_in_the_catalogue_has_a_transcript_shaped_field() -> None:
    offenders: list[str] = []
    for spec in MESSAGE_SPECS:
        forbidden = _FORBIDDEN_FIELD_NAMES & set(spec.model.model_fields)
        if forbidden:
            offenders.append(f"{spec.model.__name__} has {sorted(forbidden)}")
    assert not offenders, "\n".join(offenders)


def test_the_catalogue_is_not_accidentally_empty() -> None:
    # A vacuously-passing walk (an empty catalogue) would prove nothing; guard against that.
    assert len(MESSAGE_SPECS) > 20
