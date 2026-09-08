# waggle.messages

The messages package is the Waggle message catalogue: every payload that can travel inside an
Envelope (the outer wrapper every Waggle message carries), as a frozen pydantic model that
forbids unknown keys and describes every field. `docs/waggle/spec.md` section 8 is the source of
truth; `registry.py` is the only place in code where the list of kinds lives, and
`tests/test_spec_drift.py` fails CI when the two disagree in either direction.

## Layout (roadmap step 1.3)

- `base.py`: `WaggleMessage`, the root every payload subclasses; `MessageShape` (request, reply,
  event); the shared bounds (`MAX_REASON_CHARS`, `MAX_CHUNK_BYTES`, `MAX_PATH_CHARS`,
  `MAX_SUB_BEES_ON_WIRE`, ...); `UtcDatetime`; and one `Annotated` id alias per `IdKind`
  (`TaskIdField`, `CellIdField`, ...) plus `id_validator(*kinds)` for union id fields, so a family
  declares an alias and writes no validator.
- `labels.py` and `reports.py`: the closed sets and value models that ride on messages of more
  than one family (`HoneyClearance`, `AccessLevel`, `CombShieldLevel`, `Urgency`, `Tempo`,
  `Postcondition`, `PlatformReport`, `HostCapacityReport`, ...), so no family file imports another.
- One package per family, `messages/<family>/`, split by responsibility into modules, with an
  `__init__.py` that re-exports the family's messages, enums and value models so a caller writes
  `from waggle.messages.forage import SourceRef` without knowing the split (codingrules section
  3): `task/` (`assignment.py`, `reports.py`); `supervision/` (`oversight.py`, `telemetry.py`,
  `alarms.py`, `questions.py`); `forage/` (`grants.py`, `values.py`, `capacity.py`,
  `hosting.py`); `cell/` (`status.py`, `leases.py`, `wax.py`); `session/` (`commands.py`,
  `output.py`, `files.py`); `honey/` (`exchange.py`, `hit.py`); `tool/` (`authoring.py`,
  `call.py`, `json_text.py`); `capping/` (`proposals.py`, `action.py`, `verdict.py`); `swarm/`
  (`enrolment.py`, `colonized.py`); `control/` (`protocol.py`, `hive.py`). Enums that only one
  family uses live in that family's package; every bound stays in the module that names it.
- `registry.py`: `MessageSpec` (kind, model, shape, replies_to), `MESSAGE_SPECS` for all
  sixty-six kinds in the spec's order, and the lookups `spec_for`, `model_for`, `kind_for`,
  `all_kinds`. Message classes never carry their own kind.

Rules every family follows: every id field uses a base.py alias; every `reason` is bounded by
`MAX_REASON_CHARS`; every closed set is an `Enum`; every large text field has a named bound;
bytes travel as base64 chunks of at most `MAX_CHUNK_BYTES` with `offset` and `final`; a rule the
spec marks "(validator)" is a pydantic validator, and a rule marked "(receiver rule)" is left to
the receiver and only mentioned in the field description.

## How to test this

```bash
uv run --frozen pytest packages/waggle/tests/messages packages/waggle/tests/test_spec_drift.py
```

The test tree mirrors the packages (`tests/messages/<family>/test_<module>.py`). Each family's
root test module carries an `EXAMPLES` tuple with one valid instance of every class; the registry
tests load all ten by file path and round-trip every example through `wrap`, the `Codec` and
back, so a class that cannot survive the wire never reaches the registry.
