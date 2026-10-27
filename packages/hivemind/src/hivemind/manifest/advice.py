"""Find what a valid Hive Manifest allows but an operator should hear about: load-time advice.

A manifest can pass every validator and still be a poor choice. Two cases are checked here. First,
the JUDGE and RIPENER slots resolve to the same model. The clearance judge (ADR-0038) is meant to be
an independent second reading of a deposit the Ripener read as less sensitive; asked by the same
model, it shares the Ripener's blind spots, so a lowering is not really checked. And a weak shared
model silently blocks lowering instead (phase 7 handoff item 2: a 3B judge proved useless). That
is the operator's choice to make, so it is advice, never a validation error: the composition root
logs it as a warning whenever it builds a judge that will actually run (`hive run`, `hive serve`
and every `hive honey` command that opens the store with its models).

Second, `[memory] budget_fraction` past the compact line (`DEFAULT_COMPACT_AT`) or the
`handoff_threshold`: a fresh episode assembled to its budget starts already over the line, so its
bee is compacted (or handed off) before it has done anything. The executor no longer loops on it,
but nothing else says why every episode compacts at once; the composition root logs this one when
it builds the Queen's memory budget.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by
    `hivemind.cli.compose.honey.build_honey_access` once it has built a judge, and by
    `hivemind.cli.compose.deps.queen` for the memory budget. Calls into
    `hivemind.manifest.schema` and `hivemind.forage` (ModelSlot) only.

Key invariants:
    - Pure: reads the `LlmSection` it is given and returns text; never raises for a manifest
      that validated, never refuses one.
    - Two bindings are "the same model" when they name the same model id on the same endpoint:
      the same provider, or two providers of one kind at one base URL. Fallback chains are not
      followed; the slots' own bindings are what each call tries first.

See Also:
    - docs/adr/0038-honey-label-lowering-is-a-judge-reviewed-proposal.md for the judge.
    - .claude/phase-7-handoff.md section 8 item 2 for why the judge warning exists.
    - hivemind.memory.thresholds for the compact and handoff lines.
"""

from __future__ import annotations

from hivemind.forage import ModelSlot
from hivemind.manifest.schema import LlmSection, MemorySection, SlotBinding
from hivemind.manifest.schema.supervision import DEFAULT_COMPACT_AT

__all__ = ["budget_over_compact_line", "judge_shares_ripener"]


def budget_over_compact_line(
    memory: MemorySection, compact_at: float = DEFAULT_COMPACT_AT
) -> str | None:
    """Return a warning when `budget_fraction` exceeds the compact line or handoff threshold.

    Args:
        memory: A validated `[memory]` section.
        compact_at: The compact line the Queen and Warden order COMPACT past.

    Returns:
        One operator-facing sentence naming the line crossed and the fix, or None when a fresh
        episode's assembled content stays under both lines.
    """
    # The lower line is the one a fresh episode crosses first.
    line, name = min((compact_at, "compact line"), (memory.handoff_threshold, "handoff_threshold"))
    if memory.budget_fraction <= line:
        return None
    return (
        f"[memory] budget_fraction ({memory.budget_fraction}) exceeds the {name} ({line}): an "
        "episode assembled to its budget starts already past it, so its bee is compacted or "
        f"handed off before it does any work. Set budget_fraction to {line} or less."
    )


def judge_shares_ripener(llm: LlmSection) -> str | None:
    """Return a warning when the JUDGE and RIPENER slots resolve to the same model, else None.

    Args:
        llm: A validated `[llm]` section; every ModelSlot has a binding in it.

    Returns:
        One operator-facing sentence naming the shared model and why the judge should differ, or
        None when the two slots resolve to different models.
    """
    judge = llm.slots.get(ModelSlot.JUDGE.manifest_key)
    ripener = llm.slots.get(ModelSlot.RIPENER.manifest_key)
    # A validated section binds both; a missing one has nothing to share.
    if judge is None or ripener is None or _endpoint(llm, judge) != _endpoint(llm, ripener):
        return None
    return (
        f"[llm.slots] binds JUDGE and RIPENER to the same model ({judge.model!r}): the clearance "
        "judge then re-reads the Ripener's own reading with the same blind spots, so a label "
        "lowering is not independently checked, and a weak model blocks lowering outright. Bind "
        "JUDGE to a different, stronger model (ADR-0038)."
    )


def _endpoint(llm: LlmSection, binding: SlotBinding) -> tuple[str, str, str]:
    """Return what identifies the model a binding reaches: provider kind, base URL and model id."""
    spec = llm.providers[binding.provider]
    # Two provider names for one server and model are still one model.
    return spec.kind, spec.base_url, binding.model
