# hivemind.royal_jelly.scaffold

The scaffolder turns a bee's request for a missing capability into a candidate tool: a model on the
SCAFFOLDER slot writes the tests, the source and the declarations, and the Hive validates the spec
(`validate_spec` against the requester's own capabilities), packs the three files and names the
package by its SHA-256. A candidate runs nowhere until the Quarantine Comb has proved it.

- `models.py`: `ToolRequestBrief` (the Hive's facts plus the requester's words, and on the one
  retry the failing report), `ScaffoldReply` (what the model fills), `ScaffoldResult`.
- `scaffolder.py`: the `Scaffolder` protocol and `ModelScaffolder`.
- `render.py`: the brief as the prompt's event section (`llm/prompts/scaffold_tool.md` is the prompt).
- `fake.py`: `FakeScaffolder`, scripted results for tests.

## How to test this

`uv run --frozen pytest packages/hivemind/tests/unit/royal_jelly/scaffold`
