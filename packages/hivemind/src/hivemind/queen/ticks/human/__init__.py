"""Provide the human end of the Queen's tick: her chat and her durable goal requests.

Roadmap step 10.5 (ADR-0040) made the Queen the human end of the Hive Entrance, with two tick
handlers: `chat` drains the human's waiting messages into her inbox and carries a `REPLY`'s words
out, stamping each message handled once decided, and `intake` settles, holds or plans every
durable goal request, one plan at a time beside the tick. They share this sub-package because
together with phase 7's `honey` tick handler they took `hivemind.queen.ticks` past codingrules
5.6's ten modules, and both answer the human rather than a Warden.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.ticks`.
    Called by `hivemind.queen.queen.Queen`'s own tick, as `ticks.human.chat` and
    `ticks.human.intake`. Calls into this package's `chat` and `intake` only.

Key invariants:
    - This file holds re-exports and __all__ only (codingrules section 5.4).

See Also:
    - docs/adr/0040 for the chat and the durable goal requests these modules drain.
    - hivemind.queen.ticks for the rest of the Queen's tick handlers.

Public API (roadmap step 10.5):
    - chat, intake: the chat and the goal-request tick-handler modules.
"""

from hivemind.queen.ticks.human import chat, intake

__all__ = ["chat", "intake"]
