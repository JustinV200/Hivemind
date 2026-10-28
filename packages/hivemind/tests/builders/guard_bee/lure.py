"""Script a Drone into the Guard Bee's injection correlation: lured by outside text, then refused.

The end-to-end tests of the Guard Bee's chain drive one real Drone through four tool calls: a real
command lands outside text carrying seed payloads from the shipped pattern file in its scratch
(`shipped_seeds`, read from the loaded families' `examples`, so a pattern change is a visible diff
the tests pick up), as a download would (`fetch`); reading it back is where the scanner looks,
before the model sees a word (`read`, flagged as `guard.injection_suspected`); steered, it reaches
for a host it holds no `net` capability for (`reach`, refused as `guard.denied`); and it then starts
a long command (`linger`), so it is still at work, on a still-attached Cell, when the Queen acts.
`lure_call` gives those calls one round at a time, for a Hive Stand Drone scripted by its tool
results; `lure_script` gives the same calls as the fixed response queue a Virtual Cell's container
is scripted with.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`
    by tests.e2e.test_guard_bee_on_hive_stand and tests.e2e.test_guard_bee_on_virtual_cell.

Key invariants:
    - The injected text fires the scanner's drop threshold at its baseline tier.
"""

from __future__ import annotations

import sys

from hivemind.guard.scanner.patterns import load_scan_patterns
from hivemind.llm import LLMResponse, ToolCall, text_response, tool_call_response

__all__ = ["INJECTED", "LINGER_S", "LURE_CALLS", "lure_call", "lure_script", "shipped_seeds"]

LINGER_S = 60  # The last command outlives any scenario: only the Queen's lever ends it.
NOTES = "notes.txt"  # Where the fetched text lands, in the Drone's scratch.
UNHELD_URL = "https://example.org/"  # No Drone holds net:example.org; refused before any lookup.


def shipped_seeds(*families: str) -> tuple[str, ...]:
    """Every `examples` seed of the named families in the shipped pattern file, in file order.

    Raises:
        KeyError: A named family is not in the shipped file (renamed or dropped).
    """
    loaded = {family.name: family for family in load_scan_patterns().families}
    return tuple(seed for name in families for seed in loaded[name].examples)


# The shipped imperatives and role overrides, one per line (`^`-anchored patterns fire): past the
# baseline tier's drop threshold, so the model is shown a notice in their place.
INJECTED = "\n".join(shipped_seeds("imperative", "role_override"))
_FETCH = f"open({NOTES!r}, 'w').write({INJECTED!r})"
_LINGER = f"import time; time.sleep({LINGER_S})"
# The four calls, in order: (call id, tool name, arguments).
LURE_CALLS: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("fetch", "run_command", {"argv": [sys.executable, "-c", _FETCH]}),
    ("read", "read_file", {"path": NOTES}),
    ("reach", "http_request", {"method": "GET", "url": UNHELD_URL}),
    ("linger", "run_command", {"argv": [sys.executable, "-c", _LINGER]}),
)


def lure_call(round_count: int) -> tuple[str, str, dict[str, object]] | None:
    """Return the lure's call for a Drone's round `round_count`, or None once it is past them."""
    return LURE_CALLS[round_count] if round_count < len(LURE_CALLS) else None


def lure_script() -> tuple[LLMResponse, ...]:
    """The lure as a Virtual Cell container's fixed response queue: the four calls, then done."""
    calls = tuple(
        tool_call_response(ToolCall(id=call_id, name=name, arguments=arguments))
        for call_id, name, arguments in LURE_CALLS
    )
    return (*calls, text_response("Never reached: the Queen's lever ends this attempt."))
