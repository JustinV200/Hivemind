"""Re-export the fake Cell backend: an in-memory RealCellSource and CellSession for tests and demos.

`hivemind.cell.fake` is a `RealCellSource` (`hivemind.cell.source`) and `CellSession`
(`hivemind.cell.session`) implementation that never touches a real filesystem or starts a real
process: `FakeCellSource` hands out `RealCellLease`s over a fixed, in-memory Cell inventory, and
`FakeSession` answers `exec` from a scripted responder or mapping and stores `put_file`/`get_file`
data in a plain dict. Shipped code, not test-only (codingrules section 14.4: "fakes live in src/
beside their Protocol"), because `pollen`, `hive doctor` and demo paths use them too.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the cell package. Used by
    `tests/contracts/test_cell_session_contract.py` and `test_real_cell_source_contract.py`, by
    every other unit test that needs a Cell without a real device, and by demo scripts.

Key invariants:
    - Every name in `__all__` here is re-exported from `hivemind.cell.fake.session` or
      `hivemind.cell.fake.source`; this file holds no logic of its own (codingrules 5.4).

See Also:
    - .claude/codingrules.md section 14.4 for "fakes are shipped code."
    - hivemind.cell.source for RealCellSource, the Protocol FakeCellSource implements.
    - hivemind.cell.session for CellSession, the Protocol FakeSession implements.

Public API:
    - FakeSession, Responder: an in-memory CellSession (hivemind.cell.fake.session).
    - FakeCellSource, FakeLeaseReleaser: an in-memory RealCellSource (hivemind.cell.fake.source).
"""

from hivemind.cell.fake.session import FakeSession, Responder
from hivemind.cell.fake.source import FakeCellSource, FakeLeaseReleaser

__all__ = ["FakeCellSource", "FakeLeaseReleaser", "FakeSession", "Responder"]
