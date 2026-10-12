# hivemind.royal_jelly.promotion

The promotion flow walks one requested tool to PROMOTED or REJECTED: scaffold, ask a sandbox runner
for the Quarantine Comb's report, promote through the Comb Registry on a pass, otherwise retry once
with the failing report and then reject. Every stage writes a `tool.*` trail event, and every state
edge goes through `royal_jelly.tool_state.transition`.

- `flow.py`: `PromotionFlow`, `PromotionPorts`, `PromotionOutcome`.
- `ports.py`: `SandboxRunner`, `HoneyDeposit`, `Announcer`, the seams the composition root fills.
- `events.py`: `tool_event` and one builder per stage (also used by the Worker's tool invoker).

## How to test this

`uv run --frozen pytest packages/hivemind/tests/unit/royal_jelly/promotion`
