"""Re-export waggle's ContextTelemetry and add pure helpers a supervisor scores and logs it with.

`ContextTelemetry` (tokens used against the window, the current goal, recent actions, blockers and
spend) is what every bee reports on heartbeat, bounded so it can never smuggle a transcript up the
tree. It is a value model with no behaviour, so codingrules section 6.1 has it imported from
waggle directly rather than mirrored, exactly like `HandoffRef` and `Postcondition` elsewhere. The
three functions here are the small amount of behaviour a supervisor actually needs on top of it:
how full a bee's context is, whether that has crossed a threshold worth acting on, and a one-line,
secret-free rendering for a log line or a trail event's detail.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.supervision.attendant`
    (context fullness feeds a WATCH_OBSERVATION/ALARM item's scoring) and by whichever `Supervisor`
    implementation decides when a bee needs a checkpoint or a compact. Calls into waggle only.

Key invariants:
    - fraction_used never raises for a well-formed ContextTelemetry: context_window is validated
      greater than zero at the wire boundary (waggle.messages.supervision.telemetry), so division
      here is always safe.
    - summarise never includes last_actions or blockers text verbatim, only counts: those fields
      can describe operator or Real Cell detail, and codingrules section 12 forbids logging page
      contents or secrets; a count is a safe summary, the text itself is not.

See Also:
    - .claude/codingrules.md section 8.8 for "every bee reports ContextTelemetry on heartbeat".
    - .claude/codingrules.md section 12 for the "never log secrets or page contents" rule
      summarise follows.
    - waggle.messages.supervision for ContextTelemetry itself and its field bounds.
"""

from __future__ import annotations

from waggle.messages.supervision import ContextTelemetry

__all__ = ["ContextTelemetry", "fraction_used", "is_past_threshold", "summarise"]


def fraction_used(telemetry: ContextTelemetry) -> float:
    """Return how full a bee's context window is, as a fraction of 1.0.

    Args:
        telemetry: The bee's reported telemetry.

    Returns:
        `tokens_used / context_window`. Ordinarily in `[0, 1]`, but a caller that reports more
        tokens used than its own window (a bee's own accounting bug) can push this above 1.0;
        this function does not clamp, so a caller that needs a fraction can see that anomaly.
    """
    return telemetry.tokens_used / telemetry.context_window


def is_past_threshold(telemetry: ContextTelemetry, threshold: float) -> bool:
    """Return whether a bee's context fullness has reached or passed `threshold`.

    Used by memory's handoff logic (roadmap step 3.14) and by a Warden's autopilot to decide when
    to checkpoint a sub-bee before it runs out of room.

    Args:
        telemetry: The bee's reported telemetry.
        threshold: The fraction (e.g. the manifest's `[memory] handoff_threshold`) to compare
            against.

    Returns:
        True if `fraction_used(telemetry) >= threshold`.
    """
    return fraction_used(telemetry) >= threshold


def summarise(telemetry: ContextTelemetry) -> str:
    """Render one secret-free line of `telemetry`, safe for a log or a trail event's detail.

    Args:
        telemetry: The bee's reported telemetry.

    Returns:
        A single line naming the goal, the token fraction used, and how many blockers are open;
        never the blockers' or last actions' own text (see the module docstring).
    """
    fraction = fraction_used(telemetry)
    return (
        f"goal={telemetry.goal!r} tokens={telemetry.tokens_used}/{telemetry.context_window} "
        f"({fraction:.0%}) blockers={len(telemetry.blockers)} spend={telemetry.spend:.2f}"
    )
