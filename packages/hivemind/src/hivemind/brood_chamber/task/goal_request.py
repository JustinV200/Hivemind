"""Define GoalRequestId and the request terms every task of a requested goal carries.

A goal the Hive Entrance accepts is written first as a durable goal request in the Queen's own
tables (docs/adr/0040, "A goal is durable before it is acknowledged") and only then planned. Every
task planned from it carries two facts of that request. Its id, `goal_request_id`: the Queen finds
a goal she already planned by it after a crash, so a request is never planned twice, and Night
Veil placement cites it as the human request that asked for the tier (docs/adr/0039). Its budget,
`spend_cap_usd`: the goal's own spend cap, which only ever lowers the manifest's per-goal cap.
`GoalRequestId` lives here, not in `hivemind.queen.intake` (the table that owns requests), because
the Brood Chamber sits four layers below the Queen and must validate the reference without
importing her; waggle's closed `IdKind` set has no kind for it, so it is a hivemind-local prefixed
ULID, minted the way `hivemind.memory.cell_wax.new_wax_id` mints a wax id.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber.task`.
    Read by `hivemind.brood_chamber.task.model` (TaskSpec, TaskDraft) and `hivemind.brood_chamber.
    store` (TaskFilter); minted by `hivemind.queen.intake` when a goal request is received. Calls
    into waggle (the clock and the ULID encoder) only.

Key invariants:
    - A goal request id is always `goalreq_` plus a 26-character Crockford ULID
      (`GOAL_REQUEST_ID_PATTERN`), so it sorts by creation time like every other Hive id.
    - `None` is meaningful for both terms (codingrules section 9): a goal the operator submitted
      locally (`hive run`, `hive tasks submit`) has no request row and no budget of its own.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the goal request.
    - hivemind.queen.intake for GoalRequest, the row these ids name.
    - hivemind.brood_chamber.task.goal_set for GoalCapabilities, the goal's other ceiling.
"""

from __future__ import annotations

import secrets
from typing import Annotated, NewType

from pydantic import Field

from waggle.clock import Clock
from waggle.ulid import RANDOMNESS_BYTES, encode_ulid

GOAL_REQUEST_ID_PREFIX = "goalreq_"  # No waggle IdKind names a goal request; see the docstring.
GOAL_REQUEST_ID_PATTERN = r"^goalreq_[0-9A-HJKMNP-TV-Z]{26}$"  # The prefix plus a Crockford ULID.

__all__ = [
    "GOAL_REQUEST_ID_PATTERN",
    "GOAL_REQUEST_ID_PREFIX",
    "GoalRequestId",
    "GoalRequestRef",
    "GoalSpendCap",
    "new_goal_request_id",
]

GoalRequestId = NewType("GoalRequestId", str)


def new_goal_request_id(clock: Clock) -> GoalRequestId:
    """Mint a fresh `goalreq_`-prefixed ULID, timestamped by `clock`.

    Mirrors `waggle.ids.new_id` in miniature (module docstring): waggle has no `IdKind` for a
    goal request, and a request is a Hive-local row that never crosses Waggle.

    Args:
        clock: Injected clock so the id's timestamp is deterministic in tests.

    Returns:
        A `"goalreq_<26-char ULID>"` string matching `GOAL_REQUEST_ID_PATTERN`.
    """
    # Milliseconds, not seconds: waggle.ulid's encoding assumes millisecond resolution.
    timestamp_ms = int(clock.now().timestamp() * 1000)
    ulid = encode_ulid(timestamp_ms, secrets.token_bytes(RANDOMNESS_BYTES))
    return GoalRequestId(f"{GOAL_REQUEST_ID_PREFIX}{ulid}")


# The optional reference a task stores, validated by pattern so a malformed id never reaches the
# task's JSON body; its description rides in the type so TaskSpec and TaskDraft share one text.
GoalRequestRef = Annotated[
    GoalRequestId | None,
    Field(
        pattern=GOAL_REQUEST_ID_PATTERN,
        description="The goal request this task was planned from (roadmap step 10.5, ADR-0040); "
        "None for a goal the operator submitted locally, which has no request row.",
    ),
]

# The goal's own spend cap in US dollars, from its request's budget. Never negative; the Queen
# applies min(this, [forage] spend_cap_per_goal_usd), so it can only ever lower the manifest's cap.
GoalSpendCap = Annotated[
    float | None,
    Field(
        ge=0,
        description="The goal's own spend cap in US dollars, from its request's budget; it only "
        "ever lowers [forage] spend_cap_per_goal_usd. None means the manifest's cap alone.",
    ),
]
