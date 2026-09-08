# hivemind.wardens.autopilot

The autopilot package is the Warden's Autopilot: deterministic fallback behaviour that never
awaits a model, so the Hive keeps working when every provider is down. Nothing under this
package may import hivemind.llm.
